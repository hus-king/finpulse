"""Copy only public, real smoke-test research records to the configured shared DB.

Does not copy test accounts, tasks, watchlists, subscriptions or credentials.
"""
import json
import time
from pathlib import Path

from backend.auth import AuthStore
from backend.database import create_auth_store
from backend.providers import ROOT
from backend.research_store import ResearchStore


def main():
    source = ResearchStore(AuthStore(ROOT / '.runtime/live-research-smoke.sqlite3'))
    target = create_auth_store(ROOT)
    if target.dialect != 'mysql':
        raise RuntimeError('Explicit shared MySQL target required')
    records = []
    for namespace in ('collection', 'analysis', 'dashboard'):
        records.extend((namespace, row) for row in source.list(namespace, owner='', limit=100))
    with target.transaction() as conn:
        for namespace, row in records:
            now = int(time.time())
            conn.execute('INSERT INTO research_records(namespace,record_key,owner,payload,created_at,updated_at) VALUES(?,?,?,?,?,?) ON DUPLICATE KEY UPDATE payload=VALUES(payload),updated_at=VALUES(updated_at)', (namespace, row['key'], '', json.dumps(row['value'], ensure_ascii=False, allow_nan=False), now, now))
    print(f'Published {len(records)} public live-research records to shared MySQL; no private account data was copied.')


if __name__ == '__main__':
    main()
