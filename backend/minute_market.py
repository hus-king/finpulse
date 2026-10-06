"""Demand-driven minute bars, persisted shared cache and bounded source work."""
import asyncio
import math
from datetime import datetime, timedelta

from . import providers
from .news_cleaning import SHANGHAI
from .trading_calendar import market_state

PERIODS = {1, 5, 15, 30, 60}
MAX_BARS = 5000


def normalize_rows(rows, period, now=None):
    now = now or datetime.now(SHANGHAI)
    bars = {}
    for row in rows:
        try:
            stamp = datetime.fromisoformat(str(row['day'])).replace(tzinfo=SHANGHAI)
            clock = stamp.strftime('%H:%M:%S')
            if stamp > now + timedelta(minutes=period) or not ('09:30:00' <= clock <= '11:30:00' or '13:00:00' <= clock <= '15:00:00'):
                continue
            bar = {field: float(row[field]) for field in ('open', 'close', 'high', 'low', 'volume')}
            if not all(math.isfinite(value) for value in bar.values()):
                continue
            if min(bar['open'], bar['close'], bar['high'], bar['low']) <= 0 or bar['volume'] < 0:
                continue
            if bar['low'] > min(bar['open'], bar['close']) or bar['high'] < max(bar['open'], bar['close']) or bar['low'] > bar['high']:
                continue
            bar['date'] = stamp.strftime('%Y-%m-%d %H:%M:%S')
            bars[bar['date']] = bar
        except (KeyError, TypeError, ValueError):
            continue
    if not bars:
        raise providers.ProviderError('新浪行情源未返回有效分钟 K 线')
    return sorted(bars.values(), key=lambda bar: bar['date'])


def merge_bars(previous, incoming):
    # Never duplicate the currently forming candle or manufacture missing bars.
    return sorted({bar['date']: bar for bar in [*previous, *incoming]}.values(), key=lambda bar: bar['date'])[-MAX_BARS:]


class MinuteMarketService:
    def __init__(self, store, clock=None, fetcher=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(SHANGHAI))
        self.fetcher = fetcher or self._provider
        self.tasks = {}
        self.capacity = asyncio.Semaphore(2)
        self.closed = False

    async def _provider(self, stock, period):
        data = await providers.akshare_worker('minute_' + str(period), stock['code'], '', '', retry=False)
        return normalize_rows(data['rows'], period, self.clock())

    def _due(self, cached, state, force=False):
        now = self.clock().timestamp()
        attempted = cached.get('attempted_at', 0)
        if now < cached.get('retry_at', 0) or now - attempted < 30:
            return False
        if not cached.get('candles') or force or state['is_trading']:
            return True
        if state['final_session'] and cached.get('final_session') != state['final_session']:
            return True
        expected = state['expected_data_time']
        latest = datetime.fromisoformat(cached['candles'][-1]['date']).replace(tzinfo=SHANGHAI)
        # A stale closed-session snapshot is retried infrequently, not every viewer poll.
        return bool(expected and latest < datetime.fromisoformat(expected) and now - attempted >= 300)

    async def snapshot(self, stock, period, force=False):
        key = f"{stock['code']}:{period}"
        cached = await asyncio.to_thread(self.store.get, 'minute_market', key, default={})
        state = market_state(self.clock())
        busy = False
        if not self.closed and self._due(cached, state, force) and key not in self.tasks:
            if len(self.tasks) < 32:
                self.tasks[key] = asyncio.create_task(self._refresh(stock, period, key, force))
            else:
                busy = True
        bars = cached.get('candles', [])
        latest = bars[-1] if bars else None
        last_time = datetime.fromisoformat(latest['date']).replace(tzinfo=SHANGHAI) if latest else None
        expected = datetime.fromisoformat(state['expected_data_time']) if state['expected_data_time'] else None
        # A longer bar may legitimately trail the clock by its own period.
        grace = timedelta(minutes=period if state['is_trading'] else 0)
        stale = bool(last_time and expected and last_time + grace < expected) or bool(cached.get('error'))
        status = 'unavailable' if not bars else 'stale' if stale else 'ok'
        forming = bool(last_time and state['is_trading'] and last_time.date() == self.clock().date() and last_time + timedelta(seconds=30) > self.clock())
        previous = next((bar for bar in reversed(bars) if latest and bar['date'][:10] < latest['date'][:10]), None)
        today = [bar for bar in bars if latest and bar['date'][:10] == latest['date'][:10]]
        quote = {'price': latest['close'] if latest else None,
                 'change': round((latest['close'] / previous['close'] - 1) * 100, 2) if previous else None,
                 'open': today[0]['open'] if today else None,
                 'high': max(bar['high'] for bar in today) if today else None,
                 'low': min(bar['low'] for bar in today) if today else None,
                 'volume': sum(bar['volume'] for bar in today) if today else None}
        refreshing = key in self.tasks
        return {'code': stock['code'], 'period': period, 'candles': bars, 'quote': quote,
                'source': 'AkShare / 新浪分钟行情', 'interface': 'stock_zh_a_minute',
                'adjustment': 'none', 'volume_unit': 'shares', 'status': status,
                'as_of': latest['date'] if latest else None, 'fetched_at': cached.get('fetched_at'),
                'market': state, 'refreshing': refreshing, 'forming': forming,
                'error': cached.get('error') or ('行情请求繁忙，请稍后重试' if busy else None),
                'next_poll_seconds': 2 if refreshing else 30,
                'is_realtime': bool(state['is_trading'] and status == 'ok' and cached.get('fetched_at') and self.clock().timestamp() - datetime.fromisoformat(cached['fetched_at']).timestamp() < 90),
                'note': '分钟行情按需轮询；休市保留最近交易数据，交易时段数据延迟单独提示。未复权，不是逐笔实时报价。'}

    async def _refresh(self, stock, period, key, force):
        token = None
        try:
            async with self.capacity:
                cached = await asyncio.to_thread(self.store.get, 'minute_market', key, default={})
                state = market_state(self.clock())
                if not self._due(cached, state, force):
                    return
                token = await asyncio.to_thread(self.store.claim, 'minute:' + key, 90)
                if not token:
                    return
                # Recheck after the shared lease: another instance may have just finished.
                cached = await asyncio.to_thread(self.store.get, 'minute_market', key, default={})
                if not self._due(cached, state, force):
                    return
                attempted = self.clock().timestamp()
                try:
                    incoming = await asyncio.wait_for(self.fetcher(stock, period), timeout=50)
                    if not incoming:
                        raise providers.ProviderError('行情源没有返回分钟数据')
                    record = {'candles': merge_bars(cached.get('candles', []), incoming),
                              'attempted_at': attempted, 'fetched_at': self.clock().isoformat(),
                              'failures': 0, 'retry_at': 0, 'error': None,
                              'final_session': state['final_session'] or cached.get('final_session')}
                except Exception as exc:
                    failures = min(cached.get('failures', 0) + 1, 4)
                    record = {**cached, 'attempted_at': attempted, 'failures': failures,
                              'retry_at': self.clock().timestamp() + min(300, 30 * 2 ** failures),
                              'error': str(exc) if isinstance(exc, providers.ProviderError) else '分钟行情获取失败，请稍后重试'}
                await asyncio.to_thread(self.store.put, 'minute_market', key, record)
        finally:
            if token:
                await asyncio.to_thread(self.store.release, 'minute:' + key, token)
            self.tasks.pop(key, None)

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
