"""Shared cached snapshots, progressive loading and optional full-source waits."""
import asyncio
import logging
import time
from datetime import datetime, timedelta

from .news_cleaning import SHANGHAI
from .catalog import DEFAULT_WATCHLIST
from .trading_calendar import market_state

PERIODS = (1, 5, 15, 30, 60)


def aggregate_minutes(candles, period):
    if period == 1:
        return candles
    groups = {}
    for bar in candles:
        stamp = datetime.fromisoformat(bar['date'])
        # Source timestamps label minute ends. Morning and afternoon are
        # separate sessions; 09:30/13:00 belong to their first bucket.
        start = stamp.replace(hour=9 if stamp.hour < 12 else 13, minute=30 if stamp.hour < 12 else 0, second=0)
        offset = int((stamp - start).total_seconds() // 60)
        if not 0 <= offset <= 120:
            continue
        end = start + timedelta(minutes=max(1, (offset + period - 1) // period) * period)
        key = end.strftime('%Y-%m-%d %H:%M:%S')
        if key not in groups:
            groups[key] = ({**bar, 'date': key}, set(), end)
        merged, stamps, _ = groups[key]
        merged.update(close=bar['close'], high=max(merged['high'], bar['high']), low=min(merged['low'], bar['low']))
        merged['volume'] = (merged['volume'] if stamps else 0) + bar['volume']
        stamps.add(stamp)
    result = []
    for merged, stamps, end in groups.values():
        # Retain gaps honestly; never invent missing minute prices/volume.
        merged['partial'] = not all(end - timedelta(minutes=i) in stamps for i in range(period))
        result.append(merged)
    return result


class MarketBundleService:
    def __init__(self, research, minute_market, wait_seconds=105):
        self.research = research
        self.minute_market = minute_market
        self.store = research.store
        self.wait_seconds = wait_seconds
        self.tasks = {}
        self.snapshot_tasks = {}
        self.recent_views = {}
        self.warm_task = None
        self.warm_index = 0
        self.closed = False

    async def get(self, stock, force=False, wait=True):
        code = stock['code']
        if self.closed:
            raise RuntimeError('Market service is closed')
        self.recent_views.pop(code, None)
        self.recent_views[code] = time.monotonic()
        if len(self.recent_views) > 50:
            self.recent_views.pop(next(iter(self.recent_views)))
        tasks = self.tasks if wait else self.snapshot_tasks
        if code not in tasks:
            if len(self.tasks) + len(self.snapshot_tasks) >= 32:
                from fastapi import HTTPException
                raise HTTPException(429, '行情请求繁忙，请稍后重试。')
            tasks[code] = asyncio.create_task(self._build(stock, force) if wait else self._snapshot(stock, force))
        # A viewer leaving must not cancel acquisition shared by other viewers.
        return await asyncio.shield(tasks[code])

    async def _snapshot(self, stock, force):
        try:
            return await self._assemble(stock, schedule=True, force=force)
        finally:
            self.snapshot_tasks.pop(stock['code'], None)

    async def _settle(self, service, stock, minute=False, force=False):
        await (service.snapshot(stock, 1, force=force) if minute else service.snapshot(stock, force=force))
        key = stock['code'] + ':1' if minute else stock['code']
        deadline = asyncio.get_running_loop().time() + self.wait_seconds
        task = service.tasks.get(key)
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task), self.wait_seconds)
            except asyncio.TimeoutError:
                return '行情准备超时；可稍后刷新重试'
        # Another backend instance may hold the source lease. Wait for its
        # result too, even if this instance has an older usable cache.
        lease_key = ('minute:' if minute else 'daily:') + key
        while True:
            lease = await asyncio.to_thread(self.store.get, 'lease', lease_key, default={})
            if lease.get('expires', 0) <= time.time():
                return None
            if asyncio.get_running_loop().time() >= deadline:
                return '行情准备超时；可稍后刷新重试'
            await asyncio.sleep(.2)

    async def _build(self, stock, force):
        try:
            daily_error, minute_error = await asyncio.gather(
                self._settle(self.research.daily_market, stock, force=force),
                self._settle(self.minute_market, stock, minute=True, force=force))
            return await self._assemble(stock, daily_error=daily_error, minute_error=minute_error)
        finally:
            self.tasks.pop(stock['code'], None)

    async def _assemble(self, stock, schedule=False, force=False, daily_error=None, minute_error=None):
        daily_state, base, dashboard = await asyncio.gather(
            self.research.daily_market.snapshot(stock, schedule=schedule, force=force),
            self.minute_market.snapshot(stock, 1, schedule=schedule, force=force),
            asyncio.to_thread(self.research.dashboard, stock['code']))
        if daily_error:
            daily_state['error'] = daily_error
        dashboard['daily_request'] = daily_state
        sentiment = self.research.market_sentiment.snapshot()
        if sentiment is not None:
            dashboard['market_sentiment'] = sentiment
        minute_error = minute_error or base.get('error')
        minutes = {}
        now = datetime.fromisoformat(base['market']['server_time'])
        for period in PERIODS:
            bars = aggregate_minutes(base['candles'], period)
            forming = base['forming'] if period == 1 else bool(bars and base['market']['is_trading'] and datetime.fromisoformat(bars[-1]['date']).replace(tzinfo=SHANGHAI) > now)
            minutes[str(period)] = {**base, 'period': period, 'candles': bars, 'forming': forming,
                'error': minute_error, 'status': 'unavailable' if not bars else 'stale' if minute_error else base['status'],
                'derived_from': '1m', 'partial_bars': sum(bool(bar.get('partial')) for bar in bars),
                'note': '由同一份 1 分钟数据生成；较大周期不额外请求上游，缺失分钟不补造。'}
        errors = {key: error for key, error in [('daily', daily_state.get('error')), ('minute', minute_error)] if error}
        status = 'ok' if dashboard['candles'] and base['candles'] and not errors else 'partial'
        result = {'dashboard': dashboard, 'minutes': minutes, 'status': status, 'errors': errors,
                  'next_poll_seconds': 2 if daily_state['refreshing'] or base['refreshing'] else 30, 'fetched_at': datetime.now(SHANGHAI).isoformat()}
        # Persist all periods and daily data, without copying/overwriting
        # news, analyses or private user preferences.
        await asyncio.to_thread(self.store.put, 'market_bundle', stock['code'], {
            'daily': {'candles': dashboard['candles'], 'quote': dashboard['quote']},
            **{key: value for key, value in result.items() if key != 'dashboard'}})
        return result

    def start_warming(self):
        if not self.closed and self.warm_task is None:
            self.warm_task = asyncio.create_task(self._warm_loop())

    async def warm_once(self):
        # One speculative source at a time; leave the second source slot for
        # demand and do not queue preloads behind active viewers.
        if self.closed or self.minute_market.tasks:
            return None
        cutoff = time.monotonic() - 1800
        recent = [code for code, viewed in reversed(list(self.recent_views.items())) if viewed >= cutoff]
        codes = list(dict.fromkeys([*recent, *DEFAULT_WATCHLIST]))[:8]
        code = codes[self.warm_index % len(codes)]
        self.warm_index += 1
        stock = self.research.catalog.get(code)
        if stock:
            await self.minute_market.snapshot(stock, 1)
            return code
        return None

    async def _warm_loop(self):
        while not self.closed:
            try:
                await self.warm_once()
            except Exception as exc:
                logging.getLogger(__name__).warning('Minute preload unavailable (%s)', type(exc).__name__)
            state = market_state(self.minute_market.clock())
            await asyncio.sleep(5 if state['is_trading'] else 60)

    async def close(self):
        self.closed = True
        tasks = [*self.tasks.values(), *self.snapshot_tasks.values()]
        if self.warm_task:
            tasks.append(self.warm_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
