"""Daily breadth history; public full-market CSV backfill never blocks live quotes."""
import asyncio
import csv
import io
import math
import re
from datetime import datetime, timedelta

import httpx

from .news_cleaning import SHANGHAI
from .trading_calendar import is_trade_day

SOURCE = 'Money Tree · 沪深 A 股日行情汇总'
NAMESPACE = 'sentiment_day'


def parse_daily_breadth(text, day, minimum=3000):
    counts = dict(advancing=0, declining=0, flat=0)
    seen = set()
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not {'symbol', 'trade_date', 'pct_chg', 'vol'} <= set(reader.fieldnames or []):
        raise ValueError('Missing daily market columns')
    for row in reader:
        symbol = row.get('symbol', '')
        # Shanghai/Shenzhen A shares only: exclude Beijing, B shares and indices.
        if not re.fullmatch(r'(?:6\d{5}\.SH|[03]\d{5}\.SZ)', symbol):
            continue
        if row.get('trade_date') != day.replace('-', '') or symbol in seen:
            raise ValueError('Wrong trading day or duplicate symbol')
        seen.add(symbol)
        change, volume = float(row['pct_chg']), float(row['vol'])
        if not math.isfinite(change) or not math.isfinite(volume) or volume < 0:
            raise ValueError('Invalid daily market values')
        if volume > 0:
            counts['advancing' if change > 0 else 'declining' if change < 0 else 'flat'] += 1
    total = sum(counts.values())
    if total < minimum:
        raise ValueError('Incomplete daily market universe')
    return {**counts, 'total': total, 'score': round((counts['advancing'] + .5 * counts['flat']) / total * 100),
            'date': day, 'market_as_of': day + 'T15:00:00+08:00', 'source': SOURCE}


