import asyncio
import copy
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import test_paper_api as fixture
from test_paper_rules import cache
from test_quant_model import history
from backend.news_cleaning import SHANGHAI
from backend.peg_snapshot import METHOD_VERSION
from backend.quant.peg_strategy import PEG_STRATEGY_ID, PegStrategy
from backend.quant.strategy import StrategyRegistry

NOW = datetime(2026, 10, 9, 10, tzinfo=SHANGHAI)
CODES = ['000001', '600000', '600030', '600519', '300750', '601318']


def snapshot(day='2026-10-08', ident='snapshot-one'):
    rows = [dict(code=c, name='股票'+c, group='金融' if i < 3 else '非金融',
                 status='合格', peg=(i+1)/10, collected_at=day+'T18:00:00+08:00') for i,c in enumerate(CODES)]
    rows += [dict(code=f'002{i:03d}', name='观察股', group='非金融', status='观察', peg=None,
                  collected_at=day+'T18:00:00+08:00') for i in range(294)]
    return dict(snapshot_id=ident, method_version=METHOD_VERSION, market_date=day, as_of_date=day,
                published_at=day+'T21:05:00+08:00', rows=rows, summary={'total':300})


class Records:
    def __init__(self, latest=None, older=()):self.latest=latest; self.older=older
    def get(self, namespace, key, **kwargs):return copy.deepcopy(self.latest)
    def list(self, namespace, **kwargs):return [{'value':copy.deepcopy(v)} for v in self.older]


