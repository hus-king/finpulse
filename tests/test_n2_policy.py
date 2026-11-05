import copy
from datetime import date, timedelta
import json
import math
from pathlib import Path
import unittest

from backend.quant.n2_policy import (
    N2_STRATEGY_ID, N2_VERSION, allocation_weights, initial_state,
    price_exit_codes, rank_candidates, select_portfolio, within_rebalance_band,
)


def sessions(count, start='2025-01-01'):
    day = date.fromisoformat(start)
    result = []
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def golden_inputs():
    """Synthetic prices are inputs only; expected values came from frozen research code."""
    calendar = sessions(321)
    pegs = [.30, .25, .30, .60, .20, .40]
    rows, histories = [], {}
    for k, peg in enumerate(pegs):
        code = f'60000{k}'
        rows.append(dict(code=code, name='股票' + str(k), peg=peg, industry_l1='行业' + str(k), status='合格'))
        raw, adjusted = [], []
        for i, day in enumerate(calendar[:-1]):
            if k == 4 and i < 220 or k == 2 and i in (70, 71, 150, 151) or k == 3 and i >= 317:
                continue
            close = 30. if k == 5 else (20 + 5 * k) * math.exp(.0003 * (k + 1) * i + .016 * math.sin(i / (4 + k)) + .006 * math.cos(i / 19))
            factor = 1. if i < 180 or k == 5 else 1.5 + .1 * k
            prices = dict(open=close * .995, high=close * 1.01, low=close * .99, close=close)
            adjusted.append(dict(date=day, volume=100000, **prices))
            raw.append(dict(date=day, volume=100000, **{key: value / factor for key, value in prices.items()}))
        histories[code] = dict(raw=raw, adjusted=adjusted)
    return rows, histories, calendar


def ranked(count=15):
    return [dict(code=f'600{i:03}', name='股票' + str(i), peg=(i + 1) / 10, score=1 - i / 100,
                 industry_l1='行业' + str(i)) for i in range(count)]


