import asyncio
import copy
import unittest
from datetime import timedelta
from unittest.mock import patch
import test_paper_api as fixture
from backend.quant.rules import EXAMPLE
from test_paper_rules import cache
from test_quant_model import history


class UserStrategyExecutionTests(unittest.TestCase):
    setUp=fixture.PaperAPITests.setUp
    tearDown=fixture.PaperAPITests.tearDown
    register=fixture.PaperAPITests.register

    def ready(self):
        from backend.app import app
        self.headers=self.register('rule_execution');self.owner=self.client.get('/api/auth/me').json()['user']['id']
        self.service=app.state.quant;self.now=self.now.replace(day=9);self.service.clock=lambda:self.now
        app.state.research.store.put('minute_market','000001:1',cache(self.now,10))
        async def load(codes,signal):
            bars=history();end=self.now.date()-timedelta(days=1)
            for i,b in enumerate(bars):
                b.update(date=(end-timedelta(days=len(bars)-1-i)).isoformat(),open=10,close=10,high=10.1,low=9.9,volume=1000)
            return {code:{'raw':bars,'adjusted':bars} for code in codes}
        self.service.history_loader=load
        self.package={**copy.deepcopy(EXAMPLE),'universe':['000001'],'score':{'value':1},'filters':[]}
        response=self.client.post('/api/paper/strategies',headers=self.headers,json={'strategy':self.package})
        self.assertEqual(response.status_code,201,response.text);self.record=response.json()

    def enable(self,config_revision=0,strategy_revision=1):
        return self.client.put('/api/paper/automation',headers=self.headers,json={'mode':'algorithm','strategy_id':self.record['id'],
            'strategy_revision':strategy_revision,'expected_revision':config_revision})

    def test_user_strategy_runs_shared_executor_and_replays_after_restart(self):
        self.ready();response=self.enable();self.assertEqual(response.status_code,200,response.text)
        catalogue=self.client.get('/api/paper/strategies').json()['items'];item=next(s for s in catalogue if s['id']==self.record['id'])
        self.assertEqual(item['origin'],'user');self.assertTrue(any(s.get('origin')=='official' for s in catalogue))
        asyncio.run(self.service.tick())
        trades=self.service.paper.trades(self.owner)['items'];self.assertEqual(len(trades),1)
        self.assertEqual(trades[0]['strategy_id'],self.record['id']);self.assertEqual(trades[0]['actor'],'algorithm')
        self.assertLessEqual(trades[0]['gross_fen'],4_000_000)
        decision=self.service.store.decisions(self.owner)['items'][0];decision['status']='running'
        self.service.store.records.put('quant_decision',decision['key'],decision,self.owner)
        asyncio.run(self.service.tick());self.assertEqual(len(self.service.paper.trades(self.owner)['items']),1)

    def test_strategy_revision_is_required_and_stale_selection_cannot_enable(self):
        self.ready()
        missing=self.client.put('/api/paper/automation',headers=self.headers,json={'mode':'algorithm','strategy_id':self.record['id'],'expected_revision':0})
        self.assertEqual(missing.status_code,422)
        changed=self.client.put('/api/paper/strategies/'+self.record['id'],headers=self.headers,json={'expected_revision':1,'strategy':self.package})
        self.assertEqual(changed.status_code,200)
        self.assertEqual(self.enable().json()['code'],'STRATEGY_REVISION_CONFLICT')
        self.assertEqual(self.client.get('/api/paper/automation').json()['mode'],'manual')
        self.assertEqual(self.enable(strategy_revision=2).status_code,200)

    def test_other_owner_cannot_select_my_strategy(self):
        self.ready();self.headers=self.register('rule_execution_other')
        self.assertNotIn(self.record['id'],[s['id'] for s in self.client.get('/api/paper/strategies').json()['items']])
        self.assertEqual(self.enable().status_code,404)

    def test_deleted_between_resolution_and_config_lock_cannot_enable(self):
        self.ready();original=self.service.strategy
        def resolve(owner,strategy_id):
            result=original(owner,strategy_id)
            self.service.users.delete(owner,strategy_id,1)
            return result
        with patch.object(self.service,'strategy',side_effect=resolve):
            self.assertEqual(self.enable().status_code,404)
        self.assertEqual(self.client.get('/api/paper/automation').json()['mode'],'manual')

    def test_rule_runtime_error_does_not_liquidate_existing_holdings(self):
        self.ready();self.assertEqual(self.enable().status_code,200);asyncio.run(self.service.tick())
        self.assertEqual(len(self.service.paper.trades(self.owner)['items']),1)
        self.client.put('/api/paper/automation',headers=self.headers,json={'mode':'manual','expected_revision':1})
        self.package['score']={'op':'div','args':[{'value':1},{'value':0}]}
        self.client.put('/api/paper/strategies/'+self.record['id'],headers=self.headers,json={'expected_revision':1,'strategy':self.package})
        self.assertEqual(self.enable(2,2).status_code,200);asyncio.run(self.service.tick())
        self.assertEqual(len(self.service.paper.trades(self.owner)['items']),1)
        self.assertIn('除零',self.service.state(self.owner)['runtime']['reason'])
