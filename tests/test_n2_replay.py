import copy
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

import numpy as np
import pandas as pd

from backend.auth import AuthStore
from backend.news_cleaning import SHANGHAI
from backend.paper_rules import apply_trade, new_account, PaperError
from backend.paper_store import PaperStore
from backend.research_store import ResearchStore
from backend.quant.store import QuantStore
from backend.quant.n2_book import (digest, fill_order, make_plan, model_fees,
                                   prepare_orders, refresh_entry_factors)
from backend.quant.n2_maintenance import import_n2_replay, verify_replay
from backend.quant.n2_policy import initial_state, N2_STRATEGY_ID
from backend.quant.n2_replay import replay

NOW = datetime(2026,11,5,12,0,tzinfo=SHANGHAI)


def inputs(_base, _overlay, day, *, extra_codes=()):
    calendar = pd.bdate_range('2025-01-01',day).strftime('%Y-%m-%d').tolist()
    calendar = [d for d in calendar if not '2026-10-01' <= d <= '2026-10-07']
    past = [d for d in calendar if d < day]
    rows, histories, daily = [], {}, {}
    for i, code in enumerate(('600000','600001')):
        prices = 10*np.exp(np.sin(np.arange(len(past))*.23)*(i+1)*.01)
        raw = [dict(date=d,open=float(p),high=float(p)+.1,low=float(p)-.1,
                    close=float(p),volume=1e7,factor=2.) for d,p in zip(past,prices)]
        histories[code] = dict(raw=raw, adjusted=[dict(b,**{k:b[k]*2 for k in ('open','close','high','low')}) for b in raw])
        rows.append(dict(code=code,name='股票'+code,peg=.2+i*.1,industry_l1='行业'+str(i)))
        daily[code] = {day:dict(date=day,open=10.,close=10.1,high=10.2,low=9.9,volume=1e7,factor=2.)}
    return dict(date=day,signal_date=past[-1],calendar=past,rows=rows,histories=histories,daily=daily,
                sectors={r['code']:r['industry_l1'] for r in rows},dividends={},
                provenance=dict(source_hashes={'fixture':'fixed'},cutoff=past[-1]))


def payload():
    return replay('', '', '2026-10-08','2026-10-09','test-owner:fixed-replay',input_loader=inputs)


class N2BookTests(unittest.TestCase):
    def test_fees_are_a_floor_not_added_twice_and_small_fill_fees_are_cumulative(self):
        self.assertEqual(model_fees(0,'buy')['fees_fen'],0)
        self.assertEqual(model_fees(1_000_000,'buy')['fees_fen'],2000)
        account,state = new_account(),initial_state()
        data=inputs('','','2026-10-08');plan,state=make_plan(account,state,data,{'600000':1000,'600001':1000},mode='live_minute',created_at='2026-10-08T10:00:00+08:00')
        prepare_orders(plan,'partial-test','buy',account);order=plan['orders'][0]
        when=datetime(2026,10,8,10,8,tzinfo=SHANGHAI)
        account,state,a=fill_order(account,state,plan,order,1000,when,capacity=17,source='fixture',fetched_at=when.isoformat(),bar_id='1',partial=True,factor=2.)
        account,state,b=fill_order(account,state,plan,order,1100,when,capacity=13,source='fixture',fetched_at=when.isoformat(),bar_id='2',partial=True,factor=2.)
        self.assertEqual(a['quantity'],17);self.assertEqual(b['quantity'],13)
        self.assertEqual(a['fees_fen']+b['fees_fen'],model_fees(17_000+14_300,'buy')['fees_fen'])
        entry=state['entries'][order['code']]
        self.assertAlmostEqual(entry['price_fen'],31_300/30)
        self.assertAlmostEqual(entry['adjusted_anchor_fen'],2*31_300/30)
        # Parent partial fills are internal; public/manual lot rules remain intact.
        with self.assertRaises(PaperError):
            apply_trade(new_account(),dict(code='600000',name='股票'),'buy',17,
                        dict(price_fen=1000,as_of=when.isoformat(),fetched_at=when.isoformat(),source='fixture'),when)

    def test_unfilled_exit_does_not_free_a_slot_for_buy(self):
        result=payload();account=copy.deepcopy(result['account']);state=copy.deepcopy(result['state'])
        data=inputs('','','2026-10-12');state['pending']['600000']='price_hard'
        plan,state=make_plan(account,state,data,{'600000':1000,'600001':1000},mode='daily_open_replay',created_at='2026-10-12T09:30:00+08:00')
        self.assertIn('600000',plan['exit_reasons'])
        prepare_orders(plan,'same-book','buy',account)
        self.assertTrue(all(o['code']!='600000' or o['side']=='sell' for o in plan['orders']))

    def test_entry_factor_resolution_keeps_original_raw_price(self):
        state=initial_state();state['entries']['600000']=dict(date='2026-10-08',price_fen=1111,factor_pending=True,adjusted_anchor_fen=1111.)
        result=refresh_entry_factors(state,inputs('','','2026-10-09')['histories'],'2026-10-08')
        self.assertEqual(result['entries']['600000']['price_fen'],1111)
        self.assertEqual(result['entries']['600000']['adjusted_anchor_fen'],2222)

    def test_daily_close_does_not_change_open_orders_or_fills(self):
        original=payload()
        def changed(*a,**kw):
            result=inputs(*a,**kw)
            for records in result['daily'].values():
                for bar in records.values():bar.update(close=999.,high=1000.,low=.01)
            return result
        mutated=replay('','','2026-10-08','2026-10-09','test-owner:fixed-replay',input_loader=changed)
        keys=('code','side','quantity','price_fen','fees_fen','request_id')
        self.assertEqual([{k:t[k] for k in keys} for t in original['trades']],
                         [{k:t[k] for k in keys} for t in mutated['trades']])
        self.assertEqual(verify_replay(original,now=NOW),original['account'])

    def test_corrupted_money_and_missing_day_are_rejected_even_with_new_hash(self):
        result=payload();bad=copy.deepcopy(result);bad['account']['cash_fen']+=1;bad['sha256']=digest({k:v for k,v in bad.items() if k!='sha256'})
        with self.assertRaisesRegex(ValueError,'期末账户'):verify_replay(bad,now=NOW)
        bad=copy.deepcopy(result);bad['daily_nav'][0]['equity_fen']+=1;bad['sha256']=digest({k:v for k,v in bad.items() if k!='sha256'})
        with self.assertRaisesRegex(ValueError,'资产恒等式'):verify_replay(bad,now=NOW)

    def test_unavailable_entry_factor_stays_pending_until_actual_entry_day_arrives(self):
        def loader(*a,**kw):
            data=inputs(*a,**kw)
            for bars in data['daily'].values():
                for bar in bars.values():bar.pop('factor',None)
            return data
        first=replay('','','2026-10-08','2026-10-08','pending-factor',input_loader=loader)
        self.assertTrue(all(e.get('factor_pending') for e in first['state']['entries'].values()))
        later=replay('','','2026-10-08','2026-10-09','pending-factor',input_loader=loader)
        self.assertTrue(all(not e.get('factor_pending') for e in later['state']['entries'].values()))

    def test_halt_uses_last_actual_close_without_fabricating_final_day_price(self):
        def loader(*a,**kw):
            data=inputs(*a,**kw)
            if data['date']=='2026-10-09':
                for bars in data['daily'].values():bars.clear()
            return data
        value=replay('','','2026-10-08','2026-10-09','halt-close',input_loader=loader)
        self.assertTrue(all(v['as_of'][:10]=='2026-10-08' for v in value['closing_prices'].values()))
        self.assertEqual(verify_replay(value,now=NOW),value['account'])


