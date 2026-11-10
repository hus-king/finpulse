from unittest import TestCase

from backend.cb_research_rules import decide, evaluate_rows


def bond(code, value=100, price=118, **fields):
    return {
        'code': code, 'name': code, 'price': price, 'conversion_value': value,
        'rating': 'AA', 'maturity_date': '2029-01-01', 'turnover_20d': 30_000_000,
        'stock_return_20d': 0.02, 'industry_return_20d': 0.03, 'risk': 'clear',
        'risk_reason': None, 'tradable': True, **fields,
    }


def comparable_pool():
    return [
        bond(f'{band}-{index}', value, value * (1 + premium))
        for band, value, premium in [('low', 89, 0.25), ('mid', 100, 0.18), ('high', 111, 0.13)]
        for index in range(5)
    ]


class ConvertibleBondRuleTests(TestCase):
    def test_excludes_self_from_peers_and_separates_discount_from_stock_shock(self):
        target = bond('target', price=113, stock_return_20d=0.07)
        others = [
            bond('different-rating', rating='AA+'),
            bond('different-maturity', maturity_date='2030-01-02'),
            bond('thin', turnover_20d=19_999_999),
            bond('called', risk='call'),
            bond('call-sensitive', scenario_call_sensitive=True),
        ]
        results = evaluate_rows('2026-11-06', [target, *comparable_pool(), *others], None)
        item = next(row for row in results if row['code'] == 'target')
        self.assertEqual(item['peer_count'], 5)
        self.assertEqual([peer['code'] for peer in item['peers']], [f'mid-{n}' for n in range(5)])
        self.assertAlmostEqual(item['reference_price'], 118)
        self.assertAlmostEqual(item['discount'], (118 - 113) / 113)
        self.assertAlmostEqual(item['up_reference'], 127.05)
        self.assertAlmostEqual(item['upside'], (127.05 - 118) / 113)
        self.assertAlmostEqual(item['downside'], (109.35 - 118) / 113)
        self.assertAlmostEqual(item['relative_return'], 1.07 / 1.03 - 1)
        self.assertEqual(item['rank'], 1)
        self.assertTrue(item['eligible'])

    def test_no_candidate_with_insufficient_peers_at_base_or_stress(self):
        target = bond('target', price=113, stock_return_20d=0.07)
        mid = [bond(f'mid-{n}') for n in range(4)]
        item = next(row for row in evaluate_rows('2026-11-06', [target, *mid], None) if row['code'] == 'target')
        self.assertIsNone(item['reference_price'])
        self.assertEqual(item['peer_count'], 4)
        self.assertFalse(item['eligible'])

        # Each of the five peers is close enough at current parity only.
        mid = [bond(f'mid-{n}', value=96, price=115.2) for n in range(5)]
        item = next(row for row in evaluate_rows('2026-11-06', [target, *mid], None) if row['code'] == 'target')
        self.assertIsNotNone(item['reference_price'])
        self.assertIsNone(item['upside'])
        self.assertFalse(item['eligible'])
        self.assertTrue(item['reasons'])

    def test_expensive_or_weak_stock_does_not_outrank_eligible_bond(self):
        strong = bond('strong', price=113, stock_return_20d=0.08)
        weak = bond('weak', price=100, stock_return_20d=0.01)
        expensive = bond('expensive', price=126, stock_return_20d=0.15)
        results = {row['code']: row for row in evaluate_rows(
            '2026-11-06', [strong, weak, expensive, *comparable_pool()], None,
        )}
        self.assertEqual(results['strong']['rank'], 1)
        self.assertFalse(results['weak']['eligible'])
        self.assertFalse(results['expensive']['eligible'])

    def test_nonmonotone_peer_references_cannot_supply_scenario_rank(self):
        target = bond('target', price=110, stock_return_20d=0.07)
        others = [
            bond(f'mid-{n}', value=100, price=125) for n in range(5)
        ] + [
            bond(f'high-{n}', value=111, price=112) for n in range(7)
        ]
        item = next(row for row in evaluate_rows('2026-11-06', [target, *others], None) if row['code'] == 'target')
        self.assertGreater(item['discount'], 0)
        self.assertIsNone(item['upside'])
        self.assertFalse(item['eligible'])

    def test_switch_uses_strict_net_upside_and_inclusive_downside_limits(self):
        current = bond('old', discount=0.03, relative_return=0.03, upside=0.052, downside=-0.042, rank=None)
        candidate = bond('new', rank=1, eligible=True, upside=0.066, downside=-0.052)
        self.assertEqual(decide([current, candidate], 'old')['action'], 'hold')
        candidate['upside'] = 0.073
        self.assertEqual(decide([current, candidate], 'old')['action'], 'switch')
        candidate['downside'] = -0.0521
        self.assertEqual(decide([current, candidate], 'old')['action'], 'hold')

    def test_two_failed_reviews_trigger_replacement_and_missing_data_does_not(self):
        current = bond('old', discount=0, relative_return=0.03, upside=0.08, downside=-0.05, rank=None)
        candidate = bond('new', rank=1, eligible=True, upside=0.073, downside=-0.049)
        first = decide([current, candidate], 'old')
        self.assertEqual((first['action'], first['thesis_failures']), ('hold', 1))
        second = decide([current, candidate], 'old', previous_thesis_failures=1)
        self.assertEqual((second['action'], second['thesis_failures']), ('switch', 2))
        current['discount'] = None
        unknown = decide([current, candidate], 'old', previous_thesis_failures=1)
        self.assertEqual((unknown['action'], unknown['thesis_failures']), ('unavailable', 0))

    def test_announced_exit_overrides_missing_scores_but_missing_position_does_not(self):
        candidate = bond('new', rank=1, eligible=True, upside=0.073, downside=-0.049)
        self.assertEqual(decide([candidate])['action'], 'enter')
        self.assertEqual(decide([candidate], 'old')['action'], 'unavailable')
        self.assertEqual(decide([candidate, bond('old', risk='unknown')], 'old')['action'], 'unavailable')
        result = decide([candidate, bond('old', risk='call', tradable=False)], 'old')
        self.assertEqual((result['action'], result['candidate_code']), ('exit', 'new'))
