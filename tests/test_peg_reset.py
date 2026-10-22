"""Archived paper-account reset and owner/revision-scoped opening execution."""
import asyncio
import copy
import unittest
import uuid
from unittest.mock import patch, AsyncMock

import test_paper_api as fixture
from test_paper_rules import cache
import test_peg_strategy as peg_fixture
CODES=peg_fixture.CODES
from backend.paper_rules import PaperError, new_account
from backend.quant.maintenance import reset_peg_account


class PegResetTests(unittest.TestCase):
    setUp=fixture.PaperAPITests.setUp
    tearDown=fixture.PaperAPITests.tearDown
    register=fixture.PaperAPITests.register
    ready=peg_fixture.PegExecutionTests.ready
    enable=peg_fixture.PegExecutionTests.enable

    def reset(self, expected=1, ident=None):
        return reset_peg_account(self.service,self.owner,expected,ident or str(uuid.uuid4()),now=self.now)

    def seed_account(self):
        self.ready()
        trade=self.client.post('/api/paper/trades',headers=self.headers,json={'request_id':str(uuid.uuid4()),'code':'000001','side':'buy','quantity':100})
        self.assertEqual(trade.status_code,200,trade.text)
        self.enable()
        self.records.put('quant_day','2026-10-09',{'spent_fen':6_000_000},self.owner)
        self.records.put('quant_runtime','state',{'retry_at':9_999_999_999},self.owner)
        self.records.put('quant_decision','2026-10-09:0000000001',{'status':'completed','revision':1,'date':'2026-10-09'},self.owner)
        self.records.put('watchlist','list',['000001'],self.owner)
        self.records.put('paper_account','account',{'sentinel':True},'another-owner')
        return trade.json()

    def test_reset_archives_ledger_clears_positions_pnl_and_budgets_and_keeps_owner_scope(self):
        trade=self.seed_account();before=self.service.paper.store.get_account(self.owner)
        report=self.reset();cfg=self.service.store.config(self.owner)
        self.assertEqual(self.service.paper.store.get_account(self.owner),new_account())
        self.assertEqual(self.service.paper.trades(self.owner)['items'],[])
        self.assertEqual(self.service.store.decisions(self.owner)['items'],[])
        self.assertIsNone(self.records.get('quant_day','2026-10-09',self.owner))
        self.assertIsNone(self.records.get('quant_runtime','state',self.owner))
        self.assertEqual(cfg['mode'],'algorithm');self.assertEqual(cfg['strategy_id'],'official-low-peg-v1')
        self.assertEqual(cfg['strategy_config'],{'top_n':5,'group':'all'})
        self.assertEqual(cfg['revision'],2);self.assertEqual(cfg['peak_equity_fen'],20_000_000)
        archive=self.records.get('paper_reset_archive',report['reset_id'],self.owner)
        self.assertTrue(any(r['namespace']=='paper_account' and r['payload']==before for r in archive['records']))
        self.assertTrue(any(r['namespace']=='paper_trade' and r['payload']['request_id']==trade['request_id'] for r in archive['records']))
        self.assertEqual(self.records.get('watchlist','list',self.owner),['000001'])
        self.assertEqual(self.records.get('paper_account','account','another-owner'),{'sentinel':True})
        self.assertIsNotNone(self.records.get('minute_market','000001:1'))
        self.assertIn('09:30',self.service.state(self.owner)['next_check'])

    def test_reset_replay_is_idempotent_and_does_not_reset_a_later_balance(self):
        self.seed_account();ident=str(uuid.uuid4());first=self.reset(ident=ident)
        saved=new_account();saved['cash_fen']=19_000_000
        self.records.put('paper_account','account',saved,self.owner)
        second=self.reset(ident=ident)
        self.assertEqual(first,second)
        self.assertEqual(self.service.paper.store.get_account(self.owner),saved)
        self.assertEqual(self.service.store.config(self.owner)['revision'],2)
        with self.assertRaises(PaperError):self.reset(expected=0,ident=ident)

    def test_failed_reset_rolls_back_archive_config_cash_and_trade_deletion(self):
        self.seed_account();before=copy.deepcopy(self.service.paper.store.get_account(self.owner))
        original=self.service.paper.store._put
        def fail(conn,namespace,*args):
            if namespace=='paper_account':raise RuntimeError('simulated reset write failure')
            return original(conn,namespace,*args)
        ident=str(uuid.uuid4())
        with patch.object(self.service.paper.store,'_put',side_effect=fail),self.assertRaises(RuntimeError):self.reset(ident=ident)
        self.assertEqual(self.service.paper.store.get_account(self.owner),before)
        self.assertEqual(len(self.service.paper.trades(self.owner)['items']),1)
        self.assertEqual(self.service.store.config(self.owner)['revision'],1)
        self.assertIsNone(self.records.get('paper_reset_archive',ident,self.owner))

    def test_active_lease_or_stale_revision_rejects_before_any_ledger_change(self):
        self.seed_account();before=copy.deepcopy(self.service.paper.store.get_account(self.owner))
        with self.assertRaises(PaperError):self.reset(expected=0)
        token=self.records.claim('quant:'+self.owner,300)
        try:
            with self.assertRaises(PaperError) as rejected:self.reset()
            self.assertEqual(rejected.exception.code,'AUTOMATION_BUSY')
        finally:self.records.release('quant:'+self.owner,token)
        self.assertEqual(self.service.paper.store.get_account(self.owner),before)
        self.assertEqual(self.records.list('paper_reset_archive',owner=self.owner),[])

    def test_reset_account_opens_five_positions_after_0930_once_with_existing_risk_limits(self):
        self.seed_account();self.reset();self.now=self.now.replace(hour=9,minute=31)
        for code in CODES:self.records.put('minute_market',code+':1',cache(self.now,10))
        asyncio.run(self.service.tick())
        trades=self.service.paper.trades(self.owner)['items']
        self.assertEqual(len(trades),5,self.service.state(self.owner))
        self.assertEqual({r['code'] for r in trades},set(CODES[:5]))
        self.assertEqual({r['sequence'] for r in trades},set(range(1,6)))
        self.assertLessEqual(sum(r['gross_fen'] for r in trades),6_000_000)
        asyncio.run(self.service.tick())
        self.assertEqual(len(self.service.paper.trades(self.owner)['items']),5)
        self.assertNotIn('09:30',self.service.state(self.owner)['next_check'])

    def test_other_accounts_and_changed_or_expired_markers_keep_10am_start(self):
        self.ready();self.enable();self.now=self.now.replace(hour=9,minute=31)
        asyncio.run(self.service.tick());self.assertFalse(self.service.store.decisions(self.owner)['items'])
        self.reset();marker=self.records.get('quant_opening','once',self.owner)
        for changes in [{'trade_date':'2026-10-08'},{'config_revision':99},{'strategy_id':'official-multifactor-v1'}]:
            self.records.put('quant_opening','once',{**marker,**changes},self.owner)
            asyncio.run(self.service.tick());self.assertFalse(self.service.store.decisions(self.owner)['items'])
        self.records.put('quant_opening','once',marker,self.owner)
        self.service.configure(self.owner,'algorithm','official-low-peg-v1',2,strategy_config={'top_n':3,'group':'all'})
        asyncio.run(self.service.tick());self.assertFalse(self.service.store.decisions(self.owner)['items'])

    def test_opening_marker_never_overrides_closed_market_or_quote_validation(self):
        self.ready();self.enable();self.reset();self.now=self.now.replace(hour=9,minute=29)
        asyncio.run(self.service.tick());self.assertFalse(self.service.store.decisions(self.owner)['items'])
        self.now=self.now.replace(minute=31)
        with patch.object(self.service,'fresh',new=AsyncMock(side_effect=PaperError('QUOTE_STALE','delayed quote'))):asyncio.run(self.service.tick())
        self.assertFalse(self.service.paper.trades(self.owner)['items'])
        runtime=self.service.state(self.owner)['runtime']
        import time
        self.assertLessEqual(runtime['retry_at']-time.time(),61)
        self.assertGreater(runtime['retry_at']-time.time(),0)

    def test_old_order_retry_after_reset_cannot_spend_new_account_money(self):
        trade=self.seed_account();self.reset()
        self.service.configure(self.owner,'manual',None,2)
        response=self.client.post('/api/paper/trades',headers=self.headers,json={'request_id':trade['request_id'],'code':'000001','side':'buy','quantity':100})
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()['code'],'ACCOUNT_RESET')
        self.assertEqual(self.service.paper.store.get_account(self.owner),new_account())

    def test_old_revision_cannot_write_retry_state_after_reset(self):
        self.seed_account();self.reset()
        self.assertFalse(self.service.store.save_runtime(self.owner,1,{'status':'waiting','retry_at':9_999_999_999}))
        self.assertIsNone(self.records.get('quant_runtime','state',self.owner))
        self.assertTrue(self.service.store.save_runtime(self.owner,2,{'status':'ok'}))
        self.assertEqual(self.records.get('quant_runtime','state',self.owner)['revision'],2)

    def test_opening_non_quote_failures_retain_five_minute_backoff(self):
        import time
        self.ready();self.enable();self.reset();self.now=self.now.replace(hour=9,minute=31)
        with patch.object(self.service,'fresh',new=AsyncMock(side_effect=PaperError('QUANT_RISK_LIMIT','risk limit'))):asyncio.run(self.service.tick())
        runtime=self.service.state(self.owner)['runtime']
        self.assertGreater(runtime['retry_at']-time.time(),290)
