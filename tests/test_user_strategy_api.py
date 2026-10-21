import copy
import json
import unittest
import test_paper_api as fixture
from backend.quant.rules import EXAMPLE


class UserStrategyAPITests(unittest.TestCase):
    setUp=fixture.PaperAPITests.setUp
    tearDown=fixture.PaperAPITests.tearDown
    register=fixture.PaperAPITests.register

    def create(self,headers=None,package=None):
        return self.client.post('/api/paper/strategies',headers=headers or self.headers,json={'strategy':package or EXAMPLE})

    def ready(self):self.headers=self.register('user_rules')

    def test_public_schema_and_authenticated_private_routes(self):
        response=self.client.get('/api/paper/strategy-schema');self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['schema_version'],'finpulse.rules.v1')
        for path in ['/api/paper/strategies','/api/paper/strategies/user-unknown']:
            self.assertEqual(self.client.get(path).status_code,401)
        self.assertEqual(self.client.post('/api/paper/strategies',headers=fixture.ORIGIN,json={'strategy':EXAMPLE}).status_code,401)

    def test_validate_create_read_update_delete_and_revision_conflict(self):
        self.ready();valid=self.client.post('/api/paper/strategies/validate',headers=self.headers,json={'strategy':EXAMPLE})
        self.assertEqual(valid.status_code,200,valid.text);self.assertEqual(len(valid.json()['digest']),64)
        first=self.create();self.assertEqual(first.status_code,201,first.text)
        record=first.json();strategy_id=record['id'];self.assertEqual(record['revision'],1)
        read=self.client.get('/api/paper/strategies/'+strategy_id);self.assertEqual(read.headers['cache-control'],'no-store')
        changed={**EXAMPLE,'name':'更新后的策略'}
        update=self.client.put('/api/paper/strategies/'+strategy_id,headers=self.headers,json={'expected_revision':1,'strategy':changed})
        self.assertEqual(update.status_code,200,update.text);self.assertEqual(update.json()['revision'],2)
        stale=self.client.put('/api/paper/strategies/'+strategy_id,headers=self.headers,json={'expected_revision':1,'strategy':EXAMPLE})
        self.assertEqual(stale.status_code,409)
        delete=self.client.request('DELETE','/api/paper/strategies/'+strategy_id,headers=self.headers,json={'expected_revision':2})
        self.assertEqual(delete.status_code,200,delete.text);self.assertEqual(self.client.get('/api/paper/strategies/'+strategy_id).status_code,404)

    def test_cross_owner_csrf_and_unknown_fields(self):
        self.ready();first=self.create();self.assertEqual(first.status_code,201,first.text);strategy_id=first.json()['id']
        self.assertEqual(self.create(headers=fixture.ORIGIN).status_code,403)
        self.assertEqual(self.client.post('/api/paper/strategies',headers=self.headers,json={'strategy':EXAMPLE,'owner':'victim'}).status_code,422)
        self.headers=self.register('user_rules_other')
        self.assertEqual(self.client.get('/api/paper/strategies/'+strategy_id).status_code,404)
        self.assertEqual(self.client.put('/api/paper/strategies/'+strategy_id,headers=self.headers,json={'expected_revision':1,'strategy':EXAMPLE}).status_code,404)
        self.assertEqual(self.client.request('DELETE','/api/paper/strategies/'+strategy_id,headers=self.headers,json={'expected_revision':1}).status_code,404)

    def test_ten_strategy_quota_and_no_fee_api_calls_during_submission(self):
        self.ready()
        from unittest.mock import patch,AsyncMock
        with patch('backend.providers.akshare_worker',new=AsyncMock(side_effect=AssertionError('unexpected data request'))),patch('backend.app.completion',new=AsyncMock(side_effect=AssertionError('unexpected paid model call'))):
            for i in range(10):self.assertEqual(self.create(package={**EXAMPLE,'name':f'策略{i}'}).status_code,201)
            response=self.create();self.assertEqual(response.status_code,409);self.assertEqual(response.json()['code'],'STRATEGY_QUOTA')

    def test_validate_rate_limit_is_per_owner(self):
        self.ready()
        for _ in range(20):self.assertEqual(self.client.post('/api/paper/strategies/validate',headers=self.headers,json={'strategy':EXAMPLE}).status_code,200)
        self.assertEqual(self.client.post('/api/paper/strategies/validate',headers=self.headers,json={'strategy':EXAMPLE}).status_code,429)
        self.headers=self.register('user_rules_rate_other')
        self.assertEqual(self.client.post('/api/paper/strategies/validate',headers=self.headers,json={'strategy':EXAMPLE}).status_code,200)

    def test_actual_request_body_limit_and_deep_json(self):
        self.ready();body=json.dumps({'strategy':EXAMPLE,'padding':'a'*70000})
        response=self.client.post('/api/paper/strategies/validate',headers={**self.headers,'Content-Type':'application/json','Content-Length':'1'},content=body)
        self.assertEqual(response.status_code,413,response.text)
        response=self.client.post('/api/paper/strategies/validate',headers={**self.headers,'Content-Type':'application/json'},content='['*40+'0'+']'*40)
        self.assertEqual(response.status_code,422,response.text)

    def test_active_strategy_requires_pause_before_update_or_delete(self):
        self.ready();record=self.create();self.assertEqual(record.status_code,201,record.text);strategy_id=record.json()['id']
        from backend.app import app
        owner=self.client.get('/api/auth/me').json()['user']['id']
        app.state.quant.store.records.put('quant_config','config',{'mode':'algorithm','strategy_id':strategy_id,'revision':1},owner)
        body={'expected_revision':1,'strategy':EXAMPLE}
        self.assertEqual(self.client.put('/api/paper/strategies/'+strategy_id,headers=self.headers,json=body).json()['code'],'STRATEGY_IN_USE')
        self.assertEqual(self.client.request('DELETE','/api/paper/strategies/'+strategy_id,headers=self.headers,json={'expected_revision':1}).json()['code'],'STRATEGY_IN_USE')
