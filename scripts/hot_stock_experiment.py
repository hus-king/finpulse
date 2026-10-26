"""Prepare, run or evaluate frozen event-to-watchlist experiments."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import random
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

from backend.hot_stock_study import (VERSION, VARIANTS, CLASSES, SYSTEM, PATH_PROMPT, baseline, digest,
    evaluate, messages, prepare, stamp, validate_result, write_json)


def now(): return datetime.now(timezone.utc).isoformat()


def audit_packets(packets, plan):
    if plan['version'] != VERSION or digest(packets) != plan['packets_sha256'] or plan['prompts'] != {'system': SYSTEM, 'path': PATH_PROMPT}:
        raise ValueError('Frozen plan/packets/prompts mismatch')
    for packet in packets:
        cutoff = stamp(packet['news']['available_at'])
        for stock in packet['stocks']:
            if stock['business'] and stamp(stock['business']['known_at']) > cutoff: raise ValueError('Future company fact')
            market = stock['market_before_cutoff']
            if market.get('last_date') and stamp(market['last_date'] + 'T15:00:00+08:00') > cutoff: raise ValueError('Future market feature')
        for case in packet['history']:
            if stamp(case['available_at']) >= cutoff or stamp(case['outcome_known_at']) >= cutoff: raise ValueError('Future retrieval case')
            if case['id'] == packet['id'] or case['url'] == packet['news']['url']: raise ValueError('Self retrieval')
        for edge in packet['graph']:
            if stamp(edge['business_known_at']) > cutoff: raise ValueError('Future graph fact')
    return True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def run(prepared, config_path, output, workers=4, limit=None):
    packets = json.loads((prepared / 'packets.json').read_text()); plan = json.loads((prepared / 'plan.json').read_text())
    audit_packets(packets, plan)
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    endpoint = config['base_url'].rstrip('/')
    parsed = urlparse(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Expected a configured HTTPS endpoint')
    if config.get('model') != plan['generator']['model'] or not config.get('api_key'): raise ValueError('Model/config mismatch')
    if not endpoint.endswith('/chat/completions'): endpoint += '/chat/completions'
    output.mkdir(parents=True, exist_ok=True); (output / 'responses').mkdir(exist_ok=True)
    identity = {'version': VERSION, 'model': config['model'], 'provider_host': parsed.hostname,
                'packets_sha256': plan['packets_sha256'], 'plan_sha256': digest(plan), 'generation': plan['generator']}
    manifest_path = output / 'manifest.json'
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old['identity'] != identity: raise ValueError('Existing output belongs to a different experiment')
    else: write_json(manifest_path, {'identity': identity, 'started_at': now(), 'workers': workers})
    for name, expected in plan['source_sha256'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected: raise ValueError('Source fingerprint changed: ' + name)
    stop = threading.Event()
    chosen = packets if limit is None else packets[:limit]
    tasks = [(packet, variant, packet['stocks'][i:i + 4]) for packet in chosen for variant in VARIANTS for i in range(0, len(packet['stocks']), 4)]

    def execute(packet, variant, stocks):
        conversation = messages(packet, variant, stocks)
        request_key = digest([VERSION, config['model'], packet['id'], variant, [stock['code'] for stock in stocks], conversation, plan['generator']])
        path = output / 'responses' / (request_key + '.json')
        if path.exists(): return json.loads(path.read_text())
        record = {'id': packet['id'], 'variant': variant, 'codes': [s['code'] for s in stocks], 'request_sha256': request_key,
                  'input_sha256': digest(conversation), 'status': 'failed', 'started_at': now(), 'attempts': []}
        started = time.monotonic()
        for attempt in range(2):
            if stop.is_set():
                record['attempts'].append({'error': 'stopped_after_upstream_auth_or_quota_error'}); break
            try:
                payload = {'model': config['model'], 'messages': conversation, 'temperature': 0,
                           'thinking': {'type': 'disabled'}, 'max_tokens': [3500, 5000][attempt], 'stream': False}
                request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode(),
                                  headers={'Authorization': 'Bearer ' + config['api_key'], 'Content-Type': 'application/json'})
                with build_opener(NoRedirect()).open(request, timeout=85) as response: body = json.loads(response.read(3_000_000))
                choice = body['choices'][0]; text = choice['message']['content']
                record['attempts'].append({'content': text, 'finish_reason': choice.get('finish_reason'),
                                          'model': body.get('model'), 'usage': body.get('usage', {})})
                if body.get('model') != config['model']: raise ValueError('Returned model mismatch')
                if choice.get('finish_reason') == 'length': raise ValueError('Output limit')
                result = validate_result(text, packet, variant, stocks)
                record.update(status='valid', result=result); break
            except HTTPError as exc:
                record['attempts'].append({'error': 'http_' + str(exc.code)})
                if exc.code in (401, 403, 404, 429): stop.set(); break
            except (URLError, TimeoutError, OSError): record['attempts'].append({'error': 'network_or_timeout'})
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                record['attempts'].append({'error': 'schema_or_evidence', 'reason': str(exc)[:140]})
            conversation = messages(packet, variant, stocks) + [{'role': 'user', 'content':
                '上一轮未通过字段或引文校验。只返回完整JSON，不加字段；每只股票都返回；引文必须连续逐字摘录；none/uncertain必须abstain和null概率；path直接版为空，其他版3条；精简文字。'}]
        record.update(completed_at=now(), seconds=round(time.monotonic() - started, 3))
        write_json(path, record); return record

    done, failed = 0, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(execute, *task) for task in tasks]
        for future in as_completed(futures):
            record = future.result(); done += 1; failed += record['status'] != 'valid'
            if done % 4 == 0 or done == len(tasks): print(json.dumps({'completed': done, 'total': len(tasks), 'failed_batches': failed}), flush=True)
    write_json(output / 'completion.json', {'completed_at': now(), 'tasks': len(tasks), 'failed_batches': failed})


def positive_interval(gold, predictions, strong=False, iterations=500):
    dates = sorted({stamp(g['cutoff']).date().isoformat() for g in gold}); by_date = {}
    for day in dates:
        rows = [g for g in gold if stamp(g['cutoff']).date().isoformat() == day]
        metric = evaluate(rows, predictions, 3)['strong_positive' if strong else 'positive']
        by_date[day] = (metric['correct'], metric['signals'])
    rng = random.Random(42); values = []
    for _ in range(iterations):
        draws = [by_date[rng.choice(dates)] for _ in dates]
        denominator = sum(n for _, n in draws)
        if denominator: values.append(sum(c for c, _ in draws) / denominator)
    values.sort()
    return {'method': 'availability-date block bootstrap; exploratory overlapping windows', 'dates': len(dates),
            'iterations_with_signals': len(values), 'interval95': [values[int(.025 * (len(values) - 1))], values[int(.975 * (len(values) - 1))]] if values else None}


def evaluate_run(prepared, run_dir, output):
    packets = json.loads((prepared / 'packets.json').read_text()); gold = json.loads((prepared / 'gold.json').read_text()); plan = json.loads((prepared / 'plan.json').read_text())
    audit_packets(packets, plan)
    if digest(gold) != plan['gold_sha256']: raise ValueError('Gold fingerprint mismatch')
    predictions = {variant: {} for variant in (*VARIANTS, *plan['baselines'])}
    by_id = {p['id']: p for p in packets}; costs = Counter(); validation = Counter()
    for path in sorted((run_dir / 'responses').glob('*.json')):
        record = json.loads(path.read_text()); packet = by_id[record['id']]
        for attempt in record['attempts']:
            for key, value in attempt.get('usage', {}).items():
                if isinstance(value, int): costs[key] += value
        if record['status'] != 'valid': continue
        stocks = [stock for stock in packet['stocks'] if stock['code'] in record['codes']]
        rows = validate_result(record['attempts'][-1]['content'], packet, record['variant'], stocks)
        if rows != record['result']: raise ValueError('Saved parsed result mismatch')
        validation['revalidated_batches'] += 1
        for row in rows:
            key = packet['id'] + ':' + row['code']
            if key in predictions[record['variant']]: raise ValueError('Duplicate prediction')
            predictions[record['variant']][key] = row
    for packet in packets:
        for stock in packet['stocks']:
            for variant in plan['baselines']:
                predictions[variant][packet['id'] + ':' + stock['code']] = baseline(packet, stock, variant)
    results = {}
    for variant, rows in predictions.items():
        results[variant] = {str(h): evaluate(gold, rows, h) for h in (1, 3, 5)}
        results[variant]['positive_interval'] = positive_interval(gold, rows)
        results[variant]['strong_positive_interval'] = positive_interval(gold, rows, True)
        results[variant]['relations'] = dict(Counter(row['relation'] for row in rows.values()))
        results[variant]['broad_candidates'] = evaluate([g for g in gold if g['broad_candidate']], rows, 3)
        results[variant]['relation_accuracy'] = None
    # A deterministic outcome-label permutation diagnoses a weak null; never a training signal.
    null = {}
    for variant, rows in predictions.items():
        rng = random.Random(42); values = []
        outcome_gold = [g for g in gold if g['outcomes']['3']['status'] == 'ok']
        for _ in range(300):
            shuffled = outcome_gold[:]; rng.shuffle(shuffled)
            permuted = [{**g, 'outcomes': other['outcomes']} for g, other in zip(outcome_gold, shuffled)]
            precision = evaluate(permuted, rows, 3)['positive']['precision']
            if precision is not None: values.append(precision)
        null[variant] = {'note': 'pair-label shuffle descriptive null, not a dependence-robust significance test', 'mean_positive_precision': sum(values) / len(values) if values else None}
    report = {'version': VERSION, 'created_at': now(), 'plan': plan, 'results': results,
              'permutation_diagnostic': null, 'usage': dict(costs), 'validation': dict(validation),
              'relation_gold_available': False, 'no_causal_claim': True, 'production_promotion': False}
    write_json(output / 'report.json', report)
    write_json(output / 'predictions.json', predictions)
    with (output / 'predictions.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['variant', 'key', 'event_id', 'code', 'cutoff', 'horizon', 'forecast', 'relation', 'actual_class', 'return', 'label_status', 'peer_relative_return'])
        writer.writeheader()
        for variant, rows in predictions.items():
            for g in gold:
                pred = rows.get(g['key'])
                for horizon in (1, 3, 5):
                    outcome = g['outcomes'][str(horizon)]
                    writer.writerow({'variant': variant, 'key': g['key'], 'event_id': g['event_id'], 'code': g['code'], 'cutoff': g['cutoff'], 'horizon': horizon,
                        'forecast': pred['forecast'] if pred else 'failed', 'relation': pred['relation'] if pred else '',
                        'actual_class': outcome.get('class'), 'return': outcome['return'], 'label_status': outcome['status'], 'peer_relative_return': outcome.get('peer_relative_return')})
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    for key in ('corpus', 'companies', 'embeddings', 'output'): p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--events', type=int, default=8); p.add_argument('--stocks', type=int, default=12); p.add_argument('--watchlist', default='')
    r = sub.add_parser('run')
    for key in ('prepared', 'config', 'output'): r.add_argument('--' + key, type=Path, required=True)
    r.add_argument('--workers', type=int, choices=range(1, 5), default=4); r.add_argument('--limit', type=int)
    e = sub.add_parser('evaluate')
    for key in ('prepared', 'run', 'output'): e.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.corpus, args.companies, args.embeddings, args.output, args.events, args.stocks, args.watchlist.split(',') if args.watchlist else None)
        print(json.dumps({k: result[k] for k in ('selected_events', 'watchlist', 'training_cases', 'test_event_dates', 'packets_sha256')}))
    elif args.command == 'run': run(args.prepared, args.config, args.output, args.workers, args.limit)
    else:
        args.output.mkdir(parents=True, exist_ok=True); result = evaluate_run(args.prepared, args.run, args.output)
        print(json.dumps({key: value['3']['positive'] for key, value in result['results'].items()}, ensure_ascii=False))
