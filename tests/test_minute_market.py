import asyncio
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app import app
from backend.auth import AuthStore
from backend.minute_market import MinuteMarketService, normalize_rows, merge_bars, MAX_BARS
from backend.news_cleaning import SHANGHAI
from backend.providers import ProviderError
from backend.research_store import ResearchStore
from backend.trading_calendar import market_state

STOCK = {'code': '600519', 'name': '贵州茅台'}


def clock(value):
    return datetime.fromisoformat(value).replace(tzinfo=SHANGHAI)


def bar(stamp='2026-10-08 10:00:00', close=101):
    return {'date': stamp, 'open': 100, 'close': close, 'high': 105, 'low': 95, 'volume': 1000}


class CalendarAndBarTests(unittest.TestCase):
    def test_holiday_and_makeup_saturday_are_not_trading_days(self):
        holiday = market_state(clock('2026-10-06 10:00:00'))
        self.assertEqual(holiday['label'], '节假日休市')
        self.assertEqual(holiday['expected_data_time'], '2026-09-30T15:00:00+08:00')
        self.assertEqual(market_state(clock('2026-10-10 10:00:00'))['label'], '周末休市')

    def test_session_boundaries_lunch_final_sync_and_auction(self):
        for stamp, state in [('09:29:59', 'pre_open'), ('09:30:00', 'trading'),
                             ('11:29:59', 'trading'), ('11:30:00', 'lunch_break'),
                             ('12:59:59', 'lunch_break'), ('13:00:00', 'trading'),
                             ('14:59:59', 'trading'), ('15:00:00', 'after_close')]:
            with self.subTest(stamp=stamp):
                self.assertEqual(market_state(clock('2026-10-08 ' + stamp))['state'], state)
        self.assertEqual(market_state(clock('2026-10-08 14:58:00'))['label'], '收盘集合竞价')
        self.assertIsNone(market_state(clock('2026-10-08 15:00:29'))['final_session'])
        self.assertEqual(market_state(clock('2026-10-08 15:00:30'))['final_session'], '2026-10-08:afternoon')

    def test_unverified_year_is_explicitly_unknown(self):
        state = market_state(clock('2027-01-04 10:00:00'))
        self.assertEqual(state['state'], 'calendar_unknown')
        self.assertIsNone(state['is_trade_day'])

    def test_normalization_filters_invalid_and_non_session_bars(self):
        valid = {'day': '2026-10-08 09:31:00', 'open': '100', 'close': '101', 'low': '99', 'high': '102', 'volume': '500'}
        rows = [valid, {**valid, 'day': '2026-10-08 09:32:00', 'close': '100'},
                {**valid, 'open': '0'}, {**valid, 'day': '2026-10-08 12:00:00'},
                {**valid, 'high': '99'}, {**valid, 'volume': '-1'}, {**valid, 'close': 'inf'},
                {**valid, 'day': '2026-10-09 10:00:00'}]
        result = normalize_rows(rows, 1, clock('2026-10-08 10:00:00'))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['close'], 101)
        self.assertEqual(result[0]['volume'], 500)
        with self.assertRaises(ProviderError):
            normalize_rows([{'day': 'bad'}], 1)

    def test_merge_overwrites_current_bar_appends_and_bounds_history(self):
        previous = [bar(), bar('2026-10-08 10:01:00')]
        merged = merge_bars(previous, [bar('2026-10-08 10:01:00', 103), bar('2026-10-08 10:02:00')])
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[1]['close'], 103)
        many = [bar((clock('2026-01-01 09:30:00') + timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M:%S')) for i in range(MAX_BARS + 10)]
        self.assertEqual(len(merge_bars([], many)), MAX_BARS)


class MinuteServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.directory.name) / 'test.db')
        auth.initialize()
        self.store = ResearchStore(auth)
        self.store.initialize()
        self.now = clock('2026-10-08 10:00:20')
        self.fetcher = AsyncMock(return_value=[bar()])
        self.service = MinuteMarketService(self.store, clock=lambda: self.now, fetcher=self.fetcher)

    async def asyncTearDown(self):
        await self.service.close()
        self.directory.cleanup()

    async def drain(self, service=None):
        service = service or self.service
        await asyncio.wait_for(asyncio.gather(*list(service.tasks.values())), 5)

    async def seed(self, bars, **extra):
        self.store.put('minute_market', '600519:1', {'candles': bars, 'attempted_at': self.now.timestamp(),
                       'fetched_at': self.now.isoformat(), **extra})

    async def test_first_read_returns_immediately_then_real_result_persists(self):
        initial = await self.service.snapshot(STOCK, 1)
        self.assertEqual(initial['candles'], [])
        self.assertTrue(initial['refreshing'])
        await self.drain()
        result = await self.service.snapshot(STOCK, 1)
        self.assertEqual(result['quote']['price'], 101)
        self.assertTrue(result['forming'])
        self.assertTrue(result['is_realtime'])
        self.assertEqual(self.store.get('minute_market', '600519:1')['candles'], [bar()])
        self.assertEqual(self.fetcher.await_count, 1)

    async def test_twenty_viewers_coalesce_and_shared_instances_use_one_lease(self):
        gate, started = asyncio.Event(), asyncio.Event()
        async def slow_fetch(*args):
            started.set()
            await gate.wait()
            return [bar()]
        self.fetcher.side_effect = slow_fetch
        await asyncio.gather(*(self.service.snapshot(STOCK, 1) for _ in range(20)))
        await asyncio.wait_for(started.wait(), 3)
        self.assertEqual(len(self.service.tasks), 1)
        second_fetch = AsyncMock(return_value=[bar()])
        second = MinuteMarketService(self.store, clock=lambda: self.now, fetcher=second_fetch)
        try:
            await second.snapshot(STOCK, 1)
            await self.drain(second)
            second_fetch.assert_not_awaited()
            gate.set()
            await self.drain()
            self.assertEqual((await second.snapshot(STOCK, 1))['quote']['price'], 101)
            self.assertEqual(self.fetcher.await_count, 1)
        finally:
            gate.set()
            await second.close()

    async def test_trading_refresh_overwrites_then_appends_without_touching_news(self):
        news = {'news': [{'title': 'original'}], 'candles': [{'date': '2026-09-30'}]}
        self.store.put('dashboard', '600519', news)
        await self.service.snapshot(STOCK, 1)
        await self.drain()
        self.now += timedelta(seconds=31)
        self.fetcher.return_value = [bar(close=103), bar('2026-10-08 10:01:00', 104)]
        await self.service.snapshot(STOCK, 1)
        await self.drain()
        result = await self.service.snapshot(STOCK, 1)
        self.assertEqual([item['close'] for item in result['candles']], [103, 104])
        self.assertEqual(self.store.get('dashboard', '600519'), news)

    async def test_holiday_cache_and_restart_do_not_repeat_source_requests(self):
        self.now = clock('2026-10-06 12:00:00')
        self.fetcher.return_value = [bar('2026-09-30 15:00:00')]
        await self.service.snapshot(STOCK, 1)
        await self.drain()
        self.now += timedelta(hours=2)
        result = await self.service.snapshot(STOCK, 1)
        self.assertFalse(result['refreshing'])
        self.assertEqual(result['market']['label'], '节假日休市')
        self.assertEqual(self.fetcher.await_count, 1)
        restarted = MinuteMarketService(self.store, clock=lambda: self.now, fetcher=self.fetcher)
        try:
            self.assertEqual((await restarted.snapshot(STOCK, 1))['candles'], [bar('2026-09-30 15:00:00')])
            self.assertEqual(self.fetcher.await_count, 1)
        finally:
            await restarted.close()

    async def test_manual_refresh_obeys_thirty_second_floor(self):
        await self.seed([bar()])
        self.assertFalse((await self.service.snapshot(STOCK, 1, force=True))['refreshing'])
        self.now += timedelta(seconds=31)
        await self.service.snapshot(STOCK, 1, force=True)
        await self.drain()
        self.assertEqual(self.fetcher.await_count, 1)

    async def test_source_failure_keeps_cache_and_uses_backoff(self):
        await self.seed([bar()])
        self.now += timedelta(seconds=31)
        self.fetcher.side_effect = ProviderError('源站不可用')
        await self.service.snapshot(STOCK, 1)
        await self.drain()
        result = await self.service.snapshot(STOCK, 1)
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['candles'], [bar()])
        self.assertFalse(result['refreshing'])
        self.now += timedelta(seconds=61)
        await self.service.snapshot(STOCK, 1)
        await self.drain()
        self.assertEqual(self.fetcher.await_count, 2)
        self.assertEqual(self.store.get('minute_market', '600519:1')['failures'], 2)

    async def test_lunch_and_close_finalize_once_and_then_pause(self):
        self.now = clock('2026-10-08 11:30:31')
        await self.seed([bar('2026-10-08 11:29:00')])
        self.store.put('minute_market', '600519:1', {**self.store.get('minute_market', '600519:1'), 'attempted_at': self.now.timestamp()-60})
        self.fetcher.return_value = [bar('2026-10-08 11:30:00')]
        await self.service.snapshot(STOCK, 1)
        await self.drain()
        self.now += timedelta(minutes=1)
        self.assertFalse((await self.service.snapshot(STOCK, 1))['refreshing'])
        self.assertEqual(self.fetcher.await_count, 1)

    async def test_longer_period_is_not_falsely_stale_and_delayed_data_is(self):
        self.now = clock('2026-10-08 10:24:00')
        self.store.put('minute_market','600519:30', {'candles':[bar('2026-10-08 10:00:00')], 'attempted_at':self.now.timestamp(), 'fetched_at':self.now.isoformat()})
        self.assertEqual((await self.service.snapshot(STOCK, 30))['status'], 'ok')
        await self.seed([bar('2026-10-08 09:40:00')])
        result = await self.service.snapshot(STOCK, 1)
        self.assertEqual(result['status'], 'stale')
        self.assertFalse(result['is_realtime'])

    async def test_close_cancels_pending_source_and_releases_shared_lease(self):
        gate, started = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            started.set()
            await gate.wait()
        self.fetcher.side_effect = slow
        await self.service.snapshot(STOCK, 1)
        await asyncio.wait_for(started.wait(), 3)
        await self.service.close()
        self.assertFalse(self.service.tasks)
        self.assertIsNone(self.store.get('lease', 'minute:600519:1'))


class MinuteApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        app.state.auth_store = AuthStore(Path(self.directory.name) / 'api.db')
        self.context = TestClient(app, base_url='http://localhost')
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        del app.state.auth_store
        self.directory.cleanup()

    def test_invalid_period_and_unknown_stock_do_not_call_source(self):
        with patch('backend.providers.akshare_worker', new_callable=AsyncMock) as source:
            for period in ['0','2','61','bad']:
                self.assertEqual(self.client.get('/api/market/600519/minutes?period='+period).status_code, 422)
            self.assertEqual(self.client.get('/api/market/999999/minutes').status_code, 404)
            source.assert_not_awaited()

    def test_public_minute_reads_are_separate_from_protected_model_calls(self):
        app.state.minute_market.clock = lambda: clock('2026-10-06 12:00:00')
        rows = [{'day':'2026-09-30 15:00:00','open':'100','close':'101','low':'99','high':'102','volume':'1000'}]
        with patch('backend.providers.akshare_worker', new=AsyncMock(return_value={'rows': rows})) as source, patch('backend.app.completion', new_callable=AsyncMock) as model:
            initial = self.client.get('/api/market/600519/minutes?period=1')
            self.assertEqual(initial.status_code, 200)
            self.assertEqual(initial.headers['cache-control'], 'no-store')
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                data=self.client.get('/api/market/600519/minutes?period=1').json()
                if data['candles'] and not data['refreshing']:
                    break
                time.sleep(.02)
            self.assertEqual(data['as_of'],'2026-09-30 15:00:00')
            self.assertEqual(data['market']['state'],'closed')
            self.assertEqual(source.await_count,1)
            self.assertEqual(source.await_args.args[0],'minute_1')
            reply=self.client.post('/api/chat',headers={'Origin':'http://localhost'},json={'messages':[{'role':'user','content':'hello'}]})
            self.assertEqual(reply.status_code,401)
            model.assert_not_awaited()
