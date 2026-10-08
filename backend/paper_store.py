"""Atomic account and immutable fill records on the existing auth database."""
import json
import time
from typing import Callable

from .auth import AuthError
from .paper_rules import PaperError, new_account


class PaperStore:
    def __init__(self, research_store):
        self.records = research_store
        self.db = research_store.db

    def get_account(self, owner: str) -> dict:
        return self.records.get('paper_account', 'account', owner, default=None) or new_account()

    def list_trades(self, owner: str, limit: int = 20, before_seq: int | None = None) -> dict:
        limit = min(100, max(1, limit))
        sequence = ("CAST(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.sequence')) AS UNSIGNED)"
                    if self.db.dialect == 'mysql' else "CAST(json_extract(payload, '$.sequence') AS INTEGER)")
        sql = 'SELECT payload FROM research_records WHERE namespace=? AND owner=?'
        params = ['paper_trade', owner]
        if before_seq is not None:
            sql += ' AND ' + sequence + '<?'
            params.append(before_seq)
        sql += ' ORDER BY ' + sequence + ' DESC LIMIT ?'
        params.append(limit + 1)
        with self.db.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        items = [self._public(json.loads(row['payload'])) for row in rows[:limit]]
        return {'items': items, 'next_before_seq': items[-1]['sequence'] if len(rows) > limit else None}

    @staticmethod
    def _public(trade):
        return {key: value for key, value in trade.items() if key != 'intent'}

    @staticmethod
    def _get(conn, namespace, key, owner, default):
        row = conn.execute('SELECT payload FROM research_records WHERE namespace=? AND record_key=? AND owner=?',
                           (namespace, key, owner)).fetchone()
        return json.loads(row['payload']) if row else default

    def _put(self, conn, namespace, key, owner, payload):
        now = int(time.time())
        sql = 'INSERT INTO research_records(namespace,record_key,owner,payload,created_at,updated_at) VALUES(?,?,?,?,?,?) '
        sql += ('ON DUPLICATE KEY UPDATE payload=VALUES(payload),updated_at=VALUES(updated_at)' if self.db.dialect == 'mysql'
                else 'ON CONFLICT(namespace,record_key,owner) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at')
        conn.execute(sql, (namespace, key, owner, json.dumps(payload, ensure_ascii=False, allow_nan=False), now, now))

    def transact(self, owner: str, request_id: str, intent: dict,
                 apply: Callable[[dict, dict], tuple[dict, dict]]) -> dict:
        with self.db.transaction() as conn:
            suffix = ' FOR UPDATE' if self.db.dialect == 'mysql' else ''
            # The user always exists before a paper account does. Locking this row
            # serializes initialization as well as every later fill for the owner.
            user = conn.execute('SELECT id,is_active FROM users WHERE id=?' + suffix, (owner,)).fetchone()
            if not user:
                raise AuthError(401, '请先登录后使用模拟盘。', 'AUTH_REQUIRED')
            if not user['is_active']:
                raise AuthError(403, '这个账号已停用。', 'ACCOUNT_DISABLED')
            previous = self._get(conn, 'paper_trade', request_id, owner, None)
            if previous:
                if previous['intent'] != intent:
                    raise PaperError('IDEMPOTENCY_CONFLICT', '这次请求编号已用于另一笔交易，请核对成交记录。')
                return self._public(previous)
            account = self._get(conn, 'paper_account', 'account', owner, new_account())
            cached = self._get(conn, 'minute_market', intent['code'] + ':1', '', {})
            updated, trade = apply(account, cached)
            sequence = account['sequence'] + 1
            updated['sequence'] = sequence
            saved = {**trade, 'request_id': request_id, 'sequence': sequence, 'intent': intent}
            self._put(conn, 'paper_account', 'account', owner, updated)
            self._put(conn, 'paper_trade', request_id, owner, saved)
        return self._public(saved)