class N2RankingTests(unittest.TestCase):
    def test_selected_values_ranking_and_invvol_match_frozen_golden(self):
        golden = json.loads((Path(__file__).parent / 'fixtures/n2_policy_golden.json').read_text())
        rows, histories, calendar = golden_inputs()
        for record in golden['cases']:
            as_of = record['as_of']
            actual = rank_candidates(rows, histories, calendar, as_of)
            self.assertEqual([r['code'] for r in actual], record['ranking'])
            for row in actual:
                expected = record['rows'][row['code']]
                self.assertAlmostEqual(row['score'], expected['score'], places=12)
                for key, value in row['factor_values'].items():
                    if expected['factor_values'][key] is None:
                        self.assertIsNone(value)
                    else:
                        self.assertAlmostEqual(value, expected['factor_values'][key], places=12)
                self.assertEqual(row['factor_scores'], expected['factor_scores'])
                self.assertEqual(row['technical_asof'], as_of)
            weights, info = allocation_weights(['600003', '600000'], histories, calendar, as_of)
            for code, value in record['allocation'].items():
                self.assertAlmostEqual(weights[code], value, places=12)
            self.assertEqual(info, record['allocation_info'])
            weights, info = allocation_weights(['600002', '600001'], histories, calendar, as_of)
            for code, value in record['allocation_unclipped'].items():
                self.assertAlmostEqual(weights[code], value, places=12)
                self.assertGreater(value, .4)
                self.assertLess(value, .6)
            self.assertEqual(info, record['allocation_unclipped_info'])
        self.assertEqual(N2_STRATEGY_ID, 'official-four-factor-n2-v1')
        self.assertEqual(N2_VERSION, 'four-factor-n2-invvol60-k9-v1')

    def test_future_prices_and_future_missing_adjustments_cannot_change_prior_rank(self):
        rows, histories, calendar = golden_inputs()
        as_of = calendar[260]
        expected = rank_candidates(rows, histories, calendar, as_of)
        expected_allocation = allocation_weights(['600001', '600002'], histories, calendar, as_of)
        changed = copy.deepcopy(histories)
        for item in changed.values():
            for kind in ('raw', 'adjusted'):
                for bar in item[kind]:
                    if bar['date'] > as_of:
                        bar['close'] = float('nan')
            item['adjusted'] = [b for b in item['adjusted'] if b['date'] <= as_of]
        self.assertEqual(expected, rank_candidates(rows, changed, calendar + ['2030-01-01'], as_of))
        self.assertEqual(expected_allocation, allocation_weights(['600001', '600002'], changed, calendar, as_of))

    def test_short_and_flat_history_stays_in_cross_section_with_neutral_missing_factor(self):
        rows, histories, calendar = golden_inputs()
        result = {r['code']: r for r in rank_candidates(rows, histories, calendar, calendar[-2])}
        self.assertIsNone(result['600004']['factor_values']['momentum'])
        self.assertIsNone(result['600004']['factor_values']['lowvol'])
        self.assertEqual(result['600004']['factor_scores']['momentum'], 0)
        self.assertIsNone(result['600005']['factor_values']['momentum'])
        self.assertEqual(len(result), 6)
        singleton = rank_candidates([rows[0]], histories, calendar, calendar[-2])
        self.assertEqual(singleton[0]['factor_scores'], dict.fromkeys(['peg', 'momentum', 'trend', 'lowvol'], 0))
        self.assertEqual(singleton[0]['score'], 0)

    def test_equal_scores_break_by_peg_then_code_and_skip_explicit_ineligible_rows(self):
        calendar = sessions(1)
        rows = [dict(code=c, peg=p, industry_l1='行业') for c, p in [('600002', .2), ('600001', .2)]]
        histories = {c: dict(raw=[dict(date=calendar[0], close=10)], adjusted=[dict(date=calendar[0], close=10)]) for c in ['600001', '600002']}
        rows.append(dict(code='600003', status='数据缺失', peg=None))
        self.assertEqual([r['code'] for r in rank_candidates(rows, histories, calendar, calendar[0])], ['600001', '600002'])

    def test_missing_unaligned_or_bad_past_history_fails_instead_of_narrowing_universe(self):
        rows, histories, calendar = golden_inputs()
        as_of = calendar[-2]
        for modify in [lambda h: h.pop('600001'), lambda h: h['600001']['adjusted'].pop(10),
                       lambda h: h['600001']['raw'][20].update(close=0)]:
            changed = copy.deepcopy(histories)
            modify(changed)
            with self.assertRaises(ValueError):
                rank_candidates(rows, changed, calendar, as_of)
        with self.assertRaises(ValueError):
            rank_candidates(rows + [rows[0]], histories, calendar, as_of)


