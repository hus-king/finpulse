import asyncio
import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import AuthError, AuthStore, COOKIE_NAME, auth_error_handler
from backend.news_cleaning import SHANGHAI
from backend.paper_performance import PaperPerformanceService, PerformanceDataError, _corporate, _journal, _states, _valid_nav, build_comparison
from backend.paper_performance_api import router
from backend.paper_rules import new_account
from backend.paper_store import PaperStore
from backend.research_store import ResearchStore
from backend import providers


INDEX=[dict(date='2026-10-08',open=100.,close=110.),dict(date='2026-10-09',open=110.,close=90.),
       dict(date='2026-10-12',open=90.,close=120.)]


def ledger(*,prefix='a',buy=1000,sell=1100):
    initial=20_000_000;buy_gross=buy*100;sell_gross=sell*100
    first=dict(sequence=1,request_id=prefix+'1',executed_at='2026-10-08T10:07:00+08:00',code='000001',
               side='buy',quantity=100,price_fen=buy,gross_fen=buy_gross,fees_fen=200,
               cash_after_fen=initial-buy_gross-200,execution_mode='daily_open_replay')
    final=initial-buy_gross-200+sell_gross-220
    second=dict(sequence=2,request_id=prefix+'2',executed_at='2026-10-09T10:03:00+08:00',code='000001',
                side='sell',quantity=100,price_fen=sell,gross_fen=sell_gross,fees_fen=220,
                cash_after_fen=final,execution_mode='daily_open_replay')
    account={**new_account(),'cash_fen':final,'sequence':2,'total_fees_fen':420,'realized_pnl_fen':final-initial}
    return account,[first,second]


def dividends(code='000001'):
    #100shares×0.265CNY×80%=21.20CNY. Tax is already applied by the authoritative ledger.
    return [dict(event_id=code+'-ex',code=code,name='分红测试',type='dividend_ex',
                 effective_at='2026-10-09T09:00:00+08:00',cash_delta_fen=0,
                 receivable_delta_fen=2120,income_fen=0,corporate_revision=1,book_sequence=1),
            dict(event_id=code+'-pay',code=code,name='分红测试',type='cash_dividend',
                 effective_at='2026-10-12T15:00:00+08:00',cash_delta_fen=2120,
                 receivable_delta_fen=-2120,income_fen=2120,corporate_revision=2,
                 book_sequence=2,cash_after_fen=20_011_700)]


