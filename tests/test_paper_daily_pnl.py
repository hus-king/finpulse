"""Daily performance uses overnight exposure, all today's fills and actual prior closes."""
import asyncio
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from backend.auth import AuthStore
from backend.daily_market import DailyMarketService
from backend.minute_market import MinuteMarketService
from backend.news_cleaning import SHANGHAI
from backend.paper_rules import new_account,apply_trade,valid_quote
from backend.paper_store import PaperStore
from backend.paper_trading import PaperTradingService
from backend.research_store import ResearchStore
from backend.stock_catalog import StockCatalog
from test_paper_rules import cache

class DailyPerformanceTests(unittest.TestCase):
    def result(self,cash,positions,equity,trades=(),closes=None):
        from backend.paper_rules import daily_performance
        return daily_performance({'cash_fen':cash,'positions':{c:{'quantity':q} for c,q in positions.items()}},trades,closes or {},equity)

    def test_empty_new_account_and_cash_only_profit_have_zero_daily_pnl(self):
        for cash in [20_000_000,20_050_000]:
            r=self.result(cash,{},cash)
            self.assertEqual(r['daily_pnl_fen'],0);self.assertEqual(r['daily_pnl_base_equity_fen'],cash)

    def test_overnight_mark_is_compared_to_previous_close_not_entry_cost(self):
        r=self.result(19_900_000,{'000001':100},20_020_000,closes={'000001':1100})
        self.assertEqual(r['daily_pnl_fen'],10000);self.assertEqual(r['daily_pnl_base_equity_fen'],20_010_000)

    def test_new_buy_needs_no_previous_close_and_includes_fees(self):
        r=self.result(19_879_499,{'000001':100},19_999_499,
                      [{'code':'000001','side':'buy','quantity':100,'gross_fen':120000,'fees_fen':501}])
        self.assertEqual(r['daily_pnl_fen'],-501);self.assertEqual(r['daily_pnl_base_equity_fen'],20_000_000)

    def test_fully_sold_position_still_contributes_overnight_exposure(self):
        r=self.result(20_019_400,{},20_019_400,
                      [{'code':'000001','side':'sell','quantity':100,'gross_fen':120000,'fees_fen':600}],{'000001':1100})
        self.assertEqual(r['daily_pnl_fen'],9400);self.assertEqual(r['daily_pnl_base_equity_fen'],20_010_000)

    def test_partial_sale_and_new_buy_same_stock_are_both_counted(self):
        r=self.result(19_808_900,{'000001':200},20_058_900,[
            {'code':'000001','side':'buy','quantity':100,'gross_fen':120000,'fees_fen':500},
            {'code':'000001','side':'sell','quantity':100,'gross_fen':130000,'fees_fen':600}],{'000001':1000})
        self.assertEqual(r['daily_pnl_fen'],58900);self.assertEqual(r['daily_pnl_base_equity_fen'],20_000_000)

    def test_missing_previous_close_or_incomplete_ledger_is_unavailable_not_zero(self):
        r=self.result(19_900_000,{'000001':100},20_020_000)
        self.assertIsNone(r['daily_pnl_fen']);self.assertEqual(r['daily_pnl_missing_codes'],['000001'])
        bad=self.result(20_000_000,{},20_000_000,[{'code':'000001','side':'buy','quantity':100,'gross_fen':100000,'fees_fen':500}])
        self.assertIsNone(bad['daily_pnl_fen'])

    def test_percentage_uses_prior_equity_instead_of_initial_capital(self):
        r=self.result(0,{'000001':100000},200_100_000,closes={'000001':2000})
        self.assertEqual(r['daily_return_percent'],.05)

    def test_zero_baseline_is_unavailable_without_division_by_zero(self):
        r=self.result(0,{},0)
        self.assertIsNone(r['daily_pnl_fen']);self.assertIsNone(r['daily_return_percent'])

class DailyPnlServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory();auth=AuthStore(Path(self.directory.name)/'daily.db');auth.initialize()
        self.records=ResearchStore(auth);self.records.initialize();self.catalog=StockCatalog(self.records)
        self.now=datetime(2026,10,26,10,30,tzinfo=SHANGHAI);self.owner='one'
        self.fetch=AsyncMock(return_value={'candles':[{'date':'2026-10-23','open':11,'close':11,'high':11,'low':11,'volume':100}],'adjustment':'none'})
        self.daily=DailyMarketService(self.records,clock=lambda:self.now,fetcher=self.fetch)
        self.minute=MinuteMarketService(self.records,clock=lambda:self.now);self.minute.closed=True
        self.paper=PaperTradingService(PaperStore(self.records),self.catalog,self.minute,clock=lambda:self.now,daily_market=self.daily)
        self.stock=self.catalog.get('000001');old=self.now.replace(day=23)
        self.book,_=apply_trade(new_account(),self.stock,'buy',100,valid_quote(cache(old,10),old),old)
        self.records.put('paper_account','account',self.book,self.owner)
        self.records.put('minute_market','000001:1',cache(self.now,12))

    async def asyncTearDown(self):
        await self.daily.close();await self.minute.close();self.directory.cleanup()

    async def test_exact_prior_close_from_saved_raw_daily_history_does_not_fetch(self):
        self.records.put('quant_history','000001:2026-10-23',{'raw':[{'date':'2026-10-23','close':11}]})
        r=await self.paper.snapshot(self.owner)
        self.assertEqual(r['daily_pnl_fen'],10000);self.assertEqual(r['daily_reference_date'],'2026-10-23')
        self.assertEqual(r['daily_pnl_date'],'2026-10-26');self.fetch.assert_not_awaited()
        self.assertEqual(self.paper.store.get_account(self.owner),self.book)

    async def test_missing_close_returns_immediately_then_backfills_shared_daily_cache(self):
        gate=asyncio.Event()
        async def source(*args):
            await gate.wait();return {'candles':[{'date':'2026-10-23','close':11}]}
        self.fetch.side_effect=source
        first=await self.paper.snapshot(self.owner)
        self.assertIsNone(first['daily_pnl_fen']);self.assertEqual(first['daily_pnl_status'],'pending')
        self.assertEqual(first['next_poll_seconds'],2)
        gate.set();await asyncio.gather(*list(self.daily.tasks.values()))
        final=await self.paper.snapshot(self.owner)
        self.assertEqual(final['daily_pnl_fen'],10000);self.assertEqual(self.fetch.await_count,1)

    async def test_minute_fallback_requires_actual_prior_session_close(self):
        old=self.now.replace(day=23,hour=15,minute=0)
        self.records.put('minute_market','000001:1',cache(old,11))
        r=self.paper.account(self.owner,daily=True)
        self.assertEqual(r['daily_pnl_fen'],0)
        self.records.put('minute_market','000001:1',cache(old.replace(minute=59,hour=14),11))
        self.assertIsNone(self.paper.account(self.owner,daily=True)['daily_pnl_fen'])

    async def test_all_day_trades_are_owner_scoped_and_capped_at_book_sequence(self):
        for sequence in range(1,106):
            self.records.put('paper_trade',str(sequence),{'sequence':sequence,'executed_at':self.now.isoformat()},self.owner)
        self.records.put('paper_trade','other',{'sequence':1,'executed_at':self.now.isoformat()},'other-owner')
        items=self.paper.store.day_trades(self.owner,'2026-10-26',100)
        self.assertEqual(len(items),100);self.assertEqual(max(t['sequence'] for t in items),100)

    async def test_weekend_uses_latest_trading_day_and_unknown_calendar_does_not_guess(self):
        self.now=self.now.replace(day=24)
        self.records.put('quant_history','000001:2026-10-22',{'raw':[{'date':'2026-10-22','close':10}]})
        r=self.paper.account(self.owner,daily=True)
        self.assertEqual(r['daily_pnl_date'],'2026-10-23');self.assertEqual(r['daily_reference_date'],'2026-10-22')
        self.now=self.now.replace(year=2027)
        self.assertIsNone(self.paper.account(self.owner,daily=True)['daily_pnl_fen'])
