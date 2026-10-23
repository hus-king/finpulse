"""Read-only, allowlisted export of timestamped public company business facts.

Run on deployment via stdin with --root; never exports generated analyses.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys


def export(root):
    sys.path.insert(0, str(root))
    from backend.database import create_auth_store
    store = create_auth_store(root)
    versions, counts = {}, {}

    def add(code, profile, updated, namespace, key, extra_time=None):
        if not re.fullmatch(r'\d{6}', str(code or '')) or not isinstance(profile, dict):
            return
        text = profile.get('main_business')
        if not isinstance(text, str) or len(text.strip()) < 4:
            return
        facts = {k: profile.get(k) for k in ('main_business', 'revenue_segments', 'source', 'url', 'version')}
        identity = hashlib.sha256(json.dumps([code, facts], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        known = datetime.fromtimestamp(updated, timezone.utc)
        for value in (profile.get('fetched_at'), extra_time):
            if isinstance(value, (int, float)):
                known = max(known, datetime.fromtimestamp(value, timezone.utc))
            elif isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    if parsed.tzinfo:
                        known = max(known, parsed)
                except ValueError:
                    pass
        item = {'id': identity, 'code': code, **facts, 'known_at': known.isoformat(),
                'provenance': {'namespace': namespace, 'record_key': key, 'updated_at': updated},
                'limitation': 'Observed business snapshot; original public release timestamp unavailable'}
        if identity not in versions or known < datetime.fromisoformat(versions[identity]['known_at']):
            versions[identity] = item

    with store.connection() as conn:
        if store.dialect != 'mysql':
            raise ValueError('This deployed-cache exporter expects MySQL')
        conn.execute('START TRANSACTION READ ONLY')
        rows = conn.execute('SELECT record_key,payload,updated_at FROM research_records '
                            'WHERE namespace=? AND owner=?', ('company_business', '')).fetchall()
        counts['company_business'] = len(rows)
        for row in rows:
            value = json.loads(row['payload'])
            add(row['record_key'], value.get('data'), row['updated_at'], 'company_business', row['record_key'], value.get('fetched_at'))
        # Select context facts only; no model replies, scores, prompts or user records.
        for namespace, code_expr, profile_expr in (
            ('analysis', '$.stock_code', '$.context.business_profile'),
            ('dashboard', '$.stock.code', '$.business_profile'),
        ):
            rows = conn.execute(
                'SELECT record_key,updated_at,JSON_UNQUOTE(JSON_EXTRACT(payload,?)) AS stock_code,'
                'JSON_EXTRACT(payload,?) AS profile FROM research_records '
                'WHERE namespace=? AND owner=? AND JSON_EXTRACT(payload,?) IS NOT NULL',
                (code_expr, profile_expr, namespace, '', profile_expr)).fetchall()
            counts[namespace + '_fact_contexts'] = len(rows)
            for row in rows:
                add(row['stock_code'], json.loads(row['profile']), row['updated_at'], namespace, row['record_key'])
        conn.execute('ROLLBACK')
    return {'schema_version': 1, 'exported_at': datetime.now(timezone.utc).isoformat(),
            'source_counts': counts, 'availability': 'earliest verifiable exact snapshot; no publication backfill',
            'versions': sorted(versions.values(), key=lambda x: (x['code'], x['known_at'], x['id']))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.root), ensure_ascii=False, allow_nan=False))