class N2AllocationTests(unittest.TestCase):
    def test_invvol_caps_at_sixty_and_single_slot_keeps_half_cash(self):
        calendar = sessions(70)
        histories = {}
        for code, scale in [('600001', .001), ('600002', .01)]:
            close = 100.
            bars = []
            for i, day in enumerate(calendar):
                close *= 1 + (scale if i % 2 else -scale)
                bars.append(dict(date=day, close=close))
            histories[code] = dict(raw=bars, adjusted=copy.deepcopy(bars))
        weights, info = allocation_weights(['600001', '600002'], histories, calendar, calendar[-1])
        self.assertAlmostEqual(weights['600001'], .6)
        self.assertAlmostEqual(weights['600002'], .4)
        self.assertIsNone(info['fallback'])
        self.assertEqual(info['window_start'], calendar[-60])
        self.assertEqual(info['window_end'], calendar[-1])
        self.assertEqual(allocation_weights(['600001'], {}, calendar, calendar[-1])[0], {'600001': .5})
        self.assertEqual(allocation_weights([], {}, calendar, calendar[-1])[0], {})

    def test_fallback_distinguishes_calendar_missing_first_return_and_zero_variance(self):
        calendar = sessions(70)
        bars = [dict(date=d, close=10) for d in calendar]
        histories = {c: dict(raw=copy.deepcopy(bars), adjusted=copy.deepcopy(bars)) for c in ['600001', '600002']}
        for length, reason in [(59, 'insufficient_calendar'), (60, 'missing_full_window'), (70, 'zero_volatility')]:
            weights, info = allocation_weights(histories, histories, calendar, calendar[length - 1])
            self.assertEqual(weights, {'600001': .5, '600002': .5})
            self.assertEqual(info['fallback'], reason)
        with self.assertRaises(ValueError):
            allocation_weights(['600001', '600002', '600003'], histories, calendar, calendar[-1])

    def test_band_is_five_points_of_equity_and_never_overrides_industry_cap(self):
        self.assertTrue(within_rebalance_band(55000, 50000, 100000))
        self.assertFalse(within_rebalance_band(55001, 50000, 100000))
        self.assertTrue(within_rebalance_band(45000, 50000, 100000))
        self.assertFalse(within_rebalance_band(49999, 50000, 100000, 60001))
        with self.assertRaises(ValueError):
            within_rebalance_band(0, 0, 0)


class N2SelectionTests(unittest.TestCase):
    def test_three_valid_rank_absences_once_per_day_and_reappearance_reset(self):
        calendar = sessions(10)
        pool = ranked()
        held = {'600014': {'industry_l1': '行业14'}}
        state = initial_state()
        for index in [0, 1, 2]:
            selection, state = select_portfolio(pool, held, state, calendar[index], calendar)
            self.assertEqual(state['streaks']['600014'], index + 1)
            repeated, again = select_portfolio(pool, held, state, calendar[index], calendar)
            self.assertEqual(again, state)
            self.assertEqual(repeated, selection)
            self.assertEqual('600014' in selection['exit_reasons'], index == 2)
        reset, state = select_portfolio([pool[-1]] + pool[:-1], held, state, calendar[3], calendar)
        self.assertNotIn('600014', reset['exit_reasons'])
        self.assertEqual(state['streaks']['600014'], 0)

    def test_empty_ranking_pauses_confirmation_and_does_not_mutate_input_state(self):
        calendar = sessions(4)
        pool = ranked()
        held = {'600014': {'industry_l1': '行业14'}}
        _, state = select_portfolio(pool, held, initial_state(), calendar[0], calendar)
        before = copy.deepcopy(state)
        plan, paused = select_portfolio([], held, state, calendar[1], calendar)
        self.assertEqual(paused['streaks']['600014'], 1)
        self.assertEqual(paused['evaluated_day'], calendar[0])
        self.assertFalse(plan['exit_reasons'])
        self.assertEqual(state, before)
        _, state = select_portfolio(pool, held, paused, calendar[2], calendar)
        self.assertEqual(state['streaks']['600014'], 2)

    def test_sector_quota_precedes_ranks_and_top_nine_entry_window(self):
        calendar = sessions(1)
        pool = ranked(16)
        for row in pool[:7]:
            row['industry_l1'] = '电子'
        plan, _ = select_portfolio(pool, {}, initial_state(), calendar[0], calendar)
        self.assertEqual(plan['active_candidates'][5]['code'], pool[7]['code'])
        self.assertEqual(plan['selected'], [pool[0]['code'], pool[7]['code']])
        self.assertEqual(len(plan['active_candidates']), 9)
        self.assertEqual(plan['ranks'][pool[7]['code']], 6)

    def test_keeps_top9_held_stock_and_cuts_overflow_and_duplicate_industry(self):
        calendar = sessions(1)
        pool = ranked()
        plan, _ = select_portfolio(pool, {'600008': {}}, initial_state(), calendar[0], calendar)
        self.assertEqual(plan['selected'][0], '600008')
        held = {pool[i]['code']: {} for i in [0, 2, 5]}
        plan, _ = select_portfolio(pool, held, initial_state(), calendar[0], calendar)
        self.assertEqual(plan['exit_reasons'], {'600005': 'exit_rank'})
        pool[2]['industry_l1'] = pool[0]['industry_l1']
        plan, _ = select_portfolio(pool, held, initial_state(), calendar[0], calendar)
        self.assertEqual(plan['exit_reasons']['600002'], 'sector_quota')
        self.assertEqual(plan['selected'], ['600000', '600001'])

    def test_price_pending_and_twenty_complete_session_cooldown(self):
        calendar = sessions(24)
        pool = ranked()
        state = initial_state()
        state['pending']['600000'] = 'price_hard'
        state['cooldowns']['600001'] = calendar[0]
        plan, early = select_portfolio(pool, {'600000': {}}, state, calendar[20], calendar)
        self.assertEqual(plan['exit_reasons']['600000'], 'price_hard')
        self.assertEqual(plan['active_candidates'][0]['code'], '600002')
        self.assertIn('600001', early['cooldowns'])
        plan, ready = select_portfolio(pool, {'600000': {}}, state, calendar[21], calendar)
        self.assertNotIn('600001', ready['cooldowns'])
        self.assertEqual(plan['active_candidates'][0]['code'], '600001')
        self.assertEqual(state['cooldowns'], {'600001': calendar[0]})

    def test_unknown_candidates_are_ignored_but_unknown_held_industry_fails(self):
        calendar = sessions(1)
        pool = ranked()
        pool[0]['industry_l1'] = '未知'
        plan, _ = select_portfolio(pool, {}, initial_state(), calendar[0], calendar)
        self.assertNotIn('600000', plan['ranks'])
        with self.assertRaises(ValueError):
            select_portfolio(pool, {'600000': {}}, initial_state(), calendar[0], calendar)