class SentimentHistoryService:
    def __init__(self, store, now=None, request_delay=8):
        self.store = store
        self.now = now or (lambda: datetime.now(SHANGHAI))
        self.task = None
        self.writer = None
        self.current = None
        self.pending = None
        self.request_delay = request_delay
        self.lock = asyncio.Lock()
        self.error = ''

    async def close(self):
        tasks = [t for t in (self.task, self.writer) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _days(self):
        today = self.now().astimezone(SHANGHAI).date()
        return [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)
                if is_trade_day(today - timedelta(days=i)) is not False]

    def _save(self, point):
        key = 'sentiment-day:' + point['date']
        token = self.store.claim(key, ttl=30)
        if not token:
            return
        try:
            old = self.store.get(NAMESPACE, point['date'])
            if not old or point['market_as_of'] >= old['market_as_of']:
                self.store.put(NAMESPACE, point['date'], point)
        finally:
            self.store.release(key, token)

    def _current_point(self, result):
        if not self.store or result.get('status') != 'ok' or not result.get('market_as_of'):
            return None
        try:
            stamp = datetime.fromisoformat(result['market_as_of']).astimezone(SHANGHAI)
            day = stamp.date().isoformat()
            if day not in self._days() or stamp > self.now() or stamp.hour < 9 or (stamp.hour == 9 and stamp.minute < 30):
                return None
            counts = {key: result[key] for key in ('advancing', 'declining', 'flat')}
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in counts.values()):
                return None
            total = sum(counts.values())
            if not total:
                return None
            return {**counts, 'total': total, 'score': round((counts['advancing'] + .5 * counts['flat']) / total * 100),
                    'date': day, 'market_as_of': stamp.isoformat(), 'source': result.get('source', '')}
        except (ValueError, TypeError, KeyError):
            return None

    def schedule_current(self, result):
        point = self._current_point(result)
        if not point or (self.current and point['market_as_of'] < self.current['market_as_of']):
            return
        self.current = self.pending = point
        if not self.writer or self.writer.done():
            self.writer = asyncio.create_task(self._write_current())

    async def _write_current(self):
        while self.pending:
            point, self.pending = self.pending, None
            try:
                await asyncio.to_thread(self._save, point)
            except Exception:
                self.error = '历史记录暂不可用'

    async def record_current(self, result):
        self.schedule_current(result)
        if self.writer:
            await asyncio.shield(self.writer)

    async def _fetch_day(self, day):
        async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=3), follow_redirects=True,
                                     headers={'User-Agent': 'FinPulse/1.0'}) as client:
            response = await client.get('https://money-tree.eyunzhu.com/daily_kline/date/' + day.replace('-', '') + '.csv')
            response.raise_for_status()
            if len(response.content) > 4_000_000:
                raise ValueError('Daily CSV too large')
            return parse_daily_breadth(response.text, day)

    async def _backfill(self, days):
        token = None
        try:
            token = await asyncio.to_thread(self.store.claim, 'sentiment-history-backfill', ttl=600)
            if not token:
                return
            cooldown = await asyncio.to_thread(self.store.get, 'sentiment_meta', 'backfill', '', {})
            if cooldown.get('retry_at', 0) > self.now().timestamp():
                return
            for index, day in enumerate(days):
                try:
                    point = await self._fetch_day(day)
                    await asyncio.to_thread(self._save, point)
                except httpx.HTTPStatusError as exc:
                    self.error = '部分历史日期暂未取得，保留已有数据'
                    if exc.response.status_code == 429:
                        await asyncio.to_thread(self.store.put, 'sentiment_meta', 'backfill', {'retry_at': self.now().timestamp() + 3600})
                        break
                except Exception:
                    self.error = '部分历史日期暂未取得，保留已有数据'
                finally:
                    await asyncio.to_thread(self.store.put, 'sentiment_attempt', day, {'at': self.now().timestamp()})
                if index < len(days) - 1:
                    await asyncio.sleep(self.request_delay)
        except Exception:
            self.error = '历史数据暂不可用，保留已有数据'
        finally:
            if token:
                await asyncio.to_thread(self.store.release, 'sentiment-history-backfill', token)

    async def snapshot(self):
        async with self.lock:
            days = self._days()
            today = self.now().date().isoformat()
            try:
                records = await asyncio.to_thread(self.store.list, NAMESPACE, '', 90) if self.store else []
                by_day = {r['key']: r['value'] for r in records if r['key'] in days}
                attempts = await asyncio.to_thread(self.store.list, 'sentiment_attempt', '', 90) if self.store else []
                attempted = {r['key']: r['value']['at'] for r in attempts}
                if self.current and self.current['date'] in days:
                    old = by_day.get(self.current['date'])
                    if not old or self.current['market_as_of'] >= old['market_as_of']:
                        by_day[self.current['date']] = self.current
                cooldown = await asyncio.to_thread(self.store.get, 'sentiment_meta', 'backfill', '', {}) if self.store else {}
                closed_today = self.now().hour >= 15
                missing = [d for d in days if (d < today or closed_today) and (d not in by_day or by_day[d]['market_as_of'][11:16] < '15:00')]
                pending = [d for d in reversed(missing) if self.now().timestamp() - attempted.get(d, 0) >= 3600 and cooldown.get('retry_at', 0) <= self.now().timestamp()]
                if pending and (not self.task or self.task.done()) and self.store:
                    self.error = ''
                    self.task = asyncio.create_task(self._backfill(pending))
                points = [by_day[d] for d in days if d in by_day]
                refreshing = bool(self.task and not self.task.done())
                return {'points': points, 'refreshing': refreshing, 'missing_days': len(missing),
                        'status': 'ok' if points else 'loading' if refreshing else 'unavailable',
                        'message': self.error, 'trading_dates': days, 'from_date': (self.now().date() - timedelta(days=29)).isoformat(),
                        'to_date': today, 'source': SOURCE}
            except Exception:
                return {'points': [], 'refreshing': False, 'missing_days': 0, 'status': 'unavailable',
                        'message': '历史记录暂不可用', 'trading_dates': days, 'from_date': days[0] if days else today, 'to_date': today, 'source': SOURCE}
