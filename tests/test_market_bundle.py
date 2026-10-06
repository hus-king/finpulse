import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app import app
from backend.auth import AuthStore
from backend.market_bundle import MarketBundleService, aggregate_minutes
from backend.minute_market import MinuteMarketService
from backend.news_cleaning import SHANGHAI
from backend.providers import ProviderError
from backend.research import ResearchService
from backend.research_store import ResearchStore

NOW = datetime(2026, 10, 6, 10, tzinfo=SHANGHAI)
STOCK = {'code': '000001', 'name': '平安银行'}
DAILY = {'date': '2026-09-30', 'open': 10, 'close': 11, 'low': 9, 'high': 12, 'volume': 1000}
MARKET = {'status': 'ok', 'source': 'test', 'price': 11, 'change': 1,
          'is_realtime': False, 'candles': [DAILY]}


def bar(stamp, value=10, volume=100):
    return {'date': stamp, 'open': value, 'close': value + 1,
            'high': value + 2, 'low': value - 1, 'volume': volume}


MINUTES = [bar('2026-09-30 14:56:00'), bar('2026-09-30 14:57:00', 11), bar('2026-09-30 15:00:00', 12)]


class AggregationTests(unittest.TestCase):
    def test_ohlcv_and_end_label_for_complete_five_minutes(self):
        rows = [bar(f'2026-09-30 09:{minute:02}:00', minute, minute * 10) for minute in range(31, 36)]
        result = aggregate_minutes(rows, 5)
        self.assertEqual(result, [{'date': '2026-09-30 09:35:00', 'open': 31, 'close': 36,
            'high': 37, 'low': 30, 'volume': 1650, 'partial': False}])
        self.assertEqual(aggregate_minutes(rows, 1), rows)

    def test_sessions_dates_and_boundaries_do_not_cross_lunch_or_overnight(self):
        rows = [bar('2026-09-29 15:00:00'), bar('2026-09-30 09:30:00'),
                bar('2026-09-30 10:30:00'), bar('2026-09-30 11:30:00'),
                bar('2026-09-30 13:00:00'), bar('2026-09-30 15:00:00')]
        result = aggregate_minutes(rows, 60)
        self.assertEqual([item['date'] for item in result], [
            '2026-09-29 15:00:00', '2026-09-30 10:30:00', '2026-09-30 11:30:00',
            '2026-09-30 14:00:00', '2026-09-30 15:00:00'])
        self.assertEqual(result[1]['volume'], 200)
        self.assertTrue(all(item['partial'] for item in result))

    def test_missing_minutes_are_marked_and_no_empty_buckets_are_created(self):
        result = aggregate_minutes([bar('2026-09-30 09:32:00'), bar('2026-09-30 09:51:00')], 5)
        self.assertEqual(len(result), 2)
        self.assertEqual([item['date'] for item in result], ['2026-09-30 09:35:00', '2026-09-30 09:55:00'])
        self.assertTrue(all(item['partial'] for item in result))
        self.assertEqual(sum(item['volume'] for item in result), 200)


class BundleServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.directory.name) / 'bundle.db')
        auth.initialize()
        self.store = ResearchStore(auth)
        self.store.initialize()
        self.now = NOW
        self.research = ResearchService(self.store, AsyncMock())
        self.research.daily_market.clock = lambda: self.now
        self.daily = AsyncMock(return_value=MARKET)
        self.research.daily_market.fetcher = self.daily
        self.fetch = AsyncMock(return_value=MINUTES)
        self.minute = MinuteMarketService(self.store, clock=lambda: self.now, fetcher=self.fetch)
        self.bundle = MarketBundleService(self.research, self.minute)

    async def asyncTearDown(self):
        await self.bundle.close()
        await self.minute.close()
        await self.research.close()
        self.directory.cleanup()

    async def test_one_wait_returns_all_periods_persists_and_reuses_after_restart(self):
        original = self.research.dashboard(STOCK['code'])
        original['pipeline'] = {'marker': 'do not change news pipeline'}
        self.store.put('dashboard', STOCK['code'], original)
        with patch('backend.providers.search_news', new_callable=AsyncMock) as news:
            result = await self.bundle.get(STOCK)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['dashboard']['candles'], [DAILY])
            self.assertEqual(set(result['minutes']), {'1', '5', '15', '30', '60'})
            self.assertTrue(all(not item['refreshing'] for item in result['minutes'].values()))
            self.assertEqual(result['minutes']['1']['candles'], MINUTES)
            self.fetch.assert_awaited_once_with(STOCK, 1)
            self.daily.assert_awaited_once()
            saved = self.store.get('market_bundle', STOCK['code'])
            self.assertEqual(saved['daily']['candles'], [DAILY])
            self.assertEqual(saved['minutes'], result['minutes'])
            self.assertEqual(self.store.get('dashboard', STOCK['code']), original)
            news.assert_not_awaited()
        other_research = ResearchService(self.store, AsyncMock())
        other_research.daily_market.clock = lambda: self.now
        other_daily = AsyncMock(return_value=MARKET)
        other_research.daily_market.fetcher = other_daily
        other_fetch = AsyncMock(return_value=MINUTES)
        other_minute = MinuteMarketService(self.store, clock=lambda: self.now, fetcher=other_fetch)
        other = MarketBundleService(other_research, other_minute)
        try:
            self.assertEqual((await other.get(STOCK))['minutes']['60']['candles'], saved['minutes']['60']['candles'])
            other_fetch.assert_not_awaited()
            other_daily.assert_not_awaited()
        finally:
            await other.close()
            await other_minute.close()
            await other_research.close()

    async def test_twenty_viewers_wait_for_both_parallel_sources_once(self):
        gate, daily_started, minute_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def daily(*args):
            daily_started.set()
            await gate.wait()
            return MARKET
        async def minute(*args):
            minute_started.set()
            await gate.wait()
            return MINUTES
        self.daily.side_effect, self.fetch.side_effect = daily, minute
        viewers = [asyncio.create_task(self.bundle.get(STOCK)) for _ in range(20)]
        await asyncio.wait_for(asyncio.gather(daily_started.wait(), minute_started.wait()), 5)
        self.assertTrue(all(not viewer.done() for viewer in viewers))
        gate.set()
        results = await asyncio.gather(*viewers)
        self.assertTrue(all(result == results[0] for result in results))
        self.daily.assert_awaited_once()
        self.fetch.assert_awaited_once_with(STOCK, 1)

    async def test_second_backend_waits_for_shared_database_leases(self):
        gate, started = asyncio.Event(), asyncio.Event()
        async def minute(*args):
            started.set()
            await gate.wait()
            return MINUTES
        self.fetch.side_effect = minute
        first = asyncio.create_task(self.bundle.get(STOCK))
        await asyncio.wait_for(started.wait(), 5)
        other_fetch = AsyncMock(return_value=MINUTES)
        other_minute = MinuteMarketService(self.store, clock=lambda: self.now, fetcher=other_fetch)
        other = MarketBundleService(self.research, other_minute)
        try:
            second = asyncio.create_task(other.get(STOCK))
            await asyncio.sleep(.1)
            self.assertFalse(second.done())
            gate.set()
            results = await asyncio.gather(first, second)
            self.assertEqual(results[0]['minutes']['1']['candles'], results[1]['minutes']['1']['candles'])
            other_fetch.assert_not_awaited()
        finally:
            gate.set()
            await other.close()
            await other_minute.close()

    async def test_source_failure_returns_available_daily_and_explicit_partial(self):
        self.fetch.side_effect = ProviderError('minute source down')
        result = await self.bundle.get(STOCK)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['errors']['minute'], 'minute source down')
        self.assertEqual(result['dashboard']['candles'], [DAILY])
        self.assertTrue(all(item['candles'] == [] for item in result['minutes'].values()))
        await self.bundle.get(STOCK)
        self.fetch.assert_awaited_once()

    async def test_failure_preserves_all_periods_derived_from_last_saved_minute(self):
        before = await self.bundle.get(STOCK)
        self.now += timedelta(seconds=61)
        self.fetch.side_effect = ProviderError('source down')
        after = await self.bundle.get(STOCK, force=True)
        for period in before['minutes']:
            self.assertEqual(before['minutes'][period]['candles'], after['minutes'][period]['candles'])
            self.assertEqual(after['minutes'][period]['status'], 'stale')
        self.assertEqual(self.fetch.await_count, 2)

    async def test_cancelling_viewer_does_not_cancel_shared_acquisition(self):
        gate, started = asyncio.Event(), asyncio.Event()
        async def minute(*args):
            started.set()
            await gate.wait()
            return MINUTES
        self.fetch.side_effect = minute
        viewer = asyncio.create_task(self.bundle.get(STOCK))
        await asyncio.wait_for(started.wait(), 5)
        viewer.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await viewer
        another = asyncio.create_task(self.bundle.get(STOCK))
        gate.set()
        self.assertEqual((await another)['status'], 'ok')
        self.fetch.assert_awaited_once()
        self.assertIsNotNone(self.store.get('market_bundle', STOCK['code']))

    async def test_timeout_returns_partial_without_unbounded_wait(self):
        self.bundle.wait_seconds = .05
        self.fetch.side_effect = lambda *args: None
        async def minute(*args):
            await asyncio.Event().wait()
        self.fetch.side_effect = minute
        result = await asyncio.wait_for(self.bundle.get(STOCK), 3)
        self.assertEqual(result['status'], 'partial')
        self.assertIn('超时', result['errors']['minute'])

    async def test_trading_updates_only_base_minute_and_marks_forming_bucket(self):
        self.now = datetime(2026, 10, 8, 10, 2, tzinfo=SHANGHAI)
        self.fetch.return_value = [bar('2026-10-08 10:01:00')]
        self.daily.return_value = {**MARKET, 'candles': [{**DAILY, 'date': '2026-10-08'}]}
        first = await self.bundle.get(STOCK)
        self.assertTrue(first['minutes']['5']['forming'])
        self.assertEqual(first['minutes']['5']['as_of'], '2026-10-08 10:01:00')
        self.assertEqual(first['minutes']['5']['candles'][-1]['date'], '2026-10-08 10:05:00')
        self.now += timedelta(seconds=31)
        await self.bundle.get(STOCK)
        self.assertEqual(self.fetch.await_count, 2)
        self.assertTrue(all(call.args[1] == 1 for call in self.fetch.await_args_list))
        self.daily.assert_awaited_once()


