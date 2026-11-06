import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

import duckdb

from backend.auth import AuthStore
from backend.daily_history import LocalDailyHistory, archive_symbol, merge_daily_bars, _read_archive
from backend.daily_market import DailyMarketService
from backend.news_cleaning import SHANGHAI
from backend.research import ResearchService
from backend.research_store import ResearchStore


def bar(day, close=11):
    return {'date': day, 'open': 10, 'close': close, 'high': 15, 'low': 9, 'volume': 1000}


class HistoryFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.write('2010', [('600519.XSHG', '2010-12-31')])
        self.write('2011', [('600519.XSHG', '2011-01-04')])
        self.write('2020', [('600519.XSHG', '2020-12-31')])
        self.write('2021', [('600519.XSHG', '2021-01-04'), ('000001.XSHE', '2021-01-04')])
        self.write('2026-08', [('600519.XSHG', '2026-08-31'), ('688582.XSHG', '2026-08-28')])
        self.history = LocalDailyHistory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, rows):
        with duckdb.connect() as conn:
            conn.execute('CREATE TABLE bars(ts BIGINT,symbol VARCHAR,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume DOUBLE)')
            conn.executemany('INSERT INTO bars VALUES(?,?,10,15,9,11,1000)',
                             [(int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000), code) for code, day in rows])
            conn.execute('COPY bars TO ? (FORMAT PARQUET)', [str(self.root / (name + '.parquet'))])


class LocalHistoryTests(HistoryFixture, unittest.TestCase):
    def test_only_requested_equity_and_dates_since_2011_are_read(self):
        result = self.history.read('600519', '2026-10-30')
        self.assertEqual([row['date'] for row in result['candles']], ['2011-01-04', '2020-12-31', '2021-01-04', '2026-08-31'])
        self.assertEqual(result['candles'][0]['volume'], 1000)
        self.assertEqual(result['available_start'], '2011-01-04')

    def test_post_2021_listing_starts_at_its_first_real_bar(self):
        rows = self.history.read('688582', '2026-10-30')['candles']
        self.assertEqual([row['date'] for row in rows], ['2026-08-28'])

    def test_index_identity_does_not_alias_ping_an_bank(self):
        self.assertEqual(archive_symbol('000001'), '000001.XSHE')
        self.assertEqual(archive_symbol('sh000001'), '000001.XSHG')
        self.assertEqual(self.history.read('sh000001', '2026-10-30')['candles'], [])
        with self.assertRaises(ValueError):
            archive_symbol('../600519')

    def test_newer_sources_replace_overlap_and_invalid_rows_are_not_padded(self):
        result = merge_daily_bars([bar('2021-01-04'), bar('2026-08-31')],
                                 [bar('2026-08-31', 12), bar('2026-10-30'), bar('bad'),
                                  {**bar('2026-10-29'), 'close': float('nan')}])
        self.assertEqual([row['date'] for row in result], ['2021-01-04', '2026-08-31', '2026-10-30'])
        self.assertEqual(result[1]['close'], 12)

    def test_multiple_viewers_share_the_cold_file_read(self):
        before = _read_archive.cache_info().misses
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.history.read('600519', '2026-10-30'), range(16)))
        self.assertTrue(all(len(result['candles']) == 4 for result in results))
        self.assertEqual(_read_archive.cache_info().misses - before, 1)

    def test_missing_and_corrupt_archives_fail_without_replacing_recent_data(self):
        self.assertFalse(LocalDailyHistory('').read('600519', '2026-10-30')['configured'])
        self.assertIn('error', LocalDailyHistory(self.root / 'missing').read('600519', '2026-10-30'))
        (self.root / '2025.parquet').write_text('invalid parquet')
        self.assertIn('error', self.history.read('600519', '2026-10-30'))


