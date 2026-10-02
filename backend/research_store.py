"""Persistent research documents and per-account preferences on the auth database."""
import json
import time


class ResearchStore:
    def __init__(self, auth_store):
        self.db = auth_store

    def initialize(self):
        with self.db.connection() as conn:
            if self.db.dialect == 'sqlite':
                conn.execute('''CREATE TABLE IF NOT EXISTS research_records (
                    namespace TEXT NOT NULL, record_key TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
                    PRIMARY KEY(namespace,record_key,owner))''')
            else:
                conn.execute('SELECT namespace,record_key,owner,payload FROM research_records LIMIT 0')

    def get(self, namespace, key, owner='', default=None):
        with self.db.connection() as conn:
            row = conn.execute('SELECT payload FROM research_records WHERE namespace=? AND record_key=? AND owner=?', (namespace, key, owner)).fetchone()
        return json.loads(row['payload']) if row else default

    def put(self, namespace, key, payload, owner=''):
        now = int(time.time())
        values = (namespace, key, owner, json.dumps(payload, ensure_ascii=False, allow_nan=False), now, now)
        sql = 'INSERT INTO research_records(namespace,record_key,owner,payload,created_at,updated_at) VALUES(?,?,?,?,?,?) '
        if self.db.dialect == 'mysql':
            sql += 'ON DUPLICATE KEY UPDATE payload=VALUES(payload),updated_at=VALUES(updated_at)'
        else:
            sql += 'ON CONFLICT(namespace,record_key,owner) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at'
        with self.db.transaction() as conn:
            conn.execute(sql, values)

    def list(self, namespace, owner=None, limit=100):
        with self.db.connection() as conn:
            rows = conn.execute('SELECT record_key,owner,payload FROM research_records WHERE namespace=?' + (' AND owner=?' if owner is not None else '') + ' ORDER BY updated_at DESC LIMIT ?', (namespace, owner, limit) if owner is not None else (namespace, limit)).fetchall()
        return [{'key': row['record_key'], 'owner': row['owner'], 'value': json.loads(row['payload'])} for row in rows]