class ComparisonMathTests(unittest.TestCase):
    def test_first_day_open_anchor_includes_day_one_and_profit_rates_agree(self):
        account,trades=ledger();initial,ordered=_journal(dict(account=account,trades=trades))
        dates=['2026-10-08','2026-10-09','2026-10-12'];states=_states(initial,ordered,dates)
        result=build_comparison(initial,dates[0],dates,states,{},
            {'000001':{'2026-10-08':{'close':10.5}}},{r['date']:r for r in INDEX},'sh000300')
        self.assertEqual(result['points'][0]['strategy_profit_fen'],0)
        first=result['points'][1]
        self.assertEqual(first['strategy_profit_fen'],4800)
        self.assertEqual(first['benchmark_profit_fen'],2_000_000)
        self.assertAlmostEqual(first['benchmark_return_percent'],10.)
        self.assertEqual(result['points'][-1]['strategy_profit_fen'],9580)
        self.assertEqual(result['points'][-2]['strategy_profit_fen'],9580)  # Cash stays after liquidation.
        for point in result['points']:
            self.assertAlmostEqual(point['strategy_profit_fen']/initial*100,point['strategy_return_percent'])
            self.assertAlmostEqual(point['excess_return_percent'],point['strategy_return_percent']-point['benchmark_return_percent'])
            self.assertEqual(point['excess_profit_fen'],point['strategy_profit_fen']-point['benchmark_profit_fen'])

    def test_missing_close_is_waiting_not_zero_or_carried_price(self):
        account,trades=ledger();initial,ordered=_journal(dict(account=account,trades=trades));dates=['2026-10-08','2026-10-09']
        result=build_comparison(initial,dates[0],dates,_states(initial,ordered,dates),{}, {},
                                {r['date']:r for r in INDEX},'sh000300')
        self.assertIsNone(result['summary'])
        self.assertEqual(result['points'],[])
        self.assertEqual(result['missing'],{'date':'2026-10-08','codes':['000001']})
        with self.assertRaisesRegex(PerformanceDataError,'开盘价'):
            build_comparison(initial,dates[0],dates,_states(initial,ordered,dates),{}, {},
                             {'2026-10-08':{'close':100.}},'sh000300')

    def test_entire_sequence_not_a_last_page_and_book_consistency(self):
        account,trades=ledger()
        initial,ordered=_journal(dict(account=account,trades=trades+[dict(trades[0],sequence=99)]))
        self.assertEqual(len(ordered),2)
        with self.assertRaisesRegex(PerformanceDataError,'完整'):
            _journal(dict(account=account,trades=trades[1:]))
        bad=copy.deepcopy(trades);bad[0]['cash_after_fen']+=1
        with self.assertRaisesRegex(PerformanceDataError,'资金'):
            _journal(dict(account=account,trades=bad))

    def test_missing_later_own_close_truncates_both_paths_to_same_day(self):
        account,trades=ledger();trades[1]['executed_at']='2026-10-12T10:03:00+08:00'
        initial,ordered=_journal(dict(account=account,trades=trades));dates=['2026-10-08','2026-10-09','2026-10-12']
        result=build_comparison(initial,dates[0],dates,_states(initial,ordered,dates),{},
            {'000001':{'2026-10-08':{'close':10.5}}},{r['date']:r for r in INDEX},'sh000300')
        self.assertEqual(result['as_of'],'2026-10-08')
        self.assertEqual(result['missing']['date'],'2026-10-09')
        self.assertEqual(result['points'][-1]['strategy_profit_fen'],4800)
        self.assertEqual(result['points'][-1]['benchmark_profit_fen'],2_000_000)

    def test_dividend_ex_recognizes_net_income_once_and_payment_is_neutral(self):
        account,trades=ledger()
        for trade in trades:trade['code']='600219'
        account.update(cash_fen=20_011_700,receivable_fen=0,corporate_revision=2)
        snapshot=dict(account=account,trades=trades,corporate=dividends('600219'))
        initial,ordered=_journal(snapshot);events=_corporate(snapshot)
        dates=['2026-10-08','2026-10-09','2026-10-12'];states=_states(initial,ordered,dates,events)
        self.assertEqual(states['2026-10-09']['receivable_fen'],2120)
        self.assertEqual(states['2026-10-12']['receivable_fen'],0)
        self.assertEqual(states['2026-10-12']['cash_fen']-states['2026-10-09']['cash_fen'],2120)
        self.assertEqual(states['2026-10-09']['quantities'],{})  # Sold before payment, entitlement survives.
        result=build_comparison(initial,dates[0],dates,states,{},
            {'600219':{'2026-10-08':{'close':10.5}}},{r['date']:r for r in INDEX},'sh000300')
        self.assertEqual(result['points'][-2]['strategy_profit_fen'],11700)
        self.assertEqual(result['points'][-1]['strategy_profit_fen'],11700)
        row=dict(date='2026-10-09',sequence=2,cash_fen=20_009_580,receivable_fen=2120,
                 market_value_fen=0,equity_fen=20_011_700,positions={})
        self.assertEqual(_valid_nav(row,states['2026-10-09']),20_011_700)

    def test_corporate_revision_and_book_sequence_are_authoritative_bounds(self):
        account,trades=ledger();account.update(receivable_fen=2120,corporate_revision=1)
        events=dividends();snapshot=dict(account=account,trades=trades,corporate=events)
        _journal(snapshot)  # Future revision2 payment is not yet in the account.
        self.assertEqual(len(_corporate(snapshot)),1)
        bad=copy.deepcopy(snapshot);bad['corporate'][0]['book_sequence']=3
        with self.assertRaisesRegex(PerformanceDataError,'顺序'):_journal(bad)
        bad=copy.deepcopy(snapshot);bad['account']['corporate_revision']=2
        bad['corporate']=bad['corporate'][1:]
        with self.assertRaisesRegex(PerformanceDataError,'尚未完整'):_journal(bad)


class PerformanceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.auth=AuthStore(Path(self.temp.name)/'performance.db');self.auth.initialize()
        self.records=ResearchStore(self.auth);self.records.initialize()
        self.paper=SimpleNamespace(store=PaperStore(self.records),clock=lambda:self.now)
        self.now=datetime(2026,10,13,10,tzinfo=SHANGHAI)
        self.calls=[]
        async def fetch(kind,code,start,end):
            self.calls.append((kind,code,start,end))
            return {'rows':INDEX if kind=='index_history' else [dict(date='2026-10-08',close=10.5)]}
        self.fetch=fetch;self.service=PaperPerformanceService(self.paper,fetcher=fetch)
        self.addAsyncCleanup(self.service.close)

    def put(self,owner='alice',**changes):
        account,trades=ledger(**changes)
        with self.auth.transaction() as conn:
            conn.execute('DELETE FROM research_records WHERE owner=? AND namespace IN (?,?,?,?,?)',
                         (owner,'paper_account','paper_trade','paper_replay','n2_daily_nav','paper_corporate'))
        self.records.put('paper_account','account',account,owner)
        for trade in trades:self.records.put('paper_trade',trade['request_id'],trade,owner)
        return account,trades

    async def result(self,owner='alice',benchmark='sh000300'):
        for _ in range(100):
            result=await self.service.performance(owner,benchmark)
            if result['status']!='loading':return result
            await asyncio.sleep(.02)
        self.fail('Performance task did not finish')

    def nav(self,owner='alice'):
        self.records.put('paper_replay','active',dict(replay_id='replay-a',payload_sha256='oldhash',fills=2,
            start_date='2026-10-08',end_date='2026-10-09',note='历史段为日线开盘价近似成交'),owner)
        self.records.put('n2_daily_nav','2026-10-08',dict(date='2026-10-08',cash_fen=19_899_800,
            market_value_fen=105000,equity_fen=20_004_800,sequence=1,
            positions={'000001':dict(quantity=100,cost_fen=100200,close_fen=1050,price_as_of='2026-10-08T15:00:00+08:00')}),owner)
        self.records.put('n2_daily_nav','2026-10-09',dict(date='2026-10-09',cash_fen=20_009_580,
            market_value_fen=0,equity_fen=20_009_580,sequence=2,positions={}),owner)

    async def test_sales_fees_cash_and_owner_isolation(self):
        self.put();self.put('bob',prefix='b',buy=1200)
        alice=await self.result();bob=await self.result('bob')
        self.assertEqual(alice['status'],'ready')
        self.assertEqual(alice['as_of'],'2026-10-12')
        self.assertEqual(alice['summary']['strategy_profit_fen'],9580)
        self.assertEqual(bob['summary']['strategy_profit_fen'],-10420)
        self.assertNotEqual(alice['summary'],bob['summary'])
        with self.auth.connection() as conn:
            account=json.loads(conn.execute('SELECT payload FROM research_records WHERE owner=? AND namespace=?',('alice','paper_account')).fetchone()['payload'])
        self.assertEqual(account['sequence'],2)
        self.assertEqual(account['cash_fen'],20_009_580)

    async def test_saved_n2_nav_avoids_stock_fetch_and_preserves_first_day(self):
        self.put();self.nav();self.now=datetime(2026,10,12,10,tzinfo=SHANGHAI)
        result=await self.result()
        self.assertEqual(result['as_of'],'2026-10-09')
        self.assertEqual(result['summary']['strategy_profit_fen'],9580)
        self.assertEqual(result['points'][1]['benchmark_profit_fen'],2_000_000)
        self.assertTrue(all(kind=='index_history' for kind,*_ in self.calls))
        self.assertEqual(result['provenance']['cached_nav_days'],2)

    async def test_common_cutoff_truncates_both_when_benchmark_behind(self):
        self.put();self.nav();self.now=datetime(2026,10,12,10,tzinfo=SHANGHAI)
        async def stale(kind,code,start,end):return {'rows':INDEX[:1]}
        self.service.fetcher=stale
        result=await self.result()
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['as_of'],'2026-10-08')
        self.assertEqual(result['points'][-1]['strategy_profit_fen'],4800)
        self.assertEqual(result['points'][-1]['benchmark_return_percent'],10.)
        self.assertEqual(result['requested_as_of'],'2026-10-09')

    async def test_today_and_future_closes_cannot_enter_comparison(self):
        self.put();self.now=datetime(2026,10,12,13,tzinfo=SHANGHAI)
        result=await self.result()
        self.assertEqual(result['as_of'],'2026-10-09')
        self.assertTrue(all(p['trading_date']<='2026-10-09' for p in result['points']))
        self.assertEqual(result['summary']['benchmark_return_percent'],-10.)

    async def test_saved_nav_cannot_reference_future_prices(self):
        self.put();self.nav();self.now=datetime(2026,10,12,10,tzinfo=SHANGHAI)
        row=self.records.get('n2_daily_nav','2026-10-08','alice')
        row['positions']['000001']['price_as_of']='2026-10-09T15:00:00+08:00'
        self.records.put('n2_daily_nav','2026-10-08',row,'alice')
        result=await self.result()
        self.assertEqual(result['status'],'waiting')
        self.assertIsNone(result['summary'])
        self.assertIn('后来',result['reason'])

    async def test_same_owner_same_sequence_reset_invalidates_curve(self):
        self.put();first=await self.result()
        self.put(prefix='replacement',buy=1200)
        second=await self.result()
        self.assertEqual(first['summary']['strategy_profit_fen'],9580)
        self.assertEqual(second['summary']['strategy_profit_fen'],-10420)

    async def test_same_trade_sequence_new_corporate_revision_invalidates_curve(self):
        account,_=self.put();first=await self.result()
        self.records.put('paper_corporate','ex',dividends()[0],'alice')
        account.update(receivable_fen=2120,corporate_revision=1)
        self.records.put('paper_account','account',account,'alice')
        second=await self.result()
        self.assertEqual(account['sequence'],2)
        self.assertEqual(first['summary']['strategy_profit_fen'],9580)
        self.assertEqual(second['summary']['strategy_profit_fen'],11700)
        self.records.put('paper_corporate','pay',dividends()[1],'alice')
        account.update(cash_fen=20_011_700,receivable_fen=0,corporate_revision=2)
        self.records.put('paper_account','account',account,'alice')
        paid=await self.result()
        self.assertEqual(paid['summary']['strategy_profit_fen'],11700)
        self.put('bob',prefix='b')
        bob=await self.result('bob')
        self.assertEqual(bob['summary']['strategy_profit_fen'],9580)

    async def test_cached_n2_nav_includes_receivable_and_paid_cash_without_stock_fetch(self):
        account,_=self.put();self.nav()
        account.update(cash_fen=20_011_700,receivable_fen=0,corporate_revision=2)
        self.records.put('paper_account','account',account,'alice')
        for event in dividends():self.records.put('paper_corporate',event['event_id'],event,'alice')
        row=self.records.get('n2_daily_nav','2026-10-09','alice')
        row.update(receivable_fen=2120,equity_fen=20_011_700)
        self.records.put('n2_daily_nav','2026-10-09',row,'alice')
        self.records.put('n2_daily_nav','2026-10-12',dict(date='2026-10-12',cash_fen=20_011_700,
            receivable_fen=0,market_value_fen=0,equity_fen=20_011_700,sequence=2,positions={}),'alice')
        replay=self.records.get('paper_replay','active','alice');replay['end_date']='2026-10-12'
        self.records.put('paper_replay','active',replay,'alice')
        result=await self.result()
        self.assertEqual(result['summary']['strategy_profit_fen'],11700)
        self.assertEqual(result['provenance']['cached_nav_days'],3)
        self.assertTrue(all(kind=='index_history' for kind,*_ in self.calls))

    async def test_missing_own_closes_and_provider_failures_are_visible(self):
        self.put()
        async def missing(kind,code,start,end):return {'rows':INDEX if kind=='index_history' else []}
        self.service.fetcher=missing
        result=await self.result()
        self.assertEqual(result['status'],'waiting')
        self.assertIsNone(result['summary'])
        self.assertIn('持仓收盘价待补齐',result['reason'])
        self.assertEqual(result['points'],[])
        await self.service.close()
        with self.auth.transaction() as conn:
            conn.execute('DELETE FROM research_records WHERE namespace=?',('paper_performance_prices',))
        async def failure(*args):raise providers.ProviderError('PRIVATE-TOKEN')
        self.service=PaperPerformanceService(self.paper,fetcher=failure);self.addAsyncCleanup(self.service.close)
        result=await self.result('bob')
        self.assertEqual(result['status'],'empty')
        self.put('bob')
        result=await self.result('bob')
        self.assertNotIn('PRIVATE-TOKEN',result['reason'])

    async def test_concurrent_benchmarks_use_at_most_two_network_requests(self):
        self.put();self.nav();self.now=datetime(2026,10,12,10,tzinfo=SHANGHAI)
        active=0;peak=0
        async def slow(kind,code,start,end):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            try:await asyncio.sleep(.1);return {'rows':INDEX}
            finally:active-=1
        self.service.fetcher=slow
        results=await asyncio.gather(*(self.result(benchmark=c) for c in ('sh000300','sh000001','sz399006')))
        self.assertTrue(all(r['status']=='ready' for r in results))
        self.assertEqual(peak,2)


class PerformanceAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.auth=AuthStore(Path(self.temp.name)/'api.db');self.auth.initialize()
        self.alice,self.token,_=self.auth.register('performance_a','A','Test-password-123!')
        self.bob,self.bob_token,_=self.auth.register('performance_b','B','Test-password-123!')
        app=FastAPI();app.state.auth_store=self.auth
        async def read(owner,benchmark):return {'owner_seen':owner,'benchmark_seen':benchmark}
        self.service=SimpleNamespace(performance=AsyncMock(side_effect=read))
        app.state.paper_performance=self.service;app.include_router(router);app.add_exception_handler(AuthError,auth_error_handler)
        self.client=TestClient(app);self.addCleanup(self.client.close)

    def test_authentication_owner_binding_and_private_headers(self):
        self.assertEqual(self.client.get('/api/paper/performance').status_code,401)
        self.client.cookies.set(COOKIE_NAME,self.token,path='/api')
        response=self.client.get('/api/paper/performance?owner='+self.bob['id'])
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(response.json()['owner_seen'],self.alice['id'])
        self.assertEqual(response.json()['benchmark_seen'],'sh000300')
        self.client.cookies.set(COOKIE_NAME,self.bob_token,path='/api')
        self.assertEqual(self.client.get('/api/paper/performance?benchmark=sz399006').json()['owner_seen'],self.bob['id'])
        self.assertEqual(self.client.get('/api/paper/performance?benchmark=600028').status_code,422)


if __name__=='__main__':
    unittest.main()