class BundleApiTests(unittest.TestCase):
    def test_single_http_response_without_login_news_or_model(self):
        with tempfile.TemporaryDirectory() as directory:
            app.state.auth_store = AuthStore(Path(directory) / 'api.db')
            try:
                with TestClient(app, base_url='http://localhost') as client:
                    app.state.research.daily_market.clock = lambda: NOW
                    app.state.minute_market.clock = lambda: NOW
                    rows = {'rows': [{**item, 'day': item['date']} for item in MINUTES]}
                    with patch('backend.providers.daily_market', new=AsyncMock(return_value=MARKET)) as daily, patch('backend.providers.akshare_worker', new=AsyncMock(return_value=rows)) as minute, patch('backend.providers.search_news', new_callable=AsyncMock) as news, patch('backend.app.completion', new_callable=AsyncMock) as model:
                        response = client.get('/api/market/000001/bundle')
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.headers['cache-control'], 'no-store')
                        result = response.json()
                        self.assertEqual(result['status'], 'ok')
                        self.assertEqual(result['dashboard']['candles'], [DAILY])
                        self.assertTrue(all(result['minutes'][str(period)]['candles'] for period in (1, 5, 15, 30, 60)))
                        minute.assert_awaited_once_with('minute_1', '000001', '', '', retry=False)
                        daily.assert_awaited_once()
                        self.assertEqual(client.get('/api/market/999999/bundle').status_code, 404)
                        news.assert_not_awaited()
                        model.assert_not_awaited()
            finally:
                del app.state.auth_store
