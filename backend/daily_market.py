"""Independent daily prices; no news/model work or dashboard overwrites."""
import asyncio
from datetime import datetime

from . import providers
from .news_cleaning import SHANGHAI
from .trading_calendar import market_state


def quote_order(quote):
    return (quote.get('as_of_date') or '', quote.get('collected_at') or '')


class DailyMarketService:
    def __init__(self, store, clock=None, fetcher=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(SHANGHAI))
        self.fetcher = fetcher
        self.tasks = {}
        self.capacity = asyncio.Semaphore(2)
        self.closed = False

    def cached(self, code):
        record = self.store.get('daily_market', code, default={})
        dashboard = self.store.get('dashboard', code, default={})
        if dashboard.get('candles') and (not record.get('candles') or quote_order(dashboard.get('quote', {})) > quote_order(record.get('quote', {}))):
            quote = dashboard.get('quote', {})
            record = {**record, 'candles': dashboard['candles'], 'quote': quote}
        return record

    def _due(self, record, state, force=False):
        elapsed = self.clock().timestamp() - record.get('attempted_at', 0)
        if elapsed < 60 or self.clock().timestamp() < record.get('retry_at', 0):
            return False
        if force or not record.get('candles'):
            return True
        expected = self.clock().date().isoformat() if state['is_trading'] else (state['expected_data_time'] or '')[:10]
        latest = record['candles'][-1]['date']
        quote = record.get('quote', {})
        fetched = datetime.fromisoformat(quote['collected_at']).timestamp() if quote.get('collected_at') else 0
        if expected and latest < expected:
            return elapsed >= 300
        if state['is_trading'] or state['state'] == 'calendar_unknown':
            return self.clock().timestamp() - fetched >= 300
        # The day's bar can change until the session finishes. Check it once
        # after the expected final close even when its date already matches.
        return bool(state['expected_data_time'] and latest == expected and fetched < datetime.fromisoformat(state['expected_data_time']).timestamp())

    async def snapshot(self, stock, force=False, schedule=True):
        record = await asyncio.to_thread(self.cached, stock['code'])
        state = market_state(self.clock())
        busy = False
        if schedule and not self.closed and self._due(record, state, force) and stock['code'] not in self.tasks:
            if len(self.tasks) < 32:
                self.tasks[stock['code']] = asyncio.create_task(self._refresh(stock, force))
            else:
                busy = True
        return {'refreshing': stock['code'] in self.tasks, 'error': record.get('error') or ('行情请求繁忙，请稍后重试' if busy else None),
                'market': state, 'next_poll_seconds': 2 if stock['code'] in self.tasks else 30}

    async def get(self, stock, force=True):
        """News collection shares the exact same daily source/cache work."""
        await self.snapshot(stock, force)
        task = self.tasks.get(stock['code'])
        if task:
            await asyncio.shield(task)
        # Another backend may own the database lease.
        deadline = asyncio.get_running_loop().time() + 55
        while True:
            record = await asyncio.to_thread(self.cached, stock['code'])
            if record.get('candles') and not record.get('error'):
                return {**record.get('quote', {}), 'candles': record['candles']}
            if record.get('error') or asyncio.get_running_loop().time() >= deadline:
                raise providers.ProviderError(record.get('error') or '日线行情获取超时')
            await asyncio.sleep(.2)

    async def _refresh(self, stock, force):
        code, token = stock['code'], None
        try:
            async with self.capacity:
                record = await asyncio.to_thread(self.cached, code)
                if not self._due(record, market_state(self.clock()), force):
                    return
                token = await asyncio.to_thread(self.store.claim, 'daily:' + code, 110)
                if not token:
                    return
                record = await asyncio.to_thread(self.cached, code)
                if not self._due(record, market_state(self.clock()), force):
                    return
                attempted = self.clock().timestamp()
                try:
                    # Look up the callable at request time so adapters can be
                    # replaced/tested without recreating the service.
                    fetcher = self.fetcher or providers.daily_market
                    result = await asyncio.wait_for(fetcher(stock, self.clock().date().isoformat()), 95)
                    if not result.get('candles'):
                        raise providers.ProviderError('行情源未返回有效日线')
                    quote = {key: value for key, value in result.items() if key != 'candles'}
                    quote.setdefault('as_of_date', result['candles'][-1]['date'])
                    quote.setdefault('collected_at', self.clock().isoformat())
                    record = {'candles': result['candles'], 'quote': quote, 'attempted_at': attempted, 'error': None, 'retry_at': 0, 'failures': 0}
                except Exception as exc:
                    failures = min(record.get('failures', 0) + 1, 4)
                    record = {**record, 'attempted_at': attempted, 'failures': failures,
                              'retry_at': self.clock().timestamp() + min(300, 30 * 2 ** failures),
                              'error': str(exc) if isinstance(exc, providers.ProviderError) else '日线行情获取失败，请稍后重试'}
                    if record.get('candles'):
                        record['quote'] = {**record.get('quote', {}), 'status': 'stale'}
                await asyncio.to_thread(self.store.put, 'daily_market', code, record)
        finally:
            if token:
                await asyncio.to_thread(self.store.release, 'daily:' + code, token)
            self.tasks.pop(code, None)

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
