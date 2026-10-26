"""Independent raw-price, availability and saved-CSV audit of the hot-stock study."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import csv
import json
from pathlib import Path
import math

TZ = timezone(timedelta(hours=8))


def dt(value):
    x = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return x.replace(tzinfo=TZ) if x.tzinfo is None else x.astimezone(TZ)


def classification(value):
    if value <= -.03: return 'strong_negative'
    if value < 0: return 'negative'
    if value == 0: return 'flat'
    if value >= .03: return 'strong_positive'
    return 'positive'


def independent_outcome(market, calendar, cutoff, horizon):
    ready = dt(cutoff) + timedelta(seconds=300)
    days = [day for day in calendar if dt(day + 'T09:30:00+08:00') > ready][:horizon]
    if len(days) != horizon: return 'immature', None, None
    raw = {b['date']: b for b in market['raw']}; adj = {b['date']: b for b in market['adjusted']}
    if any(day not in raw or day not in adj for day in days): return 'missing_session', None, None
    if any(min(raw[d]['open'], raw[d]['close'], adj[d]['open'], adj[d]['close']) <= 0 or raw[d]['volume'] <= 0 for d in days): return 'invalid_or_suspended', None, None
    ratios = [raw[d]['close'] / adj[d]['close'] for d in days]
    if max(ratios) > min(ratios) * 1.005: return 'corporate_action', None, None
    return 'ok', adj[days[-1]]['close'] / adj[days[0]]['open'] - 1, days


def verify(corpus_path, companies_path, prepared, run_dir, evaluated, output):
    corpus = json.loads(corpus_path.read_text()); companies = json.loads(companies_path.read_text())['versions']
    packets = json.loads((prepared / 'packets.json').read_text()); gold = json.loads((prepared / 'gold.json').read_text()); plan = json.loads((prepared / 'plan.json').read_text())
    report = json.loads((evaluated / 'report.json').read_text()); checks = 0
    def check(condition, message):
        nonlocal checks
        if not condition: raise ValueError(message)
        checks += 1
    canonical = lambda x: hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    check(canonical(packets) == plan['packets_sha256'], 'packet hash')
    check(canonical(gold) == plan['gold_sha256'], 'gold hash')
    check(hashlib.sha256(corpus_path.read_bytes()).hexdigest() == plan['inputs_sha256']['corpus'], 'source corpus fingerprint')
    check(hashlib.sha256(companies_path.read_bytes()).hexdigest() == plan['inputs_sha256']['companies'], 'source company fingerprint')
    markets = corpus['markets']; counts = Counter(b['date'] for m in markets.values() for b in m['raw'])
    calendar = sorted(d for d, count in counts.items() if count >= max(3, len(markets) // 2))
    by_id = {n['id']: n for n in corpus['news']}
    for packet in packets:
        cutoff = dt(packet['news']['available_at'])
        sources = [n for n in corpus['news'] if packet['news']['text'] == n['text'][:2400] and dt(n['first_seen_at']) == cutoff]
        check(bool(sources), 'news observed full-text version not found')
        check(packet['news']['hot_rank'] is None, 'invented historical hot rank')
        for stock in packet['stocks']:
            eligible = [v for v in companies if v['code'] == stock['code'] and dt(v['known_at']) <= cutoff]
            latest = max(eligible, key=lambda v: (dt(v['known_at']), v['id'])) if eligible else None
            check((latest['id'] if latest else None) == ((stock['business'] or {}).get('id')), 'company snapshot is not point in time')
            if latest: check(stock['business']['main_business'] == latest['main_business'][:2000], 'company business changed')
            feature = stock['market_before_cutoff']
            if feature.get('last_date'): check(dt(feature['last_date'] + 'T15:00:00+08:00') <= cutoff, 'future market feature')
        current_hashes = {hashlib.sha256(n['text'].encode()).hexdigest() for n in sources}
        for case in packet['history']:
            check(dt(case['outcome_known_at']) < cutoff, 'future case outcome')
            check(case['text_sha256'] not in current_hashes, 'self text retrieval')
            source = by_id[case['article_id']]
            check(case['text'] == source['text'][:700], 'case quote mismatch')
            status, value, days = independent_outcome(markets[case['code']], calendar, case['available_at'], 3)
            check(status == 'ok' and abs(value - case['return_3']) < 1e-12, 'historic case price/return')
            check(case['outcome_known_at'][:10] == days[-1], 'historic maturity date')
        for edge in packet['graph']:
            stock = next(s for s in packet['stocks'] if s['code'] == edge['code'])
            check(edge['business_quote'] in stock['business']['main_business'], 'graph business quote')
            check(edge['news_quote'] in packet['news']['title'] + '\n' + packet['news']['text'], 'graph news quote')
            check(edge['bridge_type'] == 'conditional_hypothesis', 'graph hypothesis presented as fact')
    gold_by_key = {g['key']: g for g in gold}
    for row in gold:
        for h in (1, 3, 5):
            actual = row['outcomes'][str(h)]; status, value, days = independent_outcome(markets[row['code']], calendar, row['cutoff'], h)
            check(status == actual['status'], 'label availability/status')
            if status == 'ok':
                check(abs(value - actual['return']) < 1e-12, 'open-to-close realized return')
                check(actual['entry_date'] == days[0] and actual['exit_date'] == days[-1], 'wrong entry/exit date')
                check(actual['class'] == classification(value), 'class boundary')
    records = [json.loads(p.read_text()) for p in (run_dir / 'responses').glob('*.json')]
    for variant in plan['variants']:
        pairs = [(r['id'], code) for r in records if r['variant'] == variant for code in r['codes']]
        expected = [(p['id'], s['code']) for p in packets for s in p['stocks']]
        check(Counter(pairs) == Counter(expected), 'missing/extra requested batch pair')
    with (evaluated / 'predictions.csv').open(encoding='utf-8-sig') as handle: rows = list(csv.DictReader(handle))
    for variant, metrics in report['results'].items():
        subset = [r for r in rows if r['variant'] == variant and r['horizon'] == '3' and r['label_status'] == 'ok']
        check(len(subset) == metrics['3']['mature_pairs'], 'CSV denominator mismatch')
        for label, classes, threshold in [('positive', ('positive', 'strong_positive'), 0), ('strong_positive', ('strong_positive',), .03)]:
            chosen = [r for r in subset if r['forecast'] in classes]
            correct = sum(float(r['return']) > 0 if threshold == 0 else float(r['return']) >= threshold for r in chosen)
            metric = metrics['3'][label]
            check(len(chosen) == metric['signals'] and correct == metric['correct'], 'CSV positive/strong-positive counts')
            expected = correct / len(chosen) if chosen else None
            check(expected == metric['precision'], 'CSV precision')
        for r in subset: check(abs(float(r['return']) - gold_by_key[r['key']]['outcomes']['3']['return']) < 1e-12, 'CSV return changed')
        check(sum(r['forecast'] == 'failed' for r in subset) == metrics['3']['failed_pairs'], 'failures disappeared')
        check(sum(r['forecast'] == 'abstain' for r in subset) == metrics['3']['abstentions'], 'abstentions disappeared')
    result = {'checks_passed': checks, 'packets': len(packets), 'pairs': len(gold), 'response_batches': len(records),
              'valid_batches': sum(r['status'] == 'valid' for r in records), 'csv_rows': len(rows),
              'independent_raw_price_reconstruction': True, 'future_information_checks': True, 'csv_counts_independently_recomputed': True}
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(result, indent=2)); return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('corpus', 'companies', 'prepared', 'run', 'evaluated', 'output'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(); print(json.dumps(verify(a.corpus, a.companies, a.prepared, a.run, a.evaluated, a.output)))
