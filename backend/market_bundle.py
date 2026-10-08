"""One awaited response: daily history and minute periods derived from 1m bars."""
import asyncio
import time
from datetime import datetime, timedelta

from .news_cleaning import SHANGHAI

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
        self.closed = False

    async def get(self, stock, force=False):
        code = stock['code']
        if self.closed:
            raise RuntimeError('Market service is closed')
        if code not in self.tasks:
            if len(self.tasks) >= 32:
                from fastapi import HTTPException
                raise HTTPException(429, '行情请求繁忙，请稍后重试。')
            self.tasks[code] = asyncio.create_task(self._build(stock, force))
        # A viewer leaving must not cancel acquisition shared by other viewers.
        return await asyncio.shield(self.tasks[code])

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
            daily_state, base, dashboard = await asyncio.gather(
                self.research.daily_market.snapshot(stock, schedule=False),
                self.minute_market.snapshot(stock, 1, schedule=False),
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
                      'next_poll_seconds': 30, 'fetched_at': datetime.now(SHANGHAI).isoformat()}
            # Persist all periods and daily data, without copying/overwriting
            # news, analyses or private user preferences.
            await asyncio.to_thread(self.store.put, 'market_bundle', stock['code'], {
                'daily': {'candles': dashboard['candles'], 'quote': dashboard['quote']},
                **{key: value for key, value in result.items() if key != 'dashboard'}})
            return result
        finally:
            self.tasks.pop(stock['code'], None)

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
