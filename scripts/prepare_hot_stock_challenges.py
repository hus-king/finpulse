"""Supplementary tariff/strait report scenarios, selected by subject, never returns."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import numpy as np

from backend.hot_news_board import normalized_title, rule_screen
from backend.news_cleaning import get_stock_aliases, normalize_url
from backend.hot_stock_study import (baseline, bucket, business_asof, digest, entry_index, graph_evidence,
                                    history_features, market_outcome, stamp, write_json)


def prepare_challenges(corpus_path, companies_path, embeddings_path, base_plan_path, output):
    if (output / 'plan.json').exists(): raise ValueError('Refusing to overwrite a frozen challenge')
    corpus = json.loads(corpus_path.read_text()); companies = json.loads(companies_path.read_text()); plan = json.loads(base_plan_path.read_text())
    z = np.load(embeddings_path); metadata = json.loads(str(z['metadata']))
    if metadata['corpus_sha256'] != hashlib.sha256(corpus_path.read_bytes()).hexdigest(): raise ValueError('Corpus/vector mismatch')
    vectors = {str(k): v / max(float(np.linalg.norm(v)), 1e-12) for k, v in zip(z['ids'], z['vectors'])}
    news = [n for n in corpus['news'] if not n.get('direction_excluded') and n['id'] in vectors and n.get('date_status') == 'body_verified']
    names = {n['code']: n['company'] for n in news}; markets = corpus['markets']; watchlist = sorted(set(names) & set(markets))
    dates = Counter(b['date'] for m in markets.values() for b in m['raw']); calendar = sorted(day for day, count in dates.items() if count >= max(3, len(markets) // 2))
    selected = []
    for phrase in ('美国关税压力下', '伊朗威胁封锁霍尔木兹海峡'):
        candidates = [n for n in news if phrase in n['title']]
        if not candidates: raise ValueError('Missing fixed challenge subject')
        selected.append(min(candidates, key=lambda n: (stamp(n['first_seen_at']), n['id'])))
    selected.sort(key=lambda n: stamp(n['first_seen_at']))
    train_cutoff = min(stamp(n['first_seen_at']) for n in selected)
    training = [n for n in news if stamp(n['first_seen_at']) < train_cutoff and 0 <= (stamp(n['first_seen_at']).date() - stamp(n['publication_date']).date()).days <= 7
                and rule_screen({'id': n['id'], 'title': n['title'], 'summary': n['text'][:1000]})['decision'] == 'keep']
    packets, gold, blind = [], [], []
    for n in selected:
        cutoff = stamp(n['first_seen_at']).isoformat(); text_hash = hashlib.sha256(n['text'].encode()).hexdigest()
        event = {'id': digest([normalized_title(n['title']), text_hash]), 'title': n['title'][:250], 'text': n['text'][:2400],
                 'url': normalize_url(n['url']), 'available_at': cutoff, 'published_date': n['publication_date'], 'hot_rank': None}
        stocks = []
        for code in watchlist:
            business = business_asof(companies['versions'], code, cutoff)
            if business: business = {**business, 'main_business': business['main_business'][:2000]}
            stocks.append({'code': code, 'name': names[code], 'aliases': get_stock_aliases(names[code], {'code': code}),
                           'business': business, 'market_before_cutoff': history_features(markets[code], cutoff)})
        candidates, seen = [], set()
        for old in training:
            old_hash = hashlib.sha256(old['text'].encode()).hexdigest(); old_id = digest([normalized_title(old['title']), old_hash]); url = normalize_url(old['url'])
            if old_id in seen or old_hash == text_hash or url == event['url']: continue
            seen.add(old_id)
            outcome = market_outcome(markets[old['code']], calendar, old['first_seen_at'], 3)
            if outcome['status'] != 'ok' or stamp(outcome['exit_date'] + 'T15:00:00+08:00') >= stamp(cutoff): continue
            candidates.append({'id': old_id, 'article_id': old['id'], 'title': old['title'], 'text': old['text'][:700], 'text_sha256': old_hash,
                               'url': url, 'code': old['code'], 'available_at': stamp(old['first_seen_at']).isoformat(),
                               'outcome_known_at': outcome['exit_date'] + 'T15:00:00+08:00', 'return_3': outcome['return'],
                               'note': '历史联合观察，不证明因果关系', 'similarity': float(vectors[n['id']] @ vectors[old['id']])})
        prior = Counter(bucket(case['return_3']) for case in candidates)
        history, used_codes = [], set()
        for case in sorted(candidates, key=lambda c: (-c['similarity'], c['id'])):
            if case['similarity'] <= 0 or case['code'] in used_codes: continue
            history.append(case); used_codes.add(case['code'])
            if len(history) == 3: break
        packet = {'id': event['id'], 'news': event, 'stocks': stocks, 'history': history, 'graph': graph_evidence(event, stocks),
                  'horizon': 3, 'entry_policy': plan['entry_policy'], 'training_majority': max(CLASSES_LOCAL, key=lambda k: prior[k]) if prior else 'abstain'}
        packets.append(packet)
        outcomes = {code: {str(h): market_outcome(markets[code], calendar, cutoff, h) for h in (1, 3, 5)} for code in watchlist}
        for stock in stocks:
            code = stock['code']; row = {'key': event['id'] + ':' + code, 'event_id': event['id'], 'code': code, 'cutoff': cutoff,
                                       'broad_candidate': True, 'outcomes': outcomes[code]}
            for h, outcome in row['outcomes'].items():
                peers = [values[h]['return'] for other, values in outcomes.items() if other != code and values[h]['status'] == 'ok']
                outcome['peer_reference_return'] = sum(peers) / len(peers) if len(peers) >= 3 else None
                outcome['peer_relative_return'] = outcome['return'] - outcome['peer_reference_return'] if outcome['status'] == 'ok' and outcome['peer_reference_return'] is not None else None
            gold.append(row)
            blind.append({'key': row['key'], 'news': event, 'stock': stock, 'relation_label': None, 'impact_label': None, 'annotator': None,
                          'note': '待独立人工标注；不含未来行情和模型预测'})
    plan.update(experiment='fixed-subject supplementary challenge, not main random pilot', sampling='two fixed title subjects, earliest admitted version per subject; all23 stocks; no outcome selection',
                watchlist=watchlist, selected_events=2, test_event_dates=2, event_limit=2, train_boundary=train_cutoff.isoformat(),
                training_events=len(training), training_cases='varies by query maturity', candidate_events=len(news), test_pool_events=2, pending_events=0,
                case_retrieval='training news before earliest challenge, cases matured before each query; no test-event outcomes; same frozen Qwen cosine',
                packets_sha256=digest(packets), gold_sha256=digest(gold))
    plan['source_sha256']['scripts/prepare_hot_stock_challenges.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for name, value in [('plan.json', plan), ('packets.json', packets), ('gold.json', gold), ('relation-annotation-blind.json', blind)]: write_json(output / name, value)
    return {'events': 2, 'stocks': len(watchlist), 'packets_sha256': plan['packets_sha256'], 'history_counts': [len(p['history']) for p in packets]}


CLASSES_LOCAL = ('strong_negative', 'negative', 'flat', 'positive', 'strong_positive')
if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('corpus', 'companies', 'embeddings', 'base-plan', 'output'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(); print(json.dumps(prepare_challenges(a.corpus, a.companies, a.embeddings, a.base_plan, a.output)))
