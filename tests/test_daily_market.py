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
from backend.daily_market import DailyMarketService
from backend.news_cleaning import SHANGHAI
from backend.providers import ProviderError
from backend.research import ResearchService
from backend.research_store import ResearchStore

STOCK = {'code': '000001', 'name': '平安银行'}
NOW = datetime(2026, 10, 6, 10, tzinfo=SHANGHAI)
BAR = {'date': '2026-09-30', 'open': 10, 'close': 11, 'low': 9, 'high': 12, 'volume': 1000}
MARKET = {'status': 'ok', 'source': 'test-source', 'price': 11, 'change': 1, 'is_realtime': False, 'candles': [BAR]}


class DailyServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.directory.name) / 'daily.db')
        auth.initialize()
        self.store = ResearchStore(auth)
        self.store.initialize()
        self.now = NOW
        self.fetch = AsyncMock(return_value=MARKET)
        self.service = DailyMarketService(self.store, clock=lambda: self.now, fetcher=self.fetch)

    async def asyncTearDown(self):
        await self.service.close()
        self.directory.cleanup()

    async def drain(self, service=None):
        await asyncio.wait_for(asyncio.gather(*list((service or self.service).tasks.values())), 5)

    async def test_opening_uncached_stock_returns_pending_without_news_or_dashboard_write(self):
        with patch('backend.providers.search_news', new_callable=AsyncMock) as news:
            self.assertTrue((await self.service.snapshot(STOCK))['refreshing'])
            await self.drain()
            saved = self.store.get('daily_market', STOCK['code'])
            self.assertEqual(saved['candles'], [BAR])
            self.assertEqual(saved['quote']['price'], 11)
            self.assertIsNone(self.store.get('dashboard', STOCK['code']))
            news.assert_not_awaited()

    async def test_twenty_viewers_and_collection_share_source_and_database_lease(self):
        gate, started = asyncio.Event(), asyncio.Event()
        async def fetch(*args):
            started.set()
            await gate.wait()
            return MARKET
        self.fetch.side_effect = fetch
        await asyncio.gather(*(self.service.snapshot(STOCK) for _ in range(20)))
        await asyncio.wait_for(started.wait(), 3)
        collecting = asyncio.create_task(self.service.get(STOCK))
        other_fetch = AsyncMock(return_value=MARKET)
        other = DailyMarketService(self.store, clock=lambda: self.now, fetcher=other_fetch)
        try:
            await other.snapshot(STOCK)
            await self.drain(other)
            other_fetch.assert_not_awaited()
            gate.set()
            self.assertEqual((await collecting)['candles'], [BAR])
            self.fetch.assert_awaited_once()
        finally:
            gate.set()
            await other.close()

    async def test_existing_holiday_daily_history_is_reused_and_restart_keeps_cache(self):
        self.store.put('dashboard', STOCK['code'], {'candles': [BAR], 'quote': {'as_of_date': BAR['date'], 'collected_at': NOW.isoformat()}})
        self.assertFalse((await self.service.snapshot(STOCK))['refreshing'])
        self.fetch.assert_not_awaited()
        self.assertTrue((await self.service.snapshot(STOCK, force=True))['refreshing'])
        await self.drain()
        self.assertFalse((await self.service.snapshot(STOCK, force=True))['refreshing'])
        self.fetch.assert_awaited_once()

    async def test_error_preserves_daily_cache_and_retry_is_bounded(self):
        await self.service.snapshot(STOCK)
        await self.drain()
        self.now += timedelta(seconds=61)
        self.fetch.side_effect = ProviderError('source down')
        await self.service.snapshot(STOCK, force=True)
        await self.drain()
        cached = self.store.get('daily_market', STOCK['code'])
        self.assertEqual(cached['candles'], [BAR])
        self.assertEqual(cached['quote']['status'], 'stale')
        self.assertFalse((await self.service.snapshot(STOCK, force=True))['refreshing'])
        self.assertEqual(self.fetch.await_count, 2)

    async def test_daily_overlay_preserves_news_written_while_source_waits(self):
        research = ResearchService(self.store, AsyncMock())
        try:
            before = research.dashboard(STOCK['code'])
            self.store.put('dashboard', STOCK['code'], {**before, 'news': [], 'as_of': '2026-10-06T10:00:00+08:00'})
            await self.service.snapshot(STOCK)
            await self.drain()
            saved = self.store.get('dashboard', STOCK['code'])
            news = [{'id': 'new', 'title': 'News', 'time': '2026-09-29', 'score': 1}]
            saved.update(news=news, as_of='2026-10-06T10:01:00+08:00')
            self.store.put('dashboard', STOCK['code'], saved)
            result = research.dashboard(STOCK['code'])
            self.assertEqual(result['news'], news)
            self.assertEqual(result['as_of'], saved['as_of'])
            self.assertEqual(result['candles'], [BAR])
            self.assertEqual(result['stock']['price'], 11)
            self.assertEqual(result['backtest']['items'][0]['base_date'], BAR['date'])
            self.assertEqual(self.store.get('dashboard', STOCK['code'])['candles'], [])
        finally:
            await research.close()

    async def test_current_day_is_checked_after_final_close(self):
        self.now = datetime(2026, 10, 8, 14, 59, tzinfo=SHANGHAI)
        today = {**BAR, 'date': '2026-10-08'}
        self.fetch.return_value = {**MARKET, 'candles': [today]}
        await self.service.snapshot(STOCK)
        await self.drain()
        self.now = self.now.replace(hour=15, minute=1)
        self.assertTrue((await self.service.snapshot(STOCK))['refreshing'])
        await self.drain()
        self.assertFalse((await self.service.snapshot(STOCK))['refreshing'])
        self.assertEqual(self.fetch.await_count, 2)


class DailyApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        app.state.auth_store = AuthStore(Path(self.directory.name) / 'api.db')
        self.context = TestClient(app, base_url='http://localhost')
        self.client = self.context.__enter__()
        app.state.research.daily_market.clock = lambda: NOW

    def tearDown(self):
        self.context.__exit__(None, None, None)
        del app.state.auth_store
        self.directory.cleanup()

    def test_new_stock_minute_then_daily_can_read_prices_without_news_or_login(self):
        minute_rows = {'rows': [{'day': '2026-09-30 15:00:00', 'open': 10, 'close': 11, 'low': 9, 'high': 12, 'volume': 1000}]}
        with patch('backend.providers.akshare_worker', new=AsyncMock(return_value=minute_rows)), patch('backend.providers.daily_market', new=AsyncMock(return_value=MARKET)) as daily, patch('backend.providers.search_news', new_callable=AsyncMock) as news, patch('backend.app.completion', new_callable=AsyncMock) as model:
            self.assertEqual(self.client.get('/api/dashboard/000001').json()['candles'], [])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                minute = self.client.get('/api/market/000001/minutes?period=1').json()
                if minute['candles'] and not minute['refreshing']:
                    break
                time.sleep(.02)
            self.assertEqual(minute['as_of'], '2026-09-30 15:00:00')
            daily.assert_not_awaited()
            self.assertEqual(self.client.get('/api/dashboard/000001').json()['candles'], [])
            response = self.client.get('/api/market/000001/daily')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = self.client.get('/api/market/000001/daily').json()
                if result['candles'] and not result['daily_request']['refreshing']:
                    break
                time.sleep(.02)
            self.assertEqual(result['candles'], [BAR])
            self.assertEqual(self.client.get('/api/dashboard/000001').json()['stock']['price'], 11)
            self.assertEqual(self.client.get('/api/market/999999/daily').status_code, 404)
            daily.assert_awaited_once()
            news.assert_not_awaited()
            model.assert_not_awaited()