class N2StopTests(unittest.TestCase):
    def test_multiplicative_factor_supports_flat_entry_bar_and_eighteen_percent_boundary(self):
        history = dict(raw=[dict(date='2026-10-08', close=10), dict(date='2026-10-09', close=8.2)],
                       adjusted=[dict(date='2026-10-08', close=20), dict(date='2026-10-09', close=16.4)])
        state = initial_state()
        state['entries']['600001'] = dict(date='2026-10-08', price_fen=1000)
        self.assertEqual(price_exit_codes(state, {'600001': history}, '2026-10-09'), {'600001': 'price_hard'})
        history['adjusted'][-1]['close'] = 16.40001
        self.assertEqual(price_exit_codes(state, {'600001': history}, '2026-10-09'), {})

    def test_saved_adjusted_anchor_survives_rolling_entry_history_and_excludes_future(self):
        state = initial_state()
        state['entries']['600001'] = dict(date='2020-01-01', price_fen=1000, adjusted_anchor_fen=2000)
        history = dict(raw=[dict(date='2026-10-09', close=8)], adjusted=[dict(date='2026-10-09', close=16)])
        before = copy.deepcopy(state)
        self.assertEqual(price_exit_codes(state, {'600001': history}, '2026-10-09'), {'600001': 'price_hard'})
        history['raw'].append(dict(date='2026-10-12', close=float('nan')))
        self.assertEqual(price_exit_codes(state, {'600001': history}, '2026-10-09'), {'600001': 'price_hard'})
        self.assertEqual(state, before)
        state['entries']['600001'].pop('adjusted_anchor_fen')
        state['entries']['600001']['factor_at_entry'] = 2
        self.assertEqual(price_exit_codes(state, {'600001': history}, '2026-10-09'), {'600001': 'price_hard'})
        state['entries']['600001'].pop('factor_at_entry')
        with self.assertRaises(ValueError):
            price_exit_codes(state, {'600001': history}, '2026-10-09')


if __name__ == '__main__':
    unittest.main()
