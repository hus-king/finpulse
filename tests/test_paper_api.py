import tempfile
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app import app
from backend.auth import AuthStore
from test_paper_rules import NOW, cache

ORIGIN = {'Origin': 'http://localhost'}


class PaperAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.directory.name) / 'api.db')
        app.state.auth_store = self.auth
        self.context = TestClient(app, base_url='http://localhost')
        self.client = self.context.__enter__()
        self.now = NOW
        app.state.minute_market.clock = lambda: self.now
        app.state.minute_market.closed = True
        if hasattr(app.state, 'paper_trading'):
            app.state.paper_trading.clock = lambda: self.now
        app.state.research.store.put('minute_market', '000001:1', cache())

    def tearDown(self):
        self.context.__exit__(None, None, None)
        del app.state.auth_store
        self.directory.cleanup()

    def register(self, username='paper_api'):
        response = self.client.post('/api/auth/register', headers=ORIGIN,
            json={'username':username,'nickname':'模拟用户','password':'Test-password-123!'})
        self.assertEqual(response.status_code, 201)
        return {**ORIGIN, 'X-CSRF-Token': response.json()['csrf_token']}

    def trade(self, headers, side='buy', quantity=100, **extra):
        return self.client.post('/api/paper/trades', headers=headers,
            json={'request_id':str(uuid.uuid4()),'code':'000001','side':side,'quantity':quantity, **extra})

    def test_all_paper_routes_require_login(self):
        for path in ['/api/paper/account','/api/paper/quote/000001','/api/paper/trades']:
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.trade(ORIGIN).status_code, 401)

    def test_initial_account_has_200k_and_private_responses(self):
        self.register()
        response = self.client.get('/api/paper/account')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json()['equity_fen'], 20_000_000)
        response = self.client.get('/api/paper/quote/000001')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['price_fen'], 1000)
        self.assertTrue(response.json()['can_trade'])
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_actual_round_trip_and_t_plus_one(self):
        headers = self.register()
        with patch('backend.providers.search_news', new_callable=AsyncMock) as news, patch('backend.app.completion', new_callable=AsyncMock) as model:
            first = self.trade(headers)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()['cash_after_fen'], 19_899_499)
            same_day = self.trade(headers, 'sell')
            self.assertEqual(same_day.status_code, 409)
            self.assertEqual(same_day.json()['code'], 'T_PLUS_ONE')
            account = self.client.get('/api/paper/account').json()
            self.assertEqual(account['positions'][0]['locked'], 100)
            self.now += timedelta(days=1)
            app.state.research.store.put('minute_market','000001:1',cache(self.now,10.5))
            self.assertEqual(self.client.get('/api/paper/account').json()['positions'][0]['sellable'], 100)
            result = self.trade(headers,'sell')
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['realized_pnl_fen'], 3945)
            account = self.client.get('/api/paper/account').json()
            self.assertEqual(account['equity_fen'],20_003_945)
            self.assertEqual(account['positions'],[])
            history = self.client.get('/api/paper/trades?limit=1').json()
            self.assertEqual(history['items'][0]['side'],'sell')
            self.assertEqual(history['next_before_seq'],2)
            self.assertEqual(len(self.client.get('/api/paper/trades?before_seq=2').json()['items']),1)
            news.assert_not_awaited()
            model.assert_not_awaited()

    def test_csrf_origin_and_untrusted_body_never_change_account(self):
        headers = self.register()
        for supplied in [ORIGIN,{**headers,'X-CSRF-Token':'0'*64},{**headers,'Origin':'https://attacker.example'}]:
            self.assertEqual(self.trade(supplied).status_code,403)
        for quantity in [True,100.0,0,-1,'100']:
            self.assertEqual(self.trade(headers, quantity=quantity).status_code,422)
        for extra in [{'owner':'another'},{'price':0.01},{'cash_fen':999999},{'side':'short'},{'request_id':'bad'}]:
            self.assertEqual(self.trade(headers,**extra).status_code,422)
        for params in ['limit=101','limit=0','before_seq=-1']:
            self.assertEqual(self.client.get('/api/paper/trades?'+params).status_code,422)
        self.assertEqual(self.client.get('/api/paper/account').json()['cash_fen'],20_000_000)

    def test_closed_stale_missing_and_unknown_stock_orders_fail(self):
        headers = self.register()
        self.now = NOW.replace(hour=12)
        self.assertEqual(self.trade(headers).json()['code'],'MARKET_CLOSED')
        self.now = NOW + timedelta(minutes=5)
        self.assertEqual(self.trade(headers).json()['code'],'QUOTE_STALE')
        app.state.research.store.put('minute_market','000001:1',{})
        self.assertEqual(self.trade(headers).json()['code'],'QUOTE_UNAVAILABLE')
        response = self.trade(headers,code='999999')
        self.assertEqual(response.status_code,404)
        self.assertEqual(self.client.get('/api/paper/quote/999999').status_code,404)
        self.assertEqual(self.client.get('/api/paper/account').json()['cash_fen'],20_000_000)

    def test_replay_survives_close_and_missing_quote_but_intent_cannot_change(self):
        headers = self.register()
        key = str(uuid.uuid4())
        first = self.trade(headers,request_id=key)
        self.assertEqual(first.status_code,200)
        self.now = NOW.replace(hour=16)
        app.state.research.store.put('minute_market','000001:1',{})
        again = self.trade(headers,request_id=key)
        self.assertEqual(again.status_code,200,again.text)
        self.assertEqual(again.json(),first.json())
        self.assertEqual(self.trade(headers,quantity=200,request_id=key).json()['code'],'IDEMPOTENCY_CONFLICT')
        self.assertEqual(len(self.client.get('/api/paper/trades').json()['items']),1)

    def test_clock_is_rechecked_after_lock_acquisition(self):
        headers = self.register()
        service = app.state.paper_trading
        original = service.store.transact
        def cross_close(*args):
            self.now = NOW.replace(hour=14,minute=57)
            return original(*args)
        with patch.object(service.store,'transact',side_effect=cross_close):
            response = self.trade(headers)
        self.assertEqual(response.json()['code'],'MARKET_CLOSED')
        self.assertEqual(self.client.get('/api/paper/account').json()['cash_fen'],20_000_000)

    def test_replay_survives_stock_removed_from_catalog(self):
        headers = self.register()
        key = str(uuid.uuid4())
        first = self.trade(headers, request_id=key)
        self.assertEqual(first.status_code, 200)
        with patch.object(app.state.paper_trading.catalog, 'get', return_value=None):
            again = self.trade(headers, request_id=key)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertEqual(again.json(), first.json())
            self.assertEqual(self.trade(headers).json()['code'], 'UNKNOWN_STOCK')
        self.assertEqual(len(self.client.get('/api/paper/trades').json()['items']), 1)

    def test_malformed_cache_roots_disable_quote_and_preserve_account_valuation(self):
        headers = self.register()
        self.assertEqual(self.trade(headers).status_code, 200)
        for raw in [None, [], 'invalid']:
            with self.subTest(raw=raw):
                app.state.research.store.put('minute_market', '000001:1', raw)
                result = self.client.get('/api/paper/quote/000001')
                self.assertEqual(result.status_code, 200, result.text)
                self.assertFalse(result.json()['can_trade'])
                self.assertEqual(result.json()['quote_status'], 'unavailable')
                self.assertEqual(self.trade(headers).json()['code'], 'QUOTE_UNAVAILABLE')
                account = self.client.get('/api/paper/account').json()
                self.assertEqual(account['market_value_fen'], 100_000)
                self.assertEqual(account['positions'][0]['valuation_status'], 'estimated')

    def test_disabled_between_auth_and_transaction_cannot_fill(self):
        headers = self.register()
        service = app.state.paper_trading
        original = service.store.transact
        def disable(owner,*args):
            with self.auth.connection() as conn:
                conn.execute('UPDATE users SET is_active=0 WHERE id=?',(owner,))
            return original(owner,*args)
        with patch.object(service.store,'transact',side_effect=disable):
            response = self.trade(headers)
        self.assertEqual(response.status_code,403)
        self.assertEqual(response.json()['code'],'ACCOUNT_DISABLED')
        self.assertEqual(app.state.research.store.list('paper_trade'),[])

    def test_other_account_cannot_read_first_account_holdings(self):
        headers = self.register()
        self.assertEqual(self.trade(headers).status_code,200)
        self.register('paper_other')
        self.assertEqual(self.client.get('/api/paper/account').json()['positions'],[])
        self.assertEqual(self.client.get('/api/paper/trades').json()['items'],[])


if __name__ == '__main__': unittest.main()
