import json
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.auth import AuthError, AuthStore
from backend.database import MySQLAuthStore
from backend.paper_rules import PaperError, apply_trade, valid_quote
from backend.paper_store import PaperStore
from backend.research_store import ResearchStore
from test_paper_rules import NOW, STOCK, cache


class PaperStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.directory.name) / 'paper.db')
        self.auth.initialize()
        self.records = ResearchStore(self.auth)
        self.records.initialize()
        self.paper = PaperStore(self.records)
        self.user = self.auth.register('paper_one', '一号', 'Test-password-123!')[0]['id']
        self.other = self.auth.register('paper_two', '二号', 'Test-password-123!')[0]['id']
        self.now = NOW
        self.records.put('minute_market', '000001:1', cache())

    def tearDown(self):
        self.directory.cleanup()

    def fill(self, side='buy', quantity=100, request_id=None, owner=None):
        return self.paper.transact(owner or self.user, request_id or str(uuid.uuid4()),
            {'code': '000001', 'side': side, 'quantity': quantity},
            lambda account, cached: apply_trade(account, STOCK, side, quantity, valid_quote(cached, self.now), self.now))

    def test_lazy_200k_credit_is_independent_and_does_not_write_on_get(self):
        self.assertEqual(self.paper.get_account(self.user)['cash_fen'], 20_000_000)
        self.assertEqual(self.records.list('paper_account'), [])
        trade = self.fill()
        self.assertEqual(trade['sequence'], 1)
        self.assertEqual(self.paper.get_account(self.user)['cash_fen'], 19_899_499)
        self.assertEqual(self.paper.get_account(self.other)['cash_fen'], 20_000_000)
        self.assertEqual(self.paper.list_trades(self.other)['items'], [])

    def test_identical_request_returns_original_record_and_conflicting_request_fails(self):
        key = str(uuid.uuid4())
        first = self.fill(request_id=key)
        self.assertEqual(self.fill(request_id=key), first)
        with self.assertRaises(PaperError) as failure:
            self.fill(quantity=200, request_id=key)
        self.assertEqual(failure.exception.code, 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(len(self.paper.list_trades(self.user)['items']), 1)
        self.assertEqual(self.paper.get_account(self.user)['cash_fen'], 19_899_499)
        self.fill(request_id=key, owner=self.other)
        self.assertEqual(len(self.paper.list_trades(self.other)['items']), 1)

    def test_persistence_survives_store_restart_and_sequence_paginates_same_second(self):
        for _ in range(4):
            self.fill()
        other = PaperStore(ResearchStore(self.auth))
        self.assertEqual(other.get_account(self.user)['positions']['000001']['quantity'], 400)
        first = other.list_trades(self.user, limit=2)
        self.assertEqual([item['sequence'] for item in first['items']], [4, 3])
        second = other.list_trades(self.user, limit=2, before_seq=first['next_before_seq'])
        self.assertEqual([item['sequence'] for item in second['items']], [2, 1])
        self.assertIsNone(second['next_before_seq'])

    def test_rejected_first_order_does_not_persist_account_or_trade(self):
        with self.assertRaises(PaperError):
            self.fill(quantity=1)
        self.assertEqual(self.records.list('paper_account'), [])
        self.assertEqual(self.paper.list_trades(self.user)['items'], [])

    def test_same_id_concurrently_changes_cash_once(self):
        key = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=8) as pool:
            records = list(pool.map(lambda _: self.fill(request_id=key), range(8)))
        self.assertTrue(all(item == records[0] for item in records))
        self.assertEqual(self.paper.get_account(self.user)['cash_fen'], 19_899_499)
        self.assertEqual(len(self.paper.list_trades(self.user)['items']), 1)

    def test_competing_buys_cannot_spend_same_cash(self):
        self.records.put('minute_market', '000001:1', cache(price=1500))
        def attempt(_):
            try:
                return self.fill()['side']
            except PaperError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(attempt, range(2))), ['buy', 'INSUFFICIENT_CASH'])
        self.assertEqual(self.paper.get_account(self.user)['positions']['000001']['quantity'], 100)
        self.assertGreaterEqual(self.paper.get_account(self.user)['cash_fen'], 0)

    def test_concurrent_sells_cannot_sell_same_position(self):
        self.fill()
        self.now += timedelta(days=1)
        self.records.put('minute_market', '000001:1', cache(self.now))
        def attempt(_):
            try:
                return self.fill('sell')['side']
            except PaperError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(attempt, range(2))), ['sell', 'INSUFFICIENT_POSITION'])
        self.assertEqual(self.paper.get_account(self.user)['positions'], {})
        self.assertEqual(len(self.paper.list_trades(self.user)['items']), 2)

    def test_failure_after_account_write_rolls_back_everything(self):
        before = self.fill()
        account_before = self.paper.get_account(self.user)
        original = self.auth.transaction
        class FailingConnection:
            def __init__(self, connection): self.connection = connection
            def execute(self, sql, params=()):
                if sql.startswith('INSERT') and params[0] == 'paper_trade':
                    raise RuntimeError('simulated insert failure')
                return self.connection.execute(sql, params)
        @contextmanager
        def failing_transaction():
            with original() as conn:
                yield FailingConnection(conn)
        with patch.object(self.auth, 'transaction', failing_transaction):
            with self.assertRaisesRegex(RuntimeError, 'insert failure'):
                self.fill()
        self.assertEqual(self.paper.get_account(self.user), account_before)
        self.assertEqual(self.paper.list_trades(self.user)['items'], [before])

    def test_disabled_account_is_rechecked_inside_transaction_even_for_replay(self):
        key = str(uuid.uuid4())
        self.fill(request_id=key)
        with self.auth.connection() as conn:
            conn.execute('UPDATE users SET is_active=0 WHERE id=?', (self.user,))
        for request_id in [key, str(uuid.uuid4())]:
            with self.assertRaises(AuthError) as failure:
                self.fill(request_id=request_id)
            self.assertEqual(failure.exception.code, 'ACCOUNT_DISABLED')
        self.assertEqual(len(self.paper.list_trades(self.user)['items']), 1)

    def test_mysql_lock_and_account_trade_writes_share_one_transaction(self):
        database = MySQLAuthStore({'username':'test', 'password':'test'})
        raw = MagicMock()
        cursor = raw.cursor.return_value.__enter__.return_value
        statements = []
        def execute(sql, params):
            statements.append((sql, params))
            if sql.startswith('SELECT id'):
                rows = [{'id': self.user, 'is_active': 1}]
            elif sql.startswith('SELECT payload') and params[0] == 'minute_market':
                rows = [{'payload': json.dumps(cache())}]
            else:
                rows = []
            cursor.description = [('payload',)] if sql.startswith('SELECT') else None
            cursor.fetchall.return_value = rows
            cursor.rowcount = 1
        cursor.execute.side_effect = execute
        with patch('backend.database.pymysql.connect', return_value=raw) as connect:
            store = PaperStore(ResearchStore(database))
            trade = store.transact(self.user, str(uuid.uuid4()), {'code':'000001','side':'buy','quantity':100},
                lambda account, cached: apply_trade(account, STOCK, 'buy', 100, valid_quote(cached, NOW), NOW))
        self.assertEqual(trade['cash_after_fen'], 19_899_499)
        self.assertEqual(connect.call_count, 1)
        self.assertTrue(statements[0][0].endswith('FOR UPDATE'))
        self.assertEqual([params[0] for sql, params in statements if sql.startswith('INSERT')], ['paper_account', 'paper_trade'])
        raw.commit.assert_called_once()
        raw.rollback.assert_not_called()


if __name__ == '__main__': unittest.main()