class HistoryIntegrationTests(HistoryFixture, unittest.IsolatedAsyncioTestCase):
    async def test_first_read_has_history_without_any_upstream_request(self):
        auth = AuthStore(self.root / 'auth.db'); auth.initialize()
        store = ResearchStore(auth); store.initialize()
        fetch = AsyncMock()
        service = DailyMarketService(store, fetcher=fetch)
        service.history = self.history
        record = service.cached('600519')
        self.assertEqual(record['quote']['history_start'], '2011-01-04')
        self.assertEqual(record['quote']['as_of_date'], '2026-08-31')
        fetch.assert_not_awaited()
        await service.close()

    async def test_recent_refresh_persists_history_and_does_not_overwrite_news(self):
        auth = AuthStore(self.root / 'auth.db'); auth.initialize()
        store = ResearchStore(auth); store.initialize()
        store.put('dashboard', '600519', {'news': [{'id': 'keep'}], 'candles': []})
        fetch = AsyncMock(return_value={'candles': [bar('2026-10-30', 13)], 'price': 13, 'status': 'ok'})
        service = DailyMarketService(store, clock=lambda: datetime(2026, 10, 30, 18, tzinfo=SHANGHAI), fetcher=fetch)
        service.history = self.history
        try:
            result = await service.get({'code': '600519'})
            self.assertEqual([row['date'] for row in result['candles']], ['2011-01-04', '2020-12-31', '2021-01-04', '2026-08-31', '2026-10-30'])
            self.assertEqual(store.get('daily_market', '600519')['quote']['history_rows'], 5)
            self.assertEqual(store.get('dashboard', '600519')['news'], [{'id': 'keep'}])
        finally:
            await service.close()

    async def test_dashboard_uses_local_history_and_current_prices_together(self):
        auth = AuthStore(self.root / 'auth.db'); auth.initialize()
        store = ResearchStore(auth); store.initialize()
        recent = {'candles': [bar('2026-10-30', 13)], 'quote': {'as_of_date': '2026-10-30', 'price': 13, 'change': 1}}
        store.put('daily_market', '600519', recent)
        research = ResearchService(store, AsyncMock())
        research.daily_market.history = self.history
        try:
            result = research.dashboard('600519')
            self.assertEqual(result['candles'][0]['date'], '2011-01-04')
            self.assertEqual(result['stock']['price'], 13)
            self.assertEqual(result['candles'][-1]['date'], '2026-10-30')
        finally:
            await research.close()

class IndexHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_index_first_fetch_gets_long_history_then_only_refreshes_the_tail(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            auth=AuthStore(Path(folder)/'db');auth.initialize();store=ResearchStore(auth);store.initialize()
            now=[datetime(2026,11,5,18,tzinfo=SHANGHAI)]
            service=DailyMarketService(store,clock=lambda:now[0]);service.history=LocalDailyHistory('')
            fetch=AsyncMock(side_effect=[dict(candles=[bar('2011-01-04'),bar('2026-11-05')],price=11,status='ok'),
                                         dict(candles=[bar('2026-11-06',12)],price=12,status='ok')])
            try:
                with patch('backend.providers.daily_market',fetch):
                    first=await service.get({'code':'sh000300'})
                    self.assertEqual(first['candles'][0]['date'],'2011-01-04')
                    fetch.assert_awaited_once_with({'code':'sh000300'},'2026-11-05',start='2011-01-01')
                    now[0]=datetime(2026,11,6,18,tzinfo=SHANGHAI)
                    second=await service.get({'code':'sh000300'})
                    self.assertEqual(second['candles'][0]['date'],'2011-01-04')
                    self.assertEqual(second['candles'][-1]['date'],'2026-11-06')
                    self.assertEqual(fetch.await_args.kwargs,{})
                    self.assertEqual(second['history_requested_start'],'2011-01-01')
            finally:await service.close()

    async def test_recent_closed_index_cache_still_schedules_missing_long_history(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            auth=AuthStore(Path(folder)/'db');auth.initialize();store=ResearchStore(auth);store.initialize()
            now=datetime(2026,11,6,18,tzinfo=SHANGHAI)
            store.put('daily_market','sh000300',dict(candles=[bar('2026-11-06')],
                quote=dict(as_of_date='2026-11-06',collected_at=now.isoformat()),attempted_at=now.timestamp()-120))
            service=DailyMarketService(store,clock=lambda:now);service.history=LocalDailyHistory('')
            fetch=AsyncMock(return_value=dict(candles=[bar('2011-01-04'),bar('2026-11-06')],price=11,status='ok'))
            try:
                with patch('backend.providers.daily_market',fetch):
                    state=await service.snapshot({'code':'sh000300'})
                    self.assertTrue(state['refreshing'])
                    await asyncio.gather(*list(service.tasks.values()))
                    self.assertEqual(service.cached('sh000300')['candles'][0]['date'],'2011-01-04')
                    fetch.assert_awaited_once_with({'code':'sh000300'},'2026-11-06',start='2011-01-01')
            finally:await service.close()
