import asyncio
import copy
import unittest
from unittest.mock import patch

import test_peg_strategy as peg_fixture
from test_peg_strategy import Records, snapshot, CODES, NOW
from backend.quant.buffered_peg import (
    BUFFERED_PEG_ID, BufferedPegStrategy, adjusted_entry, cooldown_active,
    plan_selection, seed_state, update_fill_state,
)


def rows(count=15):
    return [dict(code=f'600{i:03}',name=f'股票{i}',peg=(i+1)/10,group='非金融',status='合格') for i in range(count)]


class BufferedRulesTests(unittest.TestCase):
    def test_decision_mark_normalizes_timezones_and_excludes_later_bars(self):
        from backend.quant.buffered_execution import decision_mark
        from test_paper_rules import cache
        item=cache(NOW,10);item['candles'][0]['date']='2026-10-09T02:00:00+00:00'
        future={**item['candles'][0],'date':'2026-10-09T02:01:00+00:00','close':99,'high':99}
        item['candles'].append(future)
        self.assertEqual(decision_mark(item,NOW.replace(minute=2)),1000)
        item['candles']=[future]
        with self.assertRaises(ValueError):decision_mark(item,NOW.replace(minute=2))

    def test_uses_valid_pre_day_snapshot_and_fixed_configuration(self):
        strategy=BufferedPegStrategy().prepare(Records(snapshot()),{},'2026-10-08',NOW)
        self.assertEqual(strategy.snapshot['snapshot_id'],'snapshot-one')
        with self.assertRaises(ValueError):strategy.validate_config({'top_n':5})
        with self.assertRaises(ValueError):strategy.prepare(Records(None),{},'2026-10-08',NOW)

    def test_sector_candidate_quota_precedes_top9_and_hold_quota(self):
        pool=rows();sectors={r['code']:('电子' if i<7 else '行业'+str(i)) for i,r in enumerate(pool)}
        plan=plan_selection(pool,{}, {},sectors,'2026-10-09')
        self.assertEqual(plan['active_candidates'][5]['code'],pool[7]['code'])
        self.assertEqual(plan['selected'],[pool[i]['code'] for i in [0,7,8]])
        self.assertEqual(len(plan['active_candidates']),9)

    def test_retention_top9_and_migration_reduces_five_to_three_by_rank(self):
        pool=rows();sectors={r['code']:str(i) for i,r in enumerate(pool)}
        held={pool[i]['code']:{} for i in [0,2,4,7,10]}
        plan=plan_selection(pool,held,{},sectors,'2026-10-09')
        self.assertEqual(plan['selected'],[pool[i]['code'] for i in [0,2,4]])
        self.assertEqual(set(plan['exit_reasons']),{pool[7]['code'],pool[10]['code']})
        self.assertEqual(plan_selection(pool,{pool[8]['code']:{}},{},sectors,'2026-10-09')['selected'][0],pool[8]['code'])

    def test_pending_price_exits_and_cooldown_are_excluded_before_ranking(self):
        pool=rows();sectors={r['code']:str(i) for i,r in enumerate(pool)}
        state={'pending':{pool[0]['code']:'price_hard'},'cooldowns':{pool[1]['code']:'2026-10-08'}}
        plan=plan_selection(pool,{pool[0]['code']:{}},state,sectors,'2026-10-09')
        self.assertEqual(plan['active_candidates'][0]['code'],pool[2]['code'])
        self.assertEqual(plan['exit_reasons'][pool[0]['code']],'price_hard')

    def test_unknown_sector_is_not_silently_treated_as_its_own_sector(self):
        with self.assertRaisesRegex(ValueError,'一级行业'):
            plan_selection(rows(3),{},{},{},'2026-10-09')

    def test_cooldown_counts_ten_complete_sessions_weekends_and_holidays(self):
        self.assertTrue(cooldown_active('2026-09-30','2026-10-08'))
        self.assertTrue(cooldown_active('2026-10-09','2026-10-23'))
        self.assertFalse(cooldown_active('2026-10-09','2026-10-26'))

    def test_entry_anchor_uses_first_continuous_entry_not_average_or_topup(self):
        account={'positions':{'600000':{'quantity':150}},'sequence':3}
        trades=[dict(code='600000',side=s,quantity=q,price_fen=p,executed_at=d+'T10:00:00+08:00') for s,q,p,d in
                [('buy',100,1000,'2026-10-08'),('buy',100,1200,'2026-10-09'),('sell',50,1300,'2026-10-12')]]
        state=seed_state(trades,account)
        self.assertEqual(state['entries']['600000'],{'date':'2026-10-08','price_fen':1000})
        state['pending']['600000']='price_hard'
        before={'positions':{'600000':{'quantity':150}}};partial={'positions':{'600000':{'quantity':100}},'sequence':4}
        update_fill_state(state,before,partial,{**trades[-1],'executed_at':'2026-10-13T10:02:00+08:00'})
        self.assertNotIn('600000',state['cooldowns']);self.assertIn('600000',state['pending'])
        update_fill_state(state,partial,{'positions':{},'sequence':5},{**trades[-1],'executed_at':'2026-10-14T10:02:00+08:00'})
        self.assertEqual(state['cooldowns']['600000'],'2026-10-14');self.assertNotIn('600000',state['entries'])
        with self.assertRaises(ValueError):seed_state([],account)

    def test_entry_adjustment_handles_affine_cash_and_scale_rebases(self):
        raw=[dict(date='2026-10-08',open=9.5,close=10,high=10.5,low=9)]
        for slope,offset in [(1,-.5),(.5,-.2),(1,0)]:
            adj=[{**b,**{k:b[k]*slope+offset for k in ['open','close','high','low']}} for b in raw]
            item={'raw':raw,'adjusted':adj}
            self.assertAlmostEqual(adjusted_entry({'date':'2026-10-08','price_fen':980},item),9.8*slope+offset)
        with self.assertRaises(ValueError):adjusted_entry({'date':'2025-01-01','price_fen':1000},item)


