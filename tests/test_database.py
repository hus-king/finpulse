import unittest
from unittest.mock import MagicMock, patch

import pymysql

from backend.database import MySQLAuthStore


class ConnectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.store = MySQLAuthStore({'username': 'test', 'password': 'test-only'})

    def test_transient_handshake_failure_recovers_before_application_sql(self):
        raw = MagicMock()
        with patch('backend.database.pymysql.connect', side_effect=[
            pymysql.OperationalError(2003, 'unreachable'),
            pymysql.OperationalError(2013, 'handshake interrupted'), raw,
        ]) as connect, patch('backend.database.time.sleep') as sleep:
            with self.store.connection() as conn:
                self.assertIs(conn.raw, raw)
            self.assertEqual(connect.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])
            raw.close.assert_called_once()

    def test_bad_credentials_are_not_retried(self):
        with patch('backend.database.pymysql.connect', side_effect=pymysql.OperationalError(1045, 'denied')) as connect, patch('backend.database.time.sleep') as sleep:
            with self.assertRaises(pymysql.OperationalError), self.store.connection():
                self.fail('Connection should not yield')
            connect.assert_called_once()
            sleep.assert_not_called()

    def test_failure_after_transaction_begins_never_replays_writes(self):
        raw = MagicMock()
        raw.cursor.return_value.__enter__.return_value.execute.side_effect = pymysql.OperationalError(2013, 'query interrupted')
        with patch('backend.database.pymysql.connect', return_value=raw) as connect, patch('backend.database.time.sleep') as sleep:
            with self.assertRaises(pymysql.OperationalError), self.store.transaction() as conn:
                conn.execute('INSERT INTO users(id) VALUES(?)', ('test',))
            connect.assert_called_once()
            raw.begin.assert_called_once()
            raw.rollback.assert_called_once()
            raw.commit.assert_not_called()
            raw.close.assert_called_once()
            sleep.assert_not_called()
