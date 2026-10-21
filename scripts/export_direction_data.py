"""Read-only export of public research material; never export accounts or analysis.

Run inside a deployed checkout: python -m scripts.export_direction_data --output file.json
The same file can run over SSH on stdin with --root /path/to/deployed/checkout.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys


@contextmanager
def read_connection(store):
    if store.dialect == 'sqlite':
        # Do not accidentally create an empty DB when a local path is missing.
        conn = sqlite3.connect(store.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    else:
        with store.connection() as conn:
            yield conn


def export(root):
    sys.path.insert(0, str(root))
    from backend.database import create_auth_store
    store = create_auth_store(root)
    news, markets, counts = {}, {}, Counter()
    # Explicit allowlist: do not initialize, migrate, or read private namespaces.
    with read_connection(store) as conn:
        for namespace in ('collection', 'daily_market', 'quant_history'):
            rows = conn.execute('SELECT record_key,payload,updated_at FROM research_records '
                                'WHERE namespace=? AND owner=? ORDER BY updated_at',
                                (namespace, '')).fetchall()
            counts[namespace] = len(rows)
            for row in rows:
                value = json.loads(row['payload'])
                if namespace == 'collection':
                    observed = datetime.fromtimestamp(row['updated_at'], timezone.utc)
                    stamp = value.get('collected_at')
                    if stamp:
                        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                        if parsed.tzinfo:
                            observed = max(observed, parsed)
                    for item in value.get('cleaning', {}).get('items', []):
                        code = item.get('stock_code') or value.get('code')
                        if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
                            continue
                        text = item.get('cleaned_text') or ''
                        identity = [code, item.get('url'), item.get('title'), text,
                                    item.get('effective_date'), item.get('published_date_metadata'),
                                    item.get('text_source'), item.get('date_status')]
                        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
                        document = {
                            'id': key, 'code': code, 'company': item.get('stock', ''),
                            'url': item.get('url', ''), 'title': item.get('title', ''), 'text': text,
                            'publication_date': item.get('effective_date'),
                            'publication_timestamp': item.get('published_date_metadata'),
                            'text_source': item.get('text_source'), 'date_status': item.get('date_status'),
                            'first_seen_at': observed.isoformat(),
                        }
                        if key not in news or observed < datetime.fromisoformat(news[key]['first_seen_at']):
                            news[key] = document
                else:
                    code = row['record_key'].split(':')[0]
                    if not re.fullmatch(r'\d{6}', code):
                        continue
                    target = markets.setdefault(code, {'raw': {}, 'adjusted': {}})
                    for kind, bars in [('raw', value.get('candles', value.get('raw', []))),
                                       ('adjusted', value.get('adjusted', []))]:
                        for bar in bars:
                            if all(k in bar for k in ('date', 'open', 'close', 'high', 'low', 'volume')):
                                target[kind][bar['date']] = {k: bar[k] for k in ('date', 'open', 'close', 'high', 'low', 'volume')}
    return {'schema_version': 1, 'exported_at': datetime.now(timezone.utc).isoformat(),
            'source': 'read-only research_records: collection, daily_market, quant_history',
            'source_counts': dict(counts), 'news': sorted(news.values(), key=lambda x: x['id']),
            'markets': {code: {kind: sorted(bars.values(), key=lambda b: b['date'])
                               for kind, bars in data.items()} for code, data in sorted(markets.items())}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--output', default='-')
    args = parser.parse_args()
    result = json.dumps(export(args.root.resolve()), ensure_ascii=False, allow_nan=False)
    if args.output == '-':
        print(result)
    else:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(result + '\n', encoding='utf-8')
