import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.auth import AuthStore
from backend.research_store import ResearchStore
from backend.sentiment_history import SentimentHistoryService, parse_daily_breadth
from backend.news_cleaning import SHANGHAI

NOW = datetime(2026, 10, 9, 16, tzinfo=SHANGHAI)
CSV = '''symbol,trade_date,pct_chg,vol
600001.SH,20261008,1.2,100
000001.SZ,20261008,-2,200
300001.SZ,20261008,0,300
600002.SH,20261008,0,0
900001.SH,20261008,10,100
430001.BJ,20261008,10,100
'''

class ParserTests(unittest.TestCase):
    def test_scope_excludes_beijing_b_shares_and_suspended(self):
        point = parse_daily_breadth(CSV, '2026-10-08', minimum=3)
        self.assertEqual((point['advancing'], point['declining'], point['flat'], point['score']), (1, 1, 1, 50))

    def test_partial_wrong_date_duplicates_and_invalid_numbers_rejected(self):
        for text in [CSV, CSV.replace('20261008', '20261007'), CSV + '600001.SH,20261008,1,100\n', CSV.replace('1.2', 'nan')]:
            with self.assertRaises(ValueError):
                parse_daily_breadth(text, '2026-10-08')
        with self.assertRaises(ValueError):
            parse_daily_breadth(CSV.replace('1.2', 'nan'), '2026-10-08', minimum=3)

class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.temp.name) / 'history.db'); auth.initialize()
        self.store = ResearchStore(auth); self.store.initialize()
        self.service = SentimentHistoryService(self.store, now=lambda: NOW, request_delay=0)

    async def asyncTearDown(self):
        await self.service.close(); self.temp.cleanup()

    async def drain(self):
        if self.service.task:
            await asyncio.wait_for(asyncio.shield(self.service.task), 5)

    async def test_nonblocking_coalesced_backfill_persists_across_restart(self):
        gate = asyncio.Event()
        async def fetch(day):
            await gate.wait()
            return {**parse_daily_breadth(CSV, '2026-10-08', minimum=3), 'date': day, 'market_as_of': day + 'T15:00:00+08:00'}
        with patch.object(self.service, '_fetch_day', new=AsyncMock(side_effect=fetch)) as source:
            results = await asyncio.gather(*(self.service.snapshot() for _ in range(12)))
            self.assertTrue(all(r['refreshing'] for r in results))
            self.assertTrue(all(not r['points'] for r in results))
            gate.set(); await self.drain()
            calls = source.await_count
            self.assertGreater(calls, 1)
            await self.service.snapshot(); self.assertEqual(source.await_count, calls)
        restarted = SentimentHistoryService(self.store, now=lambda: NOW, request_delay=0)
        result = await restarted.snapshot()
        self.assertGreater(len(result['points']), 1)
        self.assertFalse(result['refreshing'])
        self.assertTrue(all('2026-09-10' <= p['date'] <= '2026-10-09' for p in result['points']))
        await restarted.close()

    async def test_source_failure_retains_saved_days_and_backoff(self):
        point = parse_daily_breadth(CSV, '2026-10-08', minimum=3)
        self.store.put('sentiment_day', point['date'], point)
        with patch.object(self.service, '_fetch_day', new=AsyncMock(side_effect=ValueError('offline'))) as fetch:
            await self.service.snapshot(); await self.drain()
            result = await self.service.snapshot()
            self.assertEqual(result['points'], [point]); self.assertGreater(result['missing_days'], 0)
            count = fetch.await_count
            await self.service.snapshot(); self.assertEqual(fetch.await_count, count)

    async def test_current_uses_market_date_and_does_not_overwrite_newer_quote(self):
        current = dict(status='ok', market_as_of='2026-10-09T15:00:00+08:00', advancing=60, declining=30, flat=10, source='live')
        await self.service.record_current(current)
        await self.service.record_current({**current, 'market_as_of': '2026-10-09T10:00:00+08:00', 'advancing': 1})
        saved = self.store.get('sentiment_day', '2026-10-09')
        self.assertEqual(saved['score'], 65)
        await self.service.record_current({**current, 'market_as_of': None})
        await self.service.record_current({**current, 'status': 'stale'})
        self.assertEqual(len(self.store.list('sentiment_day')), 1)

    async def test_live_quote_returns_while_history_writer_is_blocked(self):
        from backend.market_sentiment import MarketSentimentService
        service = MarketSentimentService(store=self.store)
        service.history.now = lambda: NOW
        gate = asyncio.Event()
        async def blocked():
            await gate.wait()
        with patch.object(service, '_fetch', new=AsyncMock(return_value=dict(advancing=60, declining=30, flat=10, total=100, market_as_of='2026-10-09T15:00:00+08:00'))), patch.object(service.history, '_write_current', side_effect=blocked), patch.object(service.history, '_backfill', new=AsyncMock()):
            result = await asyncio.wait_for(service.get_sentiment(), .5)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(service.snapshot()['score'], 65)
            snapshot = await service.history.snapshot()
            self.assertEqual(snapshot['points'][-1]['date'], '2026-10-09')
            gate.set()
        await service.history.close()

    async def test_rate_limit_stops_backfill_and_persists_global_cooldown(self):
        import httpx
        response = httpx.Response(429, request=httpx.Request('GET', 'https://example.test'))
        with patch.object(self.service, '_fetch_day', new=AsyncMock(side_effect=httpx.HTTPStatusError('limited', request=response.request, response=response))) as fetch:
            await self.service.snapshot(); await self.drain()
            fetch.assert_awaited_once()
            result = await self.service.snapshot()
            self.assertFalse(result['refreshing'])
            fetch.assert_awaited_once()
        restarted = SentimentHistoryService(self.store, now=lambda: NOW, request_delay=0)
        self.assertFalse((await restarted.snapshot())['refreshing'])
        await restarted.close()
