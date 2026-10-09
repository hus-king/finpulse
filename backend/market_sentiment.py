"""沪深指数涨跌家数温度计；分数描述市场广度，不预测价格反转。"""
import asyncio
import math
import re
import time
from datetime import datetime

import httpx

from .news_cleaning import SHANGHAI
from .sentiment_history import SentimentHistoryService

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
    totals['source'] = '东方财富 · 上证指数与深证成指涨跌家数'
    return totals


def parse_legu_breadth(html_text: str):
    """从乐咕乐股网 HTML 抽取涨跌平盘分布与统计时间。"""
    items = dict(re.findall(r'<td>(平盘|上涨|下跌|停牌)</td>\s*<td[^>]*>(\d+)</td>', html_text))
    if '上涨' not in items or '下跌' not in items:
        meta_m = re.search(r'其中(?:\d+家涨停[，,])?(?:\d+家跌停[，,])?(\d+)家上涨[，,](\d+)家下跌', html_text)
        if meta_m:
            items['上涨'], items['下跌'] = meta_m.groups()
    if '上涨' not in items or '下跌' not in items:
        raise ValueError('Invalid Legu HTML content')

    advancing = int(items['上涨'])
    declining = int(items['下跌'])
    flat = int(items.get('平盘', 0))
    total = advancing + declining + flat
    if total <= 0:
        raise ValueError('Empty breadth counts')

    market_as_of = None
    date_matches = re.findall(r'\b\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\b', html_text)
    if date_matches:
        try:
            market_as_of = datetime.strptime(date_matches[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=SHANGHAI).isoformat()
        except Exception:
            market_as_of = None

    return {
        'advancing': advancing,
        'declining': declining,
        'flat': flat,
        'total': total,
        'market_as_of': market_as_of,
    }


def parse_tencent_turnover(text: str) -> float:
    turnover = 0.0
    for line in text.split(';'):
        parts = line.strip().split('~')
        if len(parts) > 7:
            try:
                turnover += float(parts[7]) * 10000.0
            except (ValueError, TypeError):
                pass
    return turnover


def parse_sina_turnover(text: str) -> float:
    turnover = 0.0
    for line in text.split('\n'):
        if '="' in line:
            content = line.split('="')[1].rstrip('";')
            parts = content.split(',')
            if len(parts) > 5:
                try:
                    turnover += float(parts[5]) * 10000.0
                except (ValueError, TypeError):
                    pass
    return turnover


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
    def __init__(self, cache_ttl=90, clock=time.monotonic, store=None):
        self.history = SentimentHistoryService(store)
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

    async def _fetch_fallback(self, client: httpx.AsyncClient):
        # 1. 广度数据 (优先直接抓取 Legu 页面，失败则调用本地 AkShare)
        breadth = None
        try:
            resp = await client.get('https://legulegu.com/stockdata/market-activity')
            if resp.status_code == 200:
                breadth = parse_legu_breadth(resp.text)
        except Exception:
            breadth = None

        if not breadth:
            try:
                import akshare as ak
                df = await asyncio.to_thread(ak.stock_market_activity_legu)
                item_dict = dict(zip(df['item'], df['value']))
                advancing = int(float(item_dict.get('上涨', 0)))
                declining = int(float(item_dict.get('下跌', 0)))
                flat = int(float(item_dict.get('平盘', 0)))
                raw_date = str(item_dict.get('统计日期', '')).strip()
                market_as_of = None
                if raw_date:
                    try:
                        market_as_of = datetime.strptime(raw_date, '%Y-%m-%d %H:%M:%S').replace(tzinfo=SHANGHAI).isoformat()
                    except Exception:
                        pass
                total = advancing + declining + flat
                if total > 0:
                    breadth = {
                        'advancing': advancing,
                        'declining': declining,
                        'flat': flat,
                        'total': total,
                        'market_as_of': market_as_of,
                    }
            except Exception:
                pass

        if not breadth:
            raise ValueError('All fallback breadth sources failed')

        # 2. 两市成交额 (腾讯行情优先，新浪为后备)
        turnover_cny = 0.0
        try:
            r_tx = await client.get('http://qt.gtimg.cn/q=s_sh000001,s_sz399001')
            if r_tx.status_code == 200:
                turnover_cny = parse_tencent_turnover(r_tx.text)
        except Exception:
            pass

        if turnover_cny <= 0:
            try:
                r_sina = await client.get('https://hq.sinajs.cn/list=s_sh000001,s_sz399001',
                                          headers={'Referer': 'https://finance.sina.com.cn'})
                if r_sina.status_code == 200:
                    turnover_cny = parse_sina_turnover(r_sina.text)
            except Exception:
                pass

        breadth['turnover_cny'] = round(turnover_cny, 2)
        breadth['source'] = '全市场广度与两市成交额（多源备用通道）'
        return breadth

    async def _fetch(self):
        async with httpx.AsyncClient(timeout=httpx.Timeout(6, connect=3), follow_redirects=True,
                                     headers={'User-Agent': 'Mozilla/5.0'}) as client:
            # 主通道：东方财富行情接口
            for url in EASTMONEY_BREADTH_URLS:
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    return parse_breadth(response.json()['data']['diff'])
                except (httpx.HTTPError, KeyError, TypeError, ValueError):
                    continue

            # 备用通道：全市场广度 + 腾讯/新浪两市成交额
            try:
                return await self._fetch_fallback(client)
            except Exception:
                pass

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
                source_label = breadth.get('source', '东方财富 · 上证指数与深证成指涨跌家数')
                result = {**breadth, 'status': 'ok', 'score': score, 'level': level, 'level_name': name,
                          'summary': summary, 'updated_at': datetime.now(SHANGHAI).isoformat(),
                          'source': source_label,
                          'method': '(上涨家数 + 0.5 × 平盘家数) / 总家数 × 100；市场广度情绪代理指标'}
                self._cached_data, self._cached_time = result, self.clock()
                self.history.schedule_current(result)
            except Exception:
                result = {**self._cached_data, 'status': 'stale'} if self._cached_data else {
                    'status': 'unavailable', 'score': None, 'level': None, 'level_name': '暂不可用',
                    'summary': '市场广度来源暂不可用，请稍后刷新', 'advancing': 0, 'declining': 0,
                    'flat': 0, 'total': 0, 'turnover_cny': 0.0, 'updated_at': None, 'market_as_of': None}
            self._last_attempt, self._last_result = self.clock(), result
            return result