class BufferedExecutionTests(unittest.TestCase):
    setUp=peg_fixture.PegExecutionTests.setUp
    tearDown=peg_fixture.PegExecutionTests.tearDown
    register=peg_fixture.PegExecutionTests.register

    def ready(self):
        peg_fixture.PegExecutionTests.ready(self)
        self.addCleanup(patch.stopall)
        patch('backend.quant.buffered_execution.industry_at',side_effect=lambda c,d:'行业'+c).start()

    def enable(self):
        return self.client.put('/api/paper/automation',headers=self.headers,json={'mode':'algorithm',
            'strategy_id':BUFFERED_PEG_ID,'expected_revision':0})

    def advance(self,hour,minute,day=9,prices=None):
        from test_paper_rules import cache
        self.now=self.now.replace(day=day,hour=hour,minute=minute)
        for code in CODES:
            item=cache(self.now,(prices or {}).get(code,10))
            if hour*60+minute>600:
                frozen=copy.deepcopy(item['candles'][0]);frozen.update(date=self.now.strftime('%Y-%m-%d')+' 10:00:00',open=10,high=10,low=10,close=10)
                item['candles'].insert(0,frozen)
            self.records.put('minute_market',code+':1',item)

    def tick(self):asyncio.run(self.service.tick())

    def account(self):return self.service.paper.account(self.owner)

    def decision(self):return self.service.store.decisions(self.owner)['items'][0]

    def manual(self,code,quantity):
        import uuid
        return asyncio.run(self.service.paper.submit(self.owner,str(uuid.uuid4()),code,'buy',quantity))

    def test_fixed_config_registered_and_three_entries_after_buy_window_start(self):
        self.ready()
        catalogue=self.client.get('/api/paper/strategies').json()['items']
        self.assertTrue(any(s['id']==BUFFERED_PEG_ID for s in catalogue))
        self.assertEqual(self.enable().status_code,200)
        self.tick();self.assertEqual(self.account()['positions'],[])
        self.advance(10,2);self.tick();self.assertEqual(self.account()['positions'],[])
        self.advance(10,7);self.tick()
        d=self.decision();self.assertEqual(d['status'],'completed',self.service.state(self.owner))
        self.assertEqual(len(self.account()['positions']),3,d)
        self.assertEqual(len(self.service.paper.trades(self.owner)['items']),3)
        self.tick();self.assertEqual(len(self.service.paper.trades(self.owner)['items']),3)
        self.assertIn('未复刻',d['execution_note'])
        state=self.records.get('peg_b_state','state',self.owner)
        self.assertEqual(set(state['entries']),set(CODES[:3]))
        self.assertEqual(state['sequence'],3)

    def test_same_day_five_holdings_cannot_be_assumed_sold_or_replaced(self):
        self.ready()
        for code in CODES[:5]:self.manual(code,100)
        before=self.service.paper.store.get_account(self.owner)
        self.assertEqual(self.enable().status_code,200)
        self.tick();self.advance(10,2);self.tick();self.advance(10,7);self.tick()
        after=self.service.paper.store.get_account(self.owner)
        self.assertEqual(set(after['positions']),set(before['positions']))
        self.assertEqual(len(after['positions']),5)
        self.assertTrue(any('T+1' in s for s in self.decision()['skipped_targets'].values()))
        self.assertFalse(any(t['code']==CODES[5] for t in self.service.paper.trades(self.owner)['items']))

    def test_blocked_stop_is_latched_and_cooldown_begins_only_on_full_actual_exit(self):
        self.ready();self.advance(9,50,day=8);self.manual(CODES[0],100)
        self.advance(10,0)
        original=self.service.history_loader
        async def load(codes,signal):
            data=await original(codes,signal)
            if CODES[0] in data:
                for b in data[CODES[0]]['raw']:b.update(open=8,close=8,high=8,low=8)
                data[CODES[0]]['adjusted']=copy.deepcopy(data[CODES[0]]['raw'])
            return data
        self.service.history_loader=load
        self.enable();self.tick()
        state=self.records.get('peg_b_state','state',self.owner)
        self.assertEqual(state['pending'],{CODES[0]:'price_hard'})
        self.advance(10,2,prices={CODES[0]:7})  # nearer than allowed daily limit => blocked
        self.tick();self.advance(10,7,prices={CODES[0]:7});self.tick()
        state=self.records.get('peg_b_state','state',self.owner)
        self.assertIn(CODES[0],state['pending']);self.assertNotIn(CODES[0],state['cooldowns'])
        self.assertIn(CODES[0],self.service.paper.store.get_account(self.owner)['positions'])
        # Next session price recovers; the latched exit still executes.
        self.records.put('peg','latest',snapshot('2026-10-09','next-day'))
        self.advance(10,0,day=12,prices={CODES[0]:8});self.tick()
        self.advance(10,2,day=12,prices={CODES[0]:8});self.tick()
        state=self.records.get('peg_b_state','state',self.owner)
        self.assertNotIn(CODES[0],state['entries'])
        self.assertEqual(state['cooldowns'][CODES[0]],'2026-10-12')

    def test_known_price_exit_survives_a_failed_quote_during_plan_preparation(self):
        from backend.paper_rules import PaperError
        self.ready();self.advance(9,50,day=8);self.manual(CODES[0],100);self.advance(10,0)
        original=self.service.history_loader
        async def load(codes,signal):
            data=await original(codes,signal)
            if CODES[0] in data:
                for b in data[CODES[0]]['raw']:b.update(open=8,close=8,high=8,low=8)
                data[CODES[0]]['adjusted']=copy.deepcopy(data[CODES[0]]['raw'])
            return data
        self.service.history_loader=load;self.enable()
        with patch.object(self.service,'fresh',side_effect=PaperError('QUOTE_UNAVAILABLE','held quote missing')):self.tick()
        self.assertEqual(self.service.store.decisions(self.owner)['items'],[])
        self.assertEqual(self.records.get('peg_b_state','state',self.owner)['pending'],{CODES[0]:'price_hard'})

    def test_band_suppresses_small_rebalancing_and_b_has_no_fifteen_percent_pause(self):
        self.ready();self.enable()
        cfg=self.service.store.config(self.owner);cfg['peak_equity_fen']=40_000_000
        self.records.put('quant_config','config',cfg,self.owner)
        self.tick();self.advance(10,2);self.tick();self.advance(10,7);self.tick()
        self.assertEqual(len(self.account()['positions']),3,self.decision())
        self.records.put('peg','latest',snapshot('2026-10-09','next-day'))
        self.advance(10,0,day=12);self.tick()
        d=self.decision();self.assertEqual(set(d['band_keep']),set(CODES[:3]));self.assertEqual(d['orders'],[])
        self.advance(10,2,day=12);self.tick();self.advance(10,7,day=12);self.tick()
        self.assertEqual(len(self.service.paper.trades(self.owner)['items']),3)
        self.assertEqual(self.service.store.config(self.owner)['mode'],'algorithm')

    def test_restart_replays_committed_fill_without_second_entry_or_double_cash(self):
        self.ready();self.enable();self.tick();self.advance(10,2);self.tick();self.advance(10,7)
        original=self.service.store.buffered_progress
        def fail_results(owner,key,revision,**changes):
            if changes.get('buy_done'):raise ValueError('process interruption after committed fills')
            return original(owner,key,revision,**changes)
        with patch.object(self.service.store,'buffered_progress',side_effect=fail_results):self.tick()
        before=self.service.paper.store.get_account(self.owner)
        self.records.put('quant_runtime','state',{},self.owner)
        self.tick();after=self.service.paper.store.get_account(self.owner)
        self.assertEqual(after,before);self.assertEqual(after['sequence'],3)
        self.assertEqual(self.decision()['status'],'completed')
        self.assertEqual(self.records.get('peg_b_state','state',self.owner)['sequence'],3)

    def test_failed_ledger_write_rolls_back_fill_and_entry_state(self):
        self.ready();self.enable();self.tick();self.advance(10,2);self.tick();self.advance(10,7)
        original=self.service.paper.store._put
        def fail(conn,namespace,*args):
            if namespace=='paper_trade':raise ValueError('disk write failed')
            return original(conn,namespace,*args)
        before=self.records.get('peg_b_state','state',self.owner)
        with patch.object(self.service.paper.store,'_put',side_effect=fail):self.tick()
        self.assertEqual(self.service.paper.store.get_account(self.owner)['sequence'],0)
        self.assertEqual(self.records.get('peg_b_state','state',self.owner),before)
        self.assertEqual(self.service.paper.trades(self.owner)['items'],[])

    def test_missed_window_records_no_retroactive_execution(self):
        self.ready();self.advance(13,0);self.enable();self.tick()
        self.assertEqual(self.account()['positions'],[])
        self.assertEqual(self.decision()['status'],'completed')
        self.assertIn('错过',self.decision()['reason'])

    def test_never_buys_second_same_sector_and_rechecks_cap_inside_transaction(self):
        self.ready()
        patch('backend.quant.buffered_execution.industry_at',return_value='同一行业').start()
        self.enable();self.tick();self.advance(10,2);self.tick();self.advance(10,7);self.tick()
        self.assertEqual(len(self.account()['positions']),1,self.decision())
        self.assertLess(self.account()['positions'][0]['market_value_fen']/self.account()['equity_fen'],.6)

    def guarded_submit(self,code,quantity):
        from backend.quant.buffered_peg import BUFFERED_VERSION
        from backend.quant.buffered_execution import order_for
        decision=self.decision();order=order_for(self.owner,decision['key'],code,'buy',quantity)
        decision['orders'].append(order)
        self.records.put('quant_decision',decision['key'],decision,self.owner)
        token=self.records.claim('quant:'+self.owner,300)
        execution={'revision':1,'decision_key':decision['key'],'lease_token':token,'strategy_id':BUFFERED_PEG_ID,
                   'model_version':BUFFERED_VERSION,'peg_snapshot_id':decision['peg_snapshot']['snapshot_id']}
        try:return asyncio.run(self.service.paper.submit(self.owner,**order,execution=execution))
        finally:self.records.release('quant:'+self.owner,token)

    def test_phase_window_and_sixty_percent_cap_are_atomic_guards(self):
        from backend.paper_rules import PaperError
        self.ready();self.enable();self.tick();self.advance(10,1)
        with self.assertRaises(PaperError) as caught:self.guarded_submit(CODES[0],15000)
        self.assertEqual(caught.exception.code,'PEG_WINDOW_CLOSED')
        self.advance(10,7)
        with self.assertRaises(PaperError) as caught:self.guarded_submit(CODES[0],15000)
        self.assertEqual(caught.exception.code,'QUANT_RISK_LIMIT')
        self.assertEqual(self.service.paper.store.get_account(self.owner)['sequence'],0)
        self.assertEqual(self.records.get('peg_b_state','state',self.owner)['entries'],{})

    def test_fourth_holding_cannot_enter_even_if_stale_plan_requests_it(self):
        from backend.paper_rules import PaperError
        self.ready();self.advance(9,50,day=8)
        for code in CODES[:3]:self.manual(code,100)
        self.advance(10,0);self.enable();self.tick();self.advance(10,7)
        with self.assertRaises(PaperError) as caught:self.guarded_submit(CODES[3],100)
        self.assertEqual(caught.exception.code,'QUANT_RISK_LIMIT')
        self.assertEqual(self.service.paper.store.get_account(self.owner)['sequence'],3)

    def test_original_anchor_survives_topup_and_switch_to_manual(self):
        self.ready();self.advance(9,50,day=8);self.manual(CODES[0],100)
        self.advance(10,0);self.enable()
        original=self.records.get('peg_b_state','state',self.owner)['entries'][CODES[0]]
        self.service.configure(self.owner,'manual',None,1)
        self.advance(10,7,prices={CODES[0]:12});self.manual(CODES[0],100)
        self.assertEqual(self.records.get('peg_b_state','state',self.owner)['entries'][CODES[0]],original)
        self.service.configure(self.owner,'algorithm',BUFFERED_PEG_ID,2)
        self.assertEqual(self.records.get('peg_b_state','state',self.owner)['sequence'],2)
