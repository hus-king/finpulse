import json
import unittest
from copy import deepcopy

from backend.hot_stock_study import (CLASSES, baseline, bucket, business_asof, digest, entry_index, evaluate,
    graph_evidence, history_features, market_outcome, messages, select_events, stamp, validate_result)
from scripts.hot_stock_experiment import audit_packets
from backend.hot_stock_study import SYSTEM, PATH_PROMPT, VERSION

CALENDAR = ['2026-10-09', '2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16']


def market():
    bars = [{'date': day, 'open': 100, 'close': 100 + i, 'high': 110, 'low': 90, 'volume': 100} for i, day in enumerate(CALENDAR)]
    return {'raw': deepcopy(bars), 'adjusted': deepcopy(bars)}


def stock():
    return {'code': '600028', 'name': '中国石化', 'aliases': ['中国石化', '中石化', '600028'],
            'business': {'id': 'business1', 'code': '600028', 'known_at': '2026-10-08T12:00:00+08:00',
                         'main_business': '石油炼制与化工产品生产销售'}, 'market_before_cutoff': {'return_5': .01}}


def packet():
    return {'id': 'e', 'news': {'id': 'e', 'title': '国际原油价格上涨', 'text': '国际原油价格上涨，石油炼制企业可能面临原料成本变化。中国石化需要核对实际敞口。',
            'available_at': '2026-10-09T16:00:00+08:00', 'published_date': '2026-10-09', 'url': 'https://example.com/a'},
            'stocks': [stock()], 'history': [], 'graph': [], 'horizon': 3, 'entry_policy': 'next_open', 'training_majority': 'negative'}


def result(relation='indirect', forecast='negative', variant='direct'):
    return {'code': '600028', 'relation': relation, 'impact': 'negative', 'forecast': forecast,
            'probabilities': [0, 1, 0, 0, 0] if forecast != 'abstain' else None,
            'news_quote': '国际原油价格上涨', 'business_quote': '石油炼制与化工产品生产销售',
            'explanation': '原料成本与产品定价传导需要核对，不据此保证股价方向。',
            'path': [] if variant == 'direct' else ['能源成本变化。', '炼油业务角色。', '三日传导仍不确定。'], 'unknowns': ['净敞口未知']}


class TimeAndLabelTests(unittest.TestCase):
    def test_return_boundaries_include_three_percent_and_flat(self):
        for value, label in [(-.03, 'strong_negative'), (-.0299, 'negative'), (0, 'flat'), (.0299, 'positive'), (.03, 'strong_positive')]:
            self.assertEqual(bucket(value), label)
        with self.assertRaises(ValueError): bucket(float('nan'))

    def test_processing_buffer_and_weekend_entry(self):
        self.assertEqual(entry_index(CALENDAR, '2026-10-09T09:24:00+08:00'), 0)
        self.assertEqual(entry_index(CALENDAR, '2026-10-09T09:25:00+08:00'), 1)
        self.assertEqual(entry_index(CALENDAR, '2026-10-09T10:00:00+08:00'), 1)
        self.assertEqual(entry_index(CALENDAR, '2026-10-10T10:00:00+08:00'), 1)

    def test_horizon_counts_entry_day_and_uses_open_not_previous_close(self):
        m = market(); m['raw'][1]['open'] = 102; m['adjusted'][1]['open'] = 102
        row = market_outcome(m, CALENDAR, '2026-10-09T16:00:00+08:00', 3)
        self.assertEqual(row['entry_date'], '2026-10-12'); self.assertEqual(row['exit_date'], '2026-10-14')
        self.assertAlmostEqual(row['return'], 103 / 102 - 1)

    def test_immature_labels_stay_missing(self):
        self.assertEqual(market_outcome(market(), CALENDAR, '2026-10-16T16:00:00+08:00', 3)['status'], 'immature')

    def test_missing_stock_session_does_not_shift_horizon(self):
        m = market(); m['raw'].pop(2)
        self.assertEqual(market_outcome(m, CALENDAR, '2026-10-09T16:00:00+08:00', 3)['status'], 'missing_session')

    def test_suspension_and_corporate_actions_are_separate(self):
        m = market(); m['raw'][2]['volume'] = 0
        self.assertEqual(market_outcome(m, CALENDAR, '2026-10-09T16:00:00+08:00', 3)['status'], 'invalid_or_suspended')
        m = market(); m['adjusted'][2]['close'] *= 2
        self.assertEqual(market_outcome(m, CALENDAR, '2026-10-09T16:00:00+08:00', 3)['status'], 'corporate_action')

    def test_company_snapshots_are_point_in_time(self):
        past = stock()['business']; future = {**past, 'id': 'future', 'known_at': '2026-10-12T00:00:00+08:00'}
        self.assertEqual(business_asof([past, future], '600028', '2026-10-09T00:00:00+08:00')['id'], 'business1')
        self.assertIsNone(business_asof([past], '600028', '2026-10-07T00:00:00+08:00'))

    def test_market_features_do_not_include_future_close(self):
        self.assertEqual(history_features(market(), '2026-10-09T14:00:00+08:00')['status'], 'missing')

    def test_sample_selection_is_label_independent_and_date_bounded(self):
        rows = [{'id': str(i), 'broad_candidate': i % 2 == 0, 'available_at': f'2026-10-{9 + i // 2:02d}T12:00:00+08:00', 'future': i} for i in range(20)]
        first = select_events(rows, 8, 42)
        changed = [{**r, 'future': -r['future']} for r in reversed(rows)]
        self.assertEqual([r['id'] for r in first], [r['id'] for r in select_events(changed, 8, 42)])
        self.assertEqual(sum(r['broad_candidate'] for r in first), 4)


