"""Portfolio reads acquire quotes without executing or changing the paper book."""
import asyncio
import copy
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import AsyncMock,patch

from backend.auth import AuthStore
from backend.research_store import ResearchStore
from backend.stock_catalog import StockCatalog
from backend.minute_market import MinuteMarketService
from backend.paper_store import PaperStore
from backend.paper_trading import PaperTradingService
from backend.paper_rules import new_account,apply_trade,valid_quote
from backend.providers import ProviderError
from backend.news_cleaning import SHANGHAI
from test_paper_rules import cache
import test_paper_api as fixture

class PaperValuationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.auth=AuthStore(Path(self.directory.name)/'valuation.db');self.auth.initialize()
        self.records=ResearchStore(self.auth);self.records.initialize()
        self.catalog=StockCatalog(self.records)
        self.now=datetime(2026,10,23,10,30,tzinfo=SHANGHAI)
        self.fetch=AsyncMock(side_effect=lambda stock,period:cache(self.now,11)['candles'])
        self.minute=MinuteMarketService(self.records,clock=lambda:self.now,fetcher=self.fetch)
        self.paper=PaperTradingService(PaperStore(self.records),self.catalog,self.minute,clock=lambda:self.now)
        self.owner='one'
        self.seed()

    def seed(self):
        account=new_account();old=self.now.replace(hour=9,minute=31)
        for code in ['000001','600519']:
            account,_=apply_trade(account,self.catalog.get(code),'buy',100,valid_quote(cache(old,10),old),old)
            self.records.put('minute_market',code+':1',cache(old,10))
        self.records.put('paper_account','account',account,self.owner)
        self.before=copy.deepcopy(account)

    async def asyncTearDown(self):
        await self.minute.close();self.directory.cleanup()

    async def drain(self):
        if self.minute.tasks:await asyncio.gather(*list(self.minute.tasks.values()))

    async def test_snapshot_returns_cache_then_refreshes_all_holdings_without_trades_or_ledger_changes(self):
        gate=asyncio.Event()
        async def source(stock,period):
            await gate.wait();return cache(self.now,11)['candles']
        self.fetch.side_effect=source
        first=await self.paper.snapshot(self.owner)
        self.assertTrue(first['valuation_refreshing']);self.assertEqual(first['next_poll_seconds'],2)
        self.assertEqual([p['price_fen'] for p in first['positions']],[1000,1000])
        gate.set();await self.drain()
        updated=await self.paper.snapshot(self.owner)
        self.assertFalse(updated['valuation_refreshing']);self.assertEqual(updated['next_poll_seconds'],30)
        self.assertEqual([p['price_fen'] for p in updated['positions']],[1100,1100])
        self.assertEqual({c.args[0]['code'] for c in self.fetch.await_args_list},{'000001','600519'})
        self.assertEqual(self.paper.store.get_account(self.owner),self.before)
        self.assertEqual(self.paper.trades(self.owner)['items'],[])
        self.assertEqual(updated['cash_fen'],first['cash_fen'])

    async def test_concurrent_account_readers_share_existing_source_tasks(self):
        gate=asyncio.Event()
        async def source(*args):await gate.wait();return cache(self.now,11)['candles']
        self.fetch.side_effect=source
        replies=await asyncio.gather(*(self.paper.snapshot(self.owner) for _ in range(8)))
        self.assertTrue(all(r['valuation_refreshing'] for r in replies))
        self.assertEqual(set(self.minute.tasks),{'000001:1','600519:1'})
        gate.set();await self.drain();self.assertEqual(self.fetch.await_count,2)

    async def test_after_close_read_catches_up_to_close_without_unlocking_t_plus_one(self):
        self.now=self.now.replace(hour=17)
        self.fetch.side_effect=lambda *args:cache(self.now.replace(hour=15,minute=0),12)['candles']
        await self.paper.snapshot(self.owner);await self.drain()
        updated=await self.paper.snapshot(self.owner)
        self.assertTrue(all(p['price_as_of'].startswith('2026-10-23T15:00') for p in updated['positions']))
        self.assertTrue(all(p['sellable']==0 and p['locked']==100 for p in updated['positions']))
        self.assertFalse(updated['market']['can_trade'])
        self.assertFalse(updated['valuation_refreshing'])

    async def test_failed_quotes_keep_saved_prices_and_expose_backoff_without_retry_storm(self):
        self.fetch.side_effect=ProviderError('source unavailable')
        await self.paper.snapshot(self.owner);await self.drain()
        updated=await self.paper.snapshot(self.owner)
        self.assertEqual(set(updated['valuation_errors']),{'000001','600519'})
        self.assertEqual([p['price_fen'] for p in updated['positions']],[1000,1000])
        self.assertFalse(updated['valuation_refreshing'])
        await self.paper.snapshot(self.owner);await self.drain();self.assertEqual(self.fetch.await_count,2)
        self.assertEqual(self.paper.store.get_account(self.owner),self.before)

    async def test_refresh_revalues_current_book_when_position_is_sold_during_source_work(self):
        gate=asyncio.Event()
        async def source(*args):await gate.wait();return cache(self.now,11)['candles']
        self.fetch.side_effect=source
        await self.paper.snapshot(self.owner)
        changed=copy.deepcopy(self.before);changed['positions']={}
        self.records.put('paper_account','account',changed,self.owner)
        gate.set();await self.drain()
        self.assertEqual((await self.paper.snapshot(self.owner))['positions'],[])
        self.assertEqual(self.paper.store.get_account(self.owner),changed)

    async def test_empty_account_is_private_and_needs_no_quotes(self):
        result=await self.paper.snapshot('other-owner')
        self.assertEqual(result['positions'],[])
        self.assertFalse(result['valuation_refreshing']);self.fetch.assert_not_awaited()
        self.assertEqual(self.records.get('minute_market','000001:1')['candles'][-1]['close'],10)

    async def test_saved_holding_refreshes_without_fetching_exchange_catalog_again(self):
        del self.catalog.items['600519']
        with patch.object(self.catalog,'ensure',new_callable=AsyncMock) as listing:
            await self.paper.snapshot(self.owner);await self.drain()
            listing.assert_not_awaited()
        self.assertEqual({c.args[0]['code'] for c in self.fetch.await_args_list},{'000001','600519'})

class PaperValuationApiTests(unittest.TestCase):
    setUp=fixture.PaperAPITests.setUp
    tearDown=fixture.PaperAPITests.tearDown
    register=fixture.PaperAPITests.register

    def test_account_endpoint_uses_demand_snapshot_and_passes_explicit_refresh(self):
        from backend.app import app
        self.register();owner=self.client.get('/api/auth/me').json()['user']['id']
        with patch.object(app.state.paper_trading,'snapshot',new=AsyncMock(return_value={'positions':[],'valuation_refreshing':True,'next_poll_seconds':2})) as snapshot:
            result=self.client.get('/api/paper/account?refresh=true')
            self.assertEqual(result.status_code,200,result.text)
            snapshot.assert_awaited_once_with(owner,refresh=True)
            self.assertEqual(result.headers['cache-control'],'no-store')
            self.assertTrue(result.json()['valuation_refreshing'])