class PegStrategyTests(unittest.TestCase):
    def prepare(self, snap=None, config=None, older=()):
        return PegStrategy().prepare(Records(snap if snap is not None else snapshot(),older),config or {},'2026-10-08',NOW)

    def test_defaults_and_strict_configuration(self):
        self.assertEqual(PegStrategy().validate_config({}),{'top_n':5,'group':'all'})
        for cfg in [{'top_n':True},{'top_n':0},{'top_n':6},{'top_n':'5'},{'group':'bad'},{'unknown':1}]:
            with self.subTest(cfg=cfg),self.assertRaises(ValueError):PegStrategy().validate_config(cfg)

    def test_all_groups_order_positive_peg_then_code_and_equal_weights(self):
        s=snapshot();s['rows'][0]['peg']=.2
        adapter=self.prepare(s);meta=adapter.metadata()
        self.assertEqual(meta['universe'],CODES[:5])
        result=adapter.decide({c:[{'date':'2026-10-08'}] for c in CODES[:5]},'2026-10-08')
        self.assertEqual(result['target_weights'],dict.fromkeys(CODES[:5],.16))
        self.assertEqual(result['peg_snapshot']['snapshot_id'],'snapshot-one')
        self.assertEqual(result['strategy_config'],{'top_n':5,'group':'all'})

    def test_group_and_smaller_count_respect_twenty_percent_cap(self):
        adapter=self.prepare(config={'top_n':2,'group':'非金融'})
        self.assertEqual(adapter.metadata()['universe'],CODES[3:5])
        result=adapter.decide({c:[{'date':'2026-10-08'}] for c in CODES[3:5]},'2026-10-08')
        self.assertEqual(list(result['target_weights'].values()),[.2,.2])

    def test_intraday_republication_uses_latest_compatible_pre_day_history(self):
        newer=snapshot(ident='intraday');newer['published_at']='2026-10-09T09:00:00+08:00'
        older=snapshot(ident='last-evening');earlier=snapshot(ident='earlier');earlier['published_at']='2026-10-08T20:00:00+08:00'
        self.assertEqual(self.prepare(newer,older=[earlier,older]).snapshot['snapshot_id'],'last-evening')

    def test_missing_stale_future_incompatible_partial_or_empty_never_plan_liquidation(self):
        cases=[None,snapshot('2026-09-30')]
        for field,value in [('published_at','2026-10-09T00:00:00+08:00'),('method_version','other'),('rows',[]),('summary',{'total':299})]:
            s=snapshot();s[field]=value;cases.append(s)
        s=snapshot();s['rows']=s['rows'][:-1];cases.append(s)
        s=snapshot()
        for row in s['rows']:row['status']='观察'
        cases.append(s)
        for s in cases:
            with self.subTest(snapshot=s and s.get('snapshot_id')),self.assertRaises(ValueError):
                PegStrategy().prepare(Records(s),{},'2026-10-08',NOW)

    def test_corrupt_eligible_values_codes_and_source_times_are_rejected(self):
        for field,value in [('peg',0),('peg',-1),('peg',float('nan')),('peg',True),('code','999999'),('collected_at','2026-10-09T08:00:00+08:00')]:
            s=snapshot();s['rows'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.prepare(s)
        s=snapshot();s['rows'][1]['code']=s['rows'][0]['code']
        with self.assertRaises(ValueError):self.prepare(s)

    def test_prepared_plan_is_immutable_and_requires_signal_day_histories(self):
        s=snapshot();adapter=self.prepare(s);s['rows'][0]['peg']=99
        self.assertEqual(adapter.snapshot['rows'][0]['peg'],.1)
        with self.assertRaises(ValueError):adapter.decide({},'2026-10-08')

    def test_unselected_security_outside_paper_board_rules_does_not_invalidate_the_batch(self):
        s=snapshot();s['rows'][-1].update(code='302132',name='中航成飞',status='合格',peg=99)
        self.assertEqual(self.prepare(s).metadata()['universe'],CODES[:5])

    def test_unsupported_selected_security_stops_with_an_explicit_message(self):
        s=snapshot();s['rows'][-1].update(code='302132',name='中航成飞',status='合格',peg=.01)
        with self.assertRaisesRegex(ValueError,'302132.*模拟盘'):self.prepare(s)

    def test_registry_keeps_peg_when_neural_model_is_unavailable(self):
        with patch('backend.quant.strategy.QuantStrategy',side_effect=OSError('missing model')):registry=StrategyRegistry()
        self.assertTrue(registry.get(PEG_STRATEGY_ID).metadata()['available'])
        self.assertTrue(any(not r['available'] for r in registry.list()))


class PegExecutionTests(unittest.TestCase):
    setUp=fixture.PaperAPITests.setUp
    tearDown=fixture.PaperAPITests.tearDown
    register=fixture.PaperAPITests.register

    def ready(self):
        from backend.app import app
        self.headers=self.register('peg_execution');self.owner=self.client.get('/api/auth/me').json()['user']['id']
        self.service=app.state.quant;self.now=NOW;self.service.clock=lambda:self.now
        self.records=self.service.store.records;self.records.put('peg','latest',snapshot())
        for code in CODES:
            self.service.paper.catalog.items[code]={'code':code,'name':'股票'+code,'industry':'测试','exchange':'SH' if code.startswith('6') else 'SZ'}
            self.records.put('minute_market',code+':1',cache(self.now,10))
        self.loaded=[]
        async def load(codes,signal):
            self.loaded.append(list(codes));bars=history();end=datetime.fromisoformat(signal).date()
            for i,b in enumerate(bars):b.update(date=(end-timedelta(days=len(bars)-1-i)).isoformat(),open=10,close=10,high=10,low=10,volume=1000)
            return {c:{'raw':bars,'adjusted':bars} for c in codes}
        self.service.history_loader=load

    def enable(self,config=None,revision=0):
        return self.client.put('/api/paper/automation',headers=self.headers,json={'mode':'algorithm','strategy_id':PEG_STRATEGY_ID,'expected_revision':revision,'strategy_config':config or {}})

    def test_api_config_persists_per_owner_and_rejects_invalid_or_other_strategy_parameters(self):
        self.ready();r=self.enable();self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['strategy_config'],{'top_n':5,'group':'all'})
        self.assertEqual(self.enable(revision=0).status_code,409)
        self.assertEqual(self.enable({'top_n':6},1).status_code,422)
        self.assertEqual(self.enable({'top_n':True},1).status_code,422)
        bad=self.client.put('/api/paper/automation',headers=self.headers,json={'mode':'algorithm','strategy_id':'official-multifactor-v1','expected_revision':1,'strategy_config':{'top_n':5}})
        self.assertEqual(bad.status_code,422)
        self.register('peg_other');self.assertEqual(self.client.get('/api/paper/automation').json()['mode'],'manual')

    def test_first_day_spreads_thirty_percent_budget_across_five_and_pins_provenance(self):
        self.ready();self.assertEqual(self.enable().status_code,200)
        asyncio.run(self.service.tick());decisions=self.service.store.decisions(self.owner)['items']
        self.assertEqual(len(decisions),1,self.service.state(self.owner));d=decisions[0]
        self.assertEqual(d['status'],'completed');self.assertEqual(len(d['target_weights']),5)
        self.assertEqual(d['peg_snapshot']['snapshot_id'],'snapshot-one')
        self.assertEqual(self.loaded,[CODES[:5]])
        trades=self.service.paper.trades(self.owner)['items']
        self.assertEqual(len(trades),5,d);self.assertLessEqual(sum(t['gross_fen'] for t in trades),6_000_000)
        self.assertEqual(len(d['allocation_outcome']['positions']),5)
        self.assertTrue(d['allocation_outcome']['differences'])

    def test_restart_replays_pinned_orders_without_new_snapshot_or_duplicate_fill(self):
        self.ready();self.enable();asyncio.run(self.service.tick())
        d=self.service.store.decisions(self.owner)['items'][0];d['status']='running';self.records.put('quant_decision',d['key'],d,self.owner)
        self.records.put('peg','latest',{})
        with patch.object(PegStrategy,'prepare',side_effect=AssertionError('must not re-prepare')):asyncio.run(self.service.tick())
        self.assertEqual(len(self.service.paper.trades(self.owner)['items']),5)
        self.assertEqual(self.service.store.decisions(self.owner)['items'][0]['status'],'completed')

    def test_stale_snapshot_keeps_existing_positions_without_any_new_trade(self):
        self.ready();self.enable();asyncio.run(self.service.tick())
        self.now=self.now.replace(day=12)
        for code in CODES:self.records.put('minute_market',code+':1',cache(self.now,10))
        asyncio.run(self.service.tick())
        self.assertEqual(len(self.service.paper.trades(self.owner)['items']),5)
        self.assertEqual(len(self.service.paper.account(self.owner)['positions']),5)
        self.assertEqual(self.service.state(self.owner)['runtime']['status'],'waiting')

    def test_dropped_holdings_sell_even_when_outside_new_selected_universe(self):
        self.ready();self.enable();asyncio.run(self.service.tick())
        self.now=self.now.replace(day=12)
        for code in CODES:self.records.put('minute_market',code+':1',cache(self.now,10))
        self.records.put('peg','latest',snapshot('2026-10-09','snapshot-two'))
        self.assertEqual(self.enable({'top_n':1,'group':'all'},1).status_code,200)
        asyncio.run(self.service.tick())
        trades=self.service.paper.trades(self.owner)['items']
        self.assertEqual(len([t for t in trades if t['side']=='sell']),4,self.service.state(self.owner))
        self.assertEqual([p['code'] for p in self.service.paper.account(self.owner)['positions']],[CODES[0]])

    def test_non_trading_day_never_creates_a_decision(self):
        self.ready();self.enable();self.now=self.now.replace(day=10)
        asyncio.run(self.service.tick());self.assertEqual(self.service.store.decisions(self.owner)['items'],[])

    def test_peg_execution_cannot_invent_a_new_order_id_from_a_saved_plan(self):
        import uuid
        from backend.paper_rules import PaperError
        self.ready();self.enable();asyncio.run(self.service.tick())
        d=self.service.store.decisions(self.owner)['items'][0];d['status']='running';self.records.put('quant_decision',d['key'],d,self.owner)
        token=self.records.claim('quant:'+self.owner,300)
        context={'revision':1,'decision_key':d['key'],'lease_token':token,'strategy_id':PEG_STRATEGY_ID,
                 'model_version':d['model_version'],'peg_snapshot_id':d['peg_snapshot']['snapshot_id']}
        with self.assertRaises(PaperError) as failure:
            asyncio.run(self.service.paper.submit(self.owner,str(uuid.uuid4()),CODES[0],'buy',100,execution=context))
        self.assertEqual(failure.exception.code,'AUTOMATION_CHANGED')

    def test_drawdown_pause_closes_the_saved_plan_instead_of_leaving_it_running(self):
        from unittest.mock import AsyncMock
        from backend.paper_rules import PaperError
        self.ready();self.enable()
        with patch.object(self.service.paper,'submit',new=AsyncMock(side_effect=PaperError('QUANT_DRAWDOWN','回撤达到15%'))):
            asyncio.run(self.service.tick())
        self.assertEqual(self.service.store.config(self.owner)['mode'],'manual')
        self.assertEqual(self.service.store.decisions(self.owner)['items'][0]['status'],'paused')

    def test_next_day_expires_old_running_plan_before_planning_from_actual_positions(self):
        self.ready();self.enable();asyncio.run(self.service.tick())
        d=self.service.store.decisions(self.owner)['items'][0];d['status']='running';self.records.put('quant_decision',d['key'],d,self.owner)
        self.now=self.now.replace(day=12)
        for code in CODES:self.records.put('minute_market',code+':1',cache(self.now,10))
        self.records.put('peg','latest',snapshot('2026-10-09','next-day'))
        asyncio.run(self.service.tick())
        self.assertEqual(self.records.get('quant_decision',d['key'],owner=self.owner)['status'],'expired')