class EvidenceAndAblationTests(unittest.TestCase):
    def validate(self, row, variant='direct', p=None):
        p = p or packet()
        return validate_result(json.dumps({'items': [row]}, ensure_ascii=False), p, variant, p['stocks'])

    def test_valid_quotes_and_short_path(self):
        self.assertEqual(self.validate(result())[0]['relation'], 'indirect')
        self.assertEqual(len(self.validate(result(variant='path'), 'path')[0]['path']), 3)

    def test_rejects_invented_news_business_and_direct_identity(self):
        for overrides in ({'news_quote': '未出现的新闻事实'}, {'business_quote': '航空运输业务'}, {'relation': 'direct'}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError): self.validate({**result(), **overrides})

    def test_none_and_uncertain_require_abstention(self):
        for relation in ('none', 'uncertain'):
            with self.assertRaises(ValueError): self.validate(result(relation=relation))
            self.validate(result(relation=relation, forecast='abstain'))

    def test_probabilities_must_match_forecast_and_be_finite(self):
        for probs in ([0, 0, 0, 0, 1], [0, 1, 0, 0], [False, 1, 0, 0, 0], [0, float('nan'), 0, 0, 0]):
            with self.subTest(probs=probs), self.assertRaises(ValueError): self.validate({**result(), 'probabilities': probs})

    def test_missing_duplicate_and_extra_stocks_rejected(self):
        for rows in ([], [result(), result()], [{**result(), 'code': '000001'}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError): validate_result(json.dumps({'items': rows}), packet(), 'direct', [stock()])

    def test_ablations_share_same_news_stocks_and_change_only_additions(self):
        p = packet(); p['history'] = [{'id': 'past'}]; p['graph'] = [{'code': '600028', 'bridge': 'hypothesis'}]
        inputs = {v: json.loads(messages(p, v)[1]['content']) for v in ('direct', 'path', 'rag', 'graph_rag')}
        self.assertEqual(inputs['direct'], inputs['path'])
        self.assertNotIn('historical_cases', inputs['path']); self.assertIn('historical_cases', inputs['rag'])
        self.assertNotIn('evidence_graph', inputs['rag']); self.assertIn('evidence_graph', inputs['graph_rag'])
        self.assertNotIn('training_majority', inputs['direct'])

    def test_graph_links_are_explicit_conditional_hypotheses_with_business_quotes(self):
        p = packet(); graph = graph_evidence(p['news'], p['stocks'])
        self.assertTrue(graph)
        self.assertEqual(graph[0]['bridge_type'], 'conditional_hypothesis')
        self.assertIn(graph[0]['business_quote'], stock()['business']['main_business'])
        self.assertEqual(graph_evidence(p['news'], [{**stock(), 'business': None}]), [])

    def test_runner_audit_rejects_future_facts_and_cases(self):
        p = packet(); plan = {'version': VERSION, 'packets_sha256': digest([p]), 'prompts': {'system': SYSTEM, 'path': PATH_PROMPT}}
        self.assertTrue(audit_packets([p], plan))
        for change in ('business', 'history', 'market'):
            changed = deepcopy(p)
            if change == 'business': changed['stocks'][0]['business']['known_at'] = '2026-10-10T00:00:00+08:00'
            elif change == 'history': changed['history'] = [{'available_at': '2026-10-01T00:00:00+08:00', 'outcome_known_at': '2026-10-10T00:00:00+08:00'}]
            else: changed['stocks'][0]['market_before_cutoff']['last_date'] = '2026-10-10'
            with self.subTest(change=change), self.assertRaises(ValueError): audit_packets([changed], {**plan, 'packets_sha256': digest([changed])})


class MetricTests(unittest.TestCase):
    def gold(self):
        return [{'key': str(i), 'cutoff': '2026-10-09T12:00:00+08:00', 'outcomes': {'3': {'status': 'ok', 'return': r, 'class': bucket(r), 'peer_relative_return': r - .01}}} for i, r in enumerate((.04, .01, -.02, -.04, 0))]

    def test_strong_up_precision_is_not_just_any_up(self):
        predictions = {'0': {'forecast': 'strong_positive'}, '1': {'forecast': 'strong_positive'}, '2': {'forecast': 'positive'}}
        score = evaluate(self.gold(), predictions)
        self.assertEqual(score['positive']['signals'], 3); self.assertEqual(score['positive']['correct'], 2)
        self.assertEqual(score['strong_positive']['signals'], 2); self.assertEqual(score['strong_positive']['correct'], 1)
        self.assertEqual(score['failed_pairs'], 2)
        self.assertEqual(score['classes'][0]['recall_all'], 0)

    def test_abstention_and_failure_do_not_disappear(self):
        score = evaluate(self.gold(), {'0': {'forecast': 'abstain'}})
        self.assertEqual(score['mature_pairs'], 5); self.assertEqual(score['abstentions'], 1)
        self.assertEqual(score['failed_pairs'], 4); self.assertEqual(score['signal_coverage'], 0)
        self.assertIsNone(score['positive']['precision'])
        self.assertEqual(score['confusion']['strong_positive']['abstain'], 1)

    def test_immature_gold_is_not_zero_return(self):
        gold = self.gold(); gold[0]['outcomes']['3'] = {'status': 'immature', 'return': None}
        score = evaluate(gold, {})
        self.assertEqual(score['mature_pairs'], 4); self.assertEqual(score['missing_or_immature'], 1)

    def test_momentum_baseline_uses_only_packet_features(self):
        self.assertEqual(baseline(packet(), stock(), 'no_news_momentum')['forecast'], 'positive')
        self.assertEqual(baseline(packet(), stock(), 'training_majority')['forecast'], 'negative')
        self.assertEqual(baseline(packet(), stock(), 'always_positive')['forecast'], 'positive')


if __name__ == '__main__': unittest.main()
