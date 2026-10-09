import unittest
from copy import deepcopy
from datetime import datetime

from backend.evidence import build_overview
from backend.news_cleaning import SHANGHAI
from backend.prompts import PROMPT_VERSION

NOW = datetime(2026, 10, 9, 12, tzinfo=SHANGHAI)


def event(key, score, published, **extra):
    return {'id': key, 'title': key, 'time': published, 'score': score, 'analysis_status': 'completed',
            'analysis': {'version': PROMPT_VERSION, 'assessment': 'positive' if score > 0 else 'negative',
                         'sentiment_score': score, 'confidence': 'medium'}, **extra}


class TimeWeightedOverviewTests(unittest.TestCase):
    def test_recent_negative_dominates_equal_old_positive(self):
        result = build_overview([event('old', 60, '2026-09-25'), event('new', -60, '2026-10-09')], now=NOW)
        self.assertEqual(result['net_score'], -36)
        self.assertEqual(result['status'], 'negative')
        self.assertEqual([p['time_weight'] for p in result['score_series']], [.25, 1])
        self.assertEqual([p['weight_share'] for p in result['score_series']], [20, 80])
        self.assertEqual(result['weighting']['half_life_days'], 7)

    def test_one_week_old_has_half_weight_and_same_day_stays_equal(self):
        result = build_overview([event('old', 60, '2026-10-02'), event('new', -60, '2026-10-09')], now=NOW)
        self.assertEqual(result['net_score'], -20)
        equal = build_overview([event('a', 25, '2026-10-09'), event('b', -30, '2026-10-09')], now=NOW)
        self.assertEqual(equal['net_score'], -2.5)

    def test_missing_invalid_and_future_dates_do_not_dilute_valid_news(self):
        news = [event('valid', -40, '2026-10-09'), event('missing', 100, None),
                event('bad', 100, 'invalid'), event('future', 100, '2026-10-10')]
        result = build_overview(news, now=NOW)
        self.assertEqual(result['net_score'], -40)
        self.assertEqual(result['analyzed'], 4)
        self.assertEqual(result['counts']['positive'], 3)
        self.assertEqual(result['weighting']['included'], 1)
        self.assertEqual(result['weighting']['excluded_dates'], 3)
        self.assertEqual(len(result['score_series']), 4)

    def test_only_undated_scored_news_has_no_fabricated_composite(self):
        result = build_overview([event('unknown', 80, None)], now=NOW)
        self.assertIsNone(result['net_score'])
        self.assertEqual(result['analyzed'], 1)
        self.assertEqual(result['counts']['pending'], 0)

    def test_pending_stale_and_invalid_analysis_are_not_weighted(self):
        result = build_overview([event('valid', 25, '2026-10-09'),
                                 event('pending', -100, '2026-10-09', analysis_status='running'),
                                 event('stale', -100, '2026-10-09', refresh_pending=True)], now=NOW)
        self.assertEqual(result['net_score'], 25)
        self.assertEqual(result['weighting']['included'], 1)
        self.assertEqual(result['counts']['pending'], 2)

    def test_publication_timezone_uses_shanghai_calendar_date(self):
        result = build_overview([event('utc', 60, '2026-10-08T16:05:00Z'), event('local', -60, '2026-10-09')], now=NOW)
        self.assertEqual(result['net_score'], 0)
        self.assertEqual([p['time_weight'] for p in result['score_series']], [1, 1])

    def test_old_only_samples_still_produce_finite_mean_without_underflow(self):
        result = build_overview([event('old', 50, '1900-01-01'), event('newer', -50, '1900-01-08')], now=NOW)
        self.assertEqual(result['net_score'], -16.7)

    def test_source_scores_are_preserved_and_calculation_is_order_independent(self):
        news = [event('old', 60, '2026-09-25'), event('new', -60, '2026-10-09')]
        before = deepcopy(news)
        first = build_overview(news, now=NOW)
        second = build_overview(list(reversed(news)), now=NOW)
        self.assertEqual(first, second)
        self.assertEqual(news, before)
        self.assertEqual([p['score'] for p in first['score_series']], [60, -60])
