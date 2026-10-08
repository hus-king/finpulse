"""沪深指数涨跌家数温度计；分数描述市场广度，不预测价格反转。"""
import asyncio
import math
import time
from datetime import datetime

import httpx

from .news_cleaning import SHANGHAI

EASTMONEY_BREADTH_URLS = [
    f'https://{host}/api/qt/ulist.np/get?fltt=2&secids=1.000001,0.399001&fields=f1,f2,f3,f4,f6,f12,f13,f104,f105,f106,f124'
    for host in ('push2delay.eastmoney.com', 'push2.eastmoney.com')
]


def parse_breadth(rows):
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError('Incomplete market breadth')
    identities = {(str(row.get('f12')), str(row.get('f13'))) for row in rows}
    if identities != {('000001', '1'), ('399001', '0')}:
        raise ValueError('Unexpected index identities')
    totals = {'advancing': 0, 'declining': 0, 'flat': 0, 'turnover_cny': 0.0}
    for row in rows:
        for key, field in [('advancing', 'f104'), ('declining', 'f105'), ('flat', 'f106'), ('turnover_cny', 'f6')]:
            try:
                value = float(row[field])
            except (KeyError, TypeError, ValueError):
                raise ValueError('Missing or invalid breadth field') from None
            if isinstance(row[field], bool) or not math.isfinite(value) or value < 0 or (key != 'turnover_cny' and not value.is_integer()):
                raise ValueError('Invalid breadth value')
            totals[key] += value if key == 'turnover_cny' else int(value)
        if sum(float(row[field]) for field in ('f104', 'f105', 'f106')) <= 0:
            raise ValueError('Empty index breadth')
    totals['total'] = totals['advancing'] + totals['declining'] + totals['flat']
    totals['turnover_cny'] = round(totals['turnover_cny'], 2)
    # Only label a market timestamp when both sources supply it. Fetch time is separate.
    timestamps = [row.get('f124') for row in rows]
    try:
        stamp = min(float(value) for value in timestamps)
        totals['market_as_of'] = datetime.fromtimestamp(stamp, SHANGHAI).isoformat() if stamp > 0 else None
    except (TypeError, ValueError, OverflowError, OSError):
        totals['market_as_of'] = None
    return totals


def calculate_level(score):
    if score >= 75:
        return 'extreme_greed', '强势 / 上涨集中', '上涨家数占比很高，当前样本呈现较强市场广度'
    if score >= 55:
        return 'greed', '偏强 / 乐观', '上涨家数多于下跌家数'
    if score >= 45:
        return 'neutral', '中性 / 均衡', '上涨与下跌家数接近'
    if score >= 25:
        return 'fear', '偏弱 / 谨慎', '下跌家数多于上涨家数'
    return 'extreme_fear', '弱势 / 下跌集中', '下跌家数占比很高，当前样本呈现较弱市场广度'


class MarketSentimentService:
    def __init__(self, cache_ttl=90, clock=time.monotonic):
        self.cache_ttl = cache_ttl
        self.clock = clock
        self._cached_data = None
        self._cached_time = float('-inf')
        self._last_attempt = float('-inf')
        self._last_result = None
        self._lock = asyncio.Lock()

    def snapshot(self):
        """Nonblocking snapshot for the chart bundle; the card fetches independently."""
        if not self._last_result:
            return None
        if self._last_result['status'] == 'ok' and self.clock() - self._cached_time >= self.cache_ttl:
            return {**self._last_result, 'status': 'stale'}
        return self._last_result

    async def _fetch(self):
        async with httpx.AsyncClient(timeout=httpx.Timeout(6, connect=3), follow_redirects=True,
                                     headers={'User-Agent': 'Mozilla/5.0'}) as client:
            for url in EASTMONEY_BREADTH_URLS:
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    return parse_breadth(response.json()['data']['diff'])
                except (httpx.HTTPError, KeyError, TypeError, ValueError):
                    continue
        raise ValueError('Market breadth sources unavailable')

    async def get_sentiment(self, force=False, force_refresh=False):
        force = force or force_refresh
        async with self._lock:
            now = self.clock()
            # Bound manual refreshes too. Failed results back off for 30 seconds.
            interval = 30 if self._last_result and self._last_result['status'] != 'ok' else 10 if force else self.cache_ttl
            if self._last_result and now - self._last_attempt < interval:
                return self._last_result
            try:
                breadth = await self._fetch()
                score = round((breadth['advancing'] + .5 * breadth['flat']) / breadth['total'] * 100)
                level, name, summary = calculate_level(score)
                result = {**breadth, 'status': 'ok', 'score': score, 'level': level, 'level_name': name,
                          'summary': summary, 'updated_at': datetime.now(SHANGHAI).isoformat(),
                          'source': '东方财富 · 上证指数与深证成指涨跌家数',
                          'method': '(上涨家数 + 0.5 × 平盘家数) / 总家数 × 100；市场广度情绪代理指标'}
                self._cached_data, self._cached_time = result, self.clock()
            except Exception:
                result = {**self._cached_data, 'status': 'stale'} if self._cached_data else {
                    'status': 'unavailable', 'score': None, 'level': None, 'level_name': '暂不可用',
                    'summary': '市场广度来源暂不可用，请稍后刷新', 'advancing': 0, 'declining': 0,
                    'flat': 0, 'total': 0, 'turnover_cny': 0.0, 'updated_at': None, 'market_as_of': None}
            self._last_attempt, self._last_result = self.clock(), result
            return result
