"""Persistent research documents and per-account preferences on the auth database."""
import json
import time
import uuid


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

    def claim(self, key, ttl=600):
        """Transactional lease shared by SQLite/MySQL and all backend instances."""
        now, token = int(time.time()), uuid.uuid4().hex
        with self.db.transaction() as conn:
            insert = 'INSERT INTO research_records VALUES(?,?,?,?,?,?) ON DUPLICATE KEY UPDATE record_key=record_key' if self.db.dialect == 'mysql' else 'INSERT OR IGNORE INTO research_records VALUES(?,?,?,?,?,?)'
            conn.execute(insert, ('lease', key, '', '{}', now, now))
            suffix = ' FOR UPDATE' if self.db.dialect == 'mysql' else ''
            row = conn.execute("SELECT payload FROM research_records WHERE namespace='lease' AND record_key=? AND owner=''" + suffix, (key,)).fetchone()
            if json.loads(row['payload']).get('expires', 0) > now:
                return None
            conn.execute("UPDATE research_records SET payload=?,updated_at=? WHERE namespace='lease' AND record_key=? AND owner=''", (json.dumps({'token': token, 'expires': now + ttl}), now, key))
        return token

    def renew(self, key, token, ttl=600):
        with self.db.transaction() as conn:
            suffix = ' FOR UPDATE' if self.db.dialect == 'mysql' else ''
            row = conn.execute("SELECT payload FROM research_records WHERE namespace='lease' AND record_key=? AND owner=''" + suffix, (key,)).fetchone()
            lease = json.loads(row['payload']) if row else {}
            if lease.get('token') != token or lease.get('expires', 0) <= time.time():
                return False
            lease['expires'] = int(time.time()) + ttl
            conn.execute("UPDATE research_records SET payload=?,updated_at=? WHERE namespace='lease' AND record_key=? AND owner=''", (json.dumps(lease), int(time.time()), key))
            return True

    def release(self, key, token):
        with self.db.transaction() as conn:
            suffix = ' FOR UPDATE' if self.db.dialect == 'mysql' else ''
            row = conn.execute("SELECT payload FROM research_records WHERE namespace='lease' AND record_key=? AND owner=''" + suffix, (key,)).fetchone()
            if row and json.loads(row['payload']).get('token') == token:
                conn.execute("DELETE FROM research_records WHERE namespace='lease' AND record_key=? AND owner=''", (key,))
