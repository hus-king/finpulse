"""Explicit live API smoke test. Uses configured Tavily/LLM quota; sends no notifications."""
import argparse
import json
import os
import secrets
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import app

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--codes', nargs='+', default=['600519'])
    parser.add_argument('--community', action='store_true')
    parser.add_argument('--max-articles', type=int, default=2)
    parser.add_argument('--sqlite', action='store_true', help='Explicit isolated SQLite smoke test; does not change the website configuration')
    args = parser.parse_args()
    if args.sqlite:
        os.environ['FINPULSE_DB_ENGINE'] = 'sqlite'
        os.environ['FINPULSE_DB_PATH'] = str(ROOT / '.runtime/live-research-smoke.sqlite3')
        print('Explicit SQLite smoke test; production MySQL configuration is unchanged.', flush=True)
    else:
        credentials = json.loads((ROOT / 'data/mysql-admin-credentials.local.json').read_text(encoding='utf-8'))
    origin = {'Origin': 'http://localhost'}
    report = []
    destination = ROOT / '.runtime/live-research-verification.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TestClient(app, base_url='http://localhost') as client:
        if args.sqlite:
            login = client.post('/api/auth/register', headers=origin, json={'username': 'smoke_' + secrets.token_hex(4), 'nickname': '真实流程验证', 'password': secrets.token_urlsafe(24)})
            assert login.status_code == 201, login.status_code
        else:
            login = client.post('/api/auth/admin/login', headers=origin, json={'username': 'hsq', 'password': credentials['website_admins']['hsq']})
            assert login.status_code == 200, login.status_code
        headers = {**origin, 'X-CSRF-Token': login.json()['csrf_token']}
        try:
            for code in args.codes:
                response = client.post(f'/api/research/{code}/refresh', headers=headers, json={'days': 30, 'max_articles': args.max_articles, 'include_community': args.community})
                assert response.status_code == 202, response.text
                job_id = response.json()['id']
                deadline = time.monotonic() + 600
                previous_stage = ''
                while time.monotonic() < deadline:
                    job = client.get(f'/api/research/jobs/{job_id}').json()
                    if 'stage' not in job:
                        raise RuntimeError(job.get('detail', 'Unable to read job state'))
                    if job['stage'] != previous_stage:
                        print(code + ': ' + job['stage'], flush=True)
                        previous_stage = job['stage']
                    if job['status'] not in ('queued', 'running'):
                        break
                    time.sleep(2)
                else:
                    raise RuntimeError('Job timed out')
                dashboard = client.get(f'/api/dashboard/{code}').json()
                audit = client.get(f'/api/research/{code}/audit').json()
                result = {'code': code, 'job': job, 'daily_bars': len(dashboard['candles']), 'quote_date': dashboard['quote'].get('as_of_date'), 'news_count': len(dashboard['news']), 'analyses': sum(row['analysis'] is not None for row in dashboard['news']), 'audit_count': len(audit.get('audit', [])), 'community_status': dashboard['sentiment']['status'], 'news': [{'title': row['title'], 'url': row['url'], 'score': row['score'], 'analysis': row['analysis']} for row in dashboard['news'][:args.max_articles]]}
                report.append(result)
                # Preserve completed stocks even if a later source or SSH
                # connection fails. Never include login credentials in reports.
                destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            digest = client.get('/api/briefing/preview')
            assert digest.status_code == 200
        finally:
            client.post('/api/auth/logout', headers=headers, json={})
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Saved live verification report: ' + str(destination))


if __name__ == '__main__':
    main()
