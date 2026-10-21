import asyncio
import time
import unittest
import uuid
from unittest.mock import patch
import test_paper_api as fixture
ORIGIN = fixture.ORIGIN
from test_paper_rules import cache
from backend.paper_rules import PaperError
from backend.quant.store import QuantStore


class StubStrategy:
    model={'version':'test-v1','universe':['000001']}
    def metadata(self): return {'id':'test','name':'测试策略','available':True,'universe':['000001'],'version':'test-v1','report':{}}
    def validate_config(self,config): return {}
    def decide(self,histories,as_of): return {'as_of':as_of,'target_weights':{'000001':.2},'model_version':'test-v1','scores':{'000001':.02},'reason':'test'}


class QuantAutomationTests(unittest.TestCase):
    setUp=fixture.PaperAPITests.setUp
    tearDown=fixture.PaperAPITests.tearDown
    register=fixture.PaperAPITests.register
    trade=fixture.PaperAPITests.trade

    def ready(self):
        from backend.app import app
        self.service=app.state.quant
        self.service.registry.strategies={'test':StubStrategy()}
        self.service.clock=lambda:self.now
        self.headers=self.register('quant_user')
        self.owner=self.client.get('/api/auth/me').json()['user']['id']
        return app

    def configure(self,mode='algorithm',revision=0):
        return self.client.put('/api/paper/automation',headers=self.headers,json={'mode':mode,'strategy_id':'test' if mode=='algorithm' else None,'expected_revision':revision})

    def test_default_manual_auth_csrf_and_revision(self):
        from backend.app import app
        for path in ['/api/paper/strategies','/api/paper/automation','/api/paper/automation/decisions']:
            self.assertEqual(self.client.get(path).status_code,401)
        self.ready()
        state=self.client.get('/api/paper/automation');self.assertEqual(state.json()['mode'],'manual')
        self.assertEqual(state.headers['cache-control'],'no-store')
        bad=self.client.put('/api/paper/automation',headers=ORIGIN,json={'mode':'manual','expected_revision':0})
        self.assertEqual(bad.status_code,403)
        first=self.configure();self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(self.configure().json()['code'],'AUTOMATION_CONFLICT')
        self.assertEqual(self.trade(self.headers).json()['code'],'AUTOMATION_ACTIVE')
        self.assertEqual(self.configure('manual',1).status_code,200)
        self.assertEqual(self.trade(self.headers).status_code,200)

    def test_replay_old_manual_order_is_allowed_after_enabling(self):
        self.ready();key=str(uuid.uuid4())
        first=self.trade(self.headers,request_id=key)
        self.assertEqual(first.status_code,200)
        self.configure()
        self.assertEqual(self.trade(self.headers,request_id=key).json(),first.json())

    def execution_context(self):
        app=self.ready();self.configure()
        token=self.service.store.records.claim('quant:'+self.owner,300)
        decision={'key':'2026-10-08:1','revision':1,'strategy_id':'test','model_version':'test-v1',
                  'as_of':'2026-10-07','date':self.now.date().isoformat(),'status':'running','budget_fen':6_000_000,'spent_fen':0,'orders':[],
                  'previous_close':{'000001':10},'eligible':['000001']}
        self.service.store.records.put('quant_decision',decision['key'],decision,self.owner)
        return {'revision':1,'decision_key':decision['key'],'lease_token':token,'strategy_id':'test','model_version':'test-v1'}

    def test_pause_stale_lease_and_disabled_user_cannot_execute(self):
        execution=self.execution_context()
        async def submit(): return await self.service.paper.submit(self.owner,str(uuid.uuid4()),'000001','buy',100,execution=execution)
        self.configure('manual',1)
        with self.assertRaises(PaperError) as result: asyncio.run(submit())
        self.assertEqual(result.exception.code,'AUTOMATION_CHANGED')
        self.configure('algorithm',2);execution['revision']=3
        with self.auth.connection() as conn: conn.execute('UPDATE users SET is_active=0 WHERE id=?',(self.owner,))
        with self.assertRaises(Exception): asyncio.run(submit())
        self.assertEqual(self.service.store.records.list('paper_trade'),[])

    def test_budget_and_per_stock_limit_and_lease_are_atomic(self):
        execution=self.execution_context()
        async def submit(quantity,key=None): return await self.service.paper.submit(self.owner,key or str(uuid.uuid4()),'000001','buy',quantity,execution=execution)
        key=str(uuid.uuid4());first=asyncio.run(submit(3900,key))
        self.assertEqual(asyncio.run(submit(3900,key)),first)
        with self.assertRaises(PaperError) as failure: asyncio.run(submit(200))
        self.assertEqual(failure.exception.code,'QUANT_RISK_LIMIT')
        state=self.service.store.records.get('quant_decision',execution['decision_key'],self.owner)
        self.assertEqual(state['spent_fen'],3_900_000)
        self.service.store.records.release('quant:'+self.owner,execution['lease_token'])
        with self.assertRaises(PaperError) as failure: asyncio.run(submit(100))
        self.assertEqual(failure.exception.code,'QUANT_LEASE_LOST')

    def test_automation_is_per_owner_and_models_cannot_be_selected_by_body_owner(self):
        self.ready();self.configure()
        self.register('quant_other')
        self.assertEqual(self.client.get('/api/paper/automation').json()['mode'],'manual')
        self.assertEqual(self.client.get('/api/paper/automation/decisions').json()['items'],[])
        response=self.client.put('/api/paper/automation',headers=self.headers,json={'mode':'manual','expected_revision':0,'owner':self.owner})
        self.assertIn(response.status_code,(403,422))

    def test_complete_background_execution_restarts_without_duplicate_fill(self):
        from datetime import timedelta
        from test_quant_model import history
        app=self.ready();self.configure()
        # A real verified previous trading day, rather than the holiday boundary fixture.
        self.now=self.now.replace(day=9)
        app.state.research.store.put('minute_market','000001:1',cache(self.now,10))
        bars=history(100); end=self.now.date()-timedelta(days=1)
        for i,b in enumerate(bars):
            b['date']=(end-timedelta(days=len(bars)-1-i)).isoformat()
            b.update(open=10,close=10,high=10,low=10,volume=100)
        async def load(codes,signal): return {'000001':{'raw':bars,'adjusted':bars}}
        self.service.history_loader=load
        asyncio.run(self.service.tick())
        decisions=self.client.get('/api/paper/automation/decisions').json()['items']
        self.assertEqual(decisions[0]['status'],'completed',decisions)
        self.assertEqual(len(self.service.store.records.list('paper_trade')),1)
        self.assertEqual(self.service.store.records.list('paper_trade')[0]['value']['actor'],'algorithm')
        key=decisions[0]['key']; decision=self.service.store.records.get('quant_decision',key,self.owner)
        decision['status']='running';self.service.store.records.put('quant_decision',key,decision,self.owner)
        asyncio.run(self.service.tick())
        self.assertEqual(len(self.service.store.records.list('paper_trade')),1)
        self.assertEqual(self.client.get('/api/paper/automation/decisions').json()['items'][0]['status'],'completed')

    def test_daily_budget_cannot_reset_by_switching_modes(self):
        execution=self.execution_context()
        self.service.store.records.put('quant_day',self.now.date().isoformat(),{'spent_fen':5_950_000,'budget_fen':6_000_000},self.owner)
        self.configure('manual',1);self.configure('algorithm',2)
        decision=self.service.store.records.get('quant_decision',execution['decision_key'],self.owner)
        decision['revision']=3;self.service.store.records.put('quant_decision',execution['decision_key'],decision,self.owner)
        execution['revision']=3
        with self.assertRaises(PaperError) as failure:
            asyncio.run(self.service.paper.submit(self.owner,str(uuid.uuid4()),'000001','buy',100,execution=execution))
        self.assertEqual(failure.exception.code,'QUANT_RISK_LIMIT')
        self.assertEqual(self.service.store.records.list('paper_trade'),[])

    def test_pause_serializes_with_an_inflight_order(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        execution=self.execution_context(); entered=Event(); release=Event()
        original=self.service.paper.store._put
        def blocking(conn,namespace,*args):
            if namespace=='paper_trade': entered.set();release.wait(3)
            return original(conn,namespace,*args)
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(self.service.paper.store,'_put',side_effect=blocking):
            fill=pool.submit(lambda:asyncio.run(self.service.paper.submit(self.owner,str(uuid.uuid4()),'000001','buy',100,execution=execution)))
            self.assertTrue(entered.wait(3))
            pause=pool.submit(self.service.store.configure,self.owner,'manual',None,1)
            self.assertFalse(pause.done());release.set();fill.result();pause.result()
        with self.assertRaises(PaperError):
            asyncio.run(self.service.paper.submit(self.owner,str(uuid.uuid4()),'000001','buy',100,execution=execution))
        self.assertEqual(len(self.service.store.records.list('paper_trade')),1)

    def test_other_holding_without_valid_quote_blocks_new_risk(self):
        execution=self.execution_context()
        # Buy once, then make its mark stale; a fresh candidate cannot conceal it.
        asyncio.run(self.service.paper.submit(self.owner,str(uuid.uuid4()),'000001','buy',100,execution=execution))
        self.service.store.records.put('minute_market','000001:1',{})
        with self.assertRaises(PaperError):
            asyncio.run(self.service.paper.submit(self.owner,str(uuid.uuid4()),'000001','buy',100,execution=execution))
        self.assertEqual(len(self.service.store.records.list('paper_trade')),1)

    def test_adapter_contract_uses_metadata_and_ensures_unattended_catalog(self):
        from unittest.mock import AsyncMock
        self.ready()
        class Adapter:
            def metadata(self):return {'id':'test','name':'Adapter','available':True,'universe':['600030'],'version':'test-v1'}
            def validate_config(self,cfg):return {}
            def decide(self,histories,as_of):return {}
        self.service.registry.strategies={'test':Adapter()};self.configure()
        catalogue=self.service.paper.catalog
        catalogue.items.pop('600030',None)
        async def ensure():catalogue.items['600030']={'code':'600030','name':'中信证券'}
        async def probe(codes,signal):
            self.assertEqual(codes,['600030'])
            raise ValueError('history probe')
        self.service.history_loader=probe
        with patch.object(catalogue,'ensure',new=AsyncMock(side_effect=ensure)) as loaded:
            with self.assertRaisesRegex(ValueError,'history probe'):
                asyncio.run(self.service.run_owner(self.owner,self.service.store.config(self.owner),'probe','token'))
            loaded.assert_awaited_once()

    def test_background_scheduler_is_opt_in_and_shuts_down(self):
        from unittest.mock import AsyncMock
        self.ready()
        self.assertIsNone(self.service.task)
        self.assertFalse(self.client.get('/api/health').json()['quant_enabled'])
        self.service.enabled=True
        async def check():
            with patch.object(self.service,'tick',new=AsyncMock()) as tick:
                self.service.start();await asyncio.sleep(.01)
                self.assertFalse(self.service.task.done());tick.assert_awaited_once()
                await self.service.close();self.assertTrue(self.service.task.done())
        asyncio.run(check())