class N2ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.auth=AuthStore(Path(self.temp.name)/'db');self.auth.initialize()
        self.records=ResearchStore(self.auth);self.records.initialize();self.paper=PaperStore(self.records)
        self.owner=self.auth.register('n2_owner','持有人','Test-password-123!')[0]['id']
        self.other=self.auth.register('n2_other','另一人','Test-password-123!')[0]['id']
        self.service=SimpleNamespace(store=QuantStore(self.paper),paper=SimpleNamespace(store=self.paper))
        self.records.put('quant_config','config',dict(mode='algorithm',strategy_id='official-peg-buffered-v1',revision=5),self.owner)
        self.old=new_account();self.old['cash_fen']=12345678
        self.records.put('paper_account','account',self.old,self.owner)
        self.records.put('paper_account','account',dict(sentinel=True),self.other)
        self.value=payload();self.ident=str(uuid.uuid4())

    def tearDown(self):self.temp.cleanup()

    def run_import(self):
        return import_n2_replay(self.service,self.owner,5,self.ident,self.value,now=NOW)

    def test_owner_atomic_archive_idempotence_and_handover(self):
        result=self.run_import();self.assertEqual(result['revision'],6)
        self.assertEqual(self.paper.get_account(self.owner),self.value['account'])
        self.assertEqual(self.records.get('paper_account','account',self.other),{'sentinel':True})
        archive=self.records.get('paper_replay_archive',self.ident,self.owner)
        self.assertTrue(any(r['namespace']=='paper_account' and r['payload']==self.old for r in archive['records']))
        self.assertEqual(self.service.store.config(self.owner)['strategy_id'],N2_STRATEGY_ID)
        self.assertEqual(self.records.get('paper_replay','active',self.owner)['execution_mode'],'daily_open_replay')
        book=self.paper.get_account(self.owner);book['cash_fen']+=17;self.records.put('paper_account','account',book,self.owner)
        self.assertEqual(self.run_import(),result);self.assertEqual(self.paper.get_account(self.owner),book)
        self.assertEqual(self.records.get('quant_decision','2026-11-05:0000000006',self.owner)['execution_mode'],'handover')

    def test_revision_and_active_lease_prevent_changes(self):
        self.records.put('quant_config','config',dict(revision=7),self.owner)
        with self.assertRaises(PaperError):self.run_import()
        self.assertEqual(self.paper.get_account(self.owner),self.old)
        self.records.put('quant_config','config',dict(revision=5),self.owner)
        token=self.records.claim('quant:'+self.owner,300)
        with self.assertRaises(PaperError):self.run_import()
        self.assertEqual(self.paper.get_account(self.owner),self.old)
        self.records.release('quant:'+self.owner,token)

    def test_write_failure_rolls_back_archive_removal_and_new_book(self):
        write=self.paper._put
        def fail(conn,namespace,key,owner,value):
            if namespace=='paper_trade':raise RuntimeError('injected')
            return write(conn,namespace,key,owner,value)
        with patch.object(self.paper,'_put',side_effect=fail):
            with self.assertRaises(RuntimeError):self.run_import()
        self.assertEqual(self.paper.get_account(self.owner),self.old)
        self.assertIsNone(self.records.get('paper_replay_archive',self.ident,self.owner))
        self.assertEqual(self.service.store.config(self.owner)['revision'],5)
