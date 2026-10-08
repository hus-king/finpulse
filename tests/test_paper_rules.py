import copy
import unittest
from datetime import datetime, timedelta

from backend.news_cleaning import SHANGHAI
from backend.paper_rules import (
    PaperError, apply_trade, board_rules, fees, new_account, session_state,
    sellable_quantity, valid_quote, value_account,
)

NOW = datetime(2026, 10, 8, 10, tzinfo=SHANGHAI)
STOCK = {'code': '000001', 'name': '平安银行'}
STAR = {'code': '688981', 'name': '中芯国际'}


def cache(now=NOW, price=10):
    return {'candles': [{'date': now.strftime('%Y-%m-%d %H:%M:%S'),
                        'open': price, 'close': price, 'high': price, 'low': price, 'volume': 100}],
            'fetched_at': now.isoformat(), 'error': None}


def quote(now=NOW, price=10):
    return valid_quote(cache(now, price), now)


class PaperRulesTests(unittest.TestCase):
    def assert_error(self, code, fn, *args):
        with self.assertRaises(PaperError) as caught:
            fn(*args)
        self.assertEqual(caught.exception.code, code)

    def test_buy_deducts_shares_and_fees_from_200k_without_mutating_input(self):
        original = new_account()
        before = copy.deepcopy(original)
        account, trade = apply_trade(original, STOCK, 'buy', 100, quote(), NOW)
        self.assertEqual(original, before)
        self.assertEqual(account['cash_fen'], 19_899_499)
        self.assertEqual(trade['fees_fen'], 501)
        self.assertEqual(account['positions']['000001']['quantity'], 100)
        self.assertEqual(account['positions']['000001']['cost_fen'], 100_501)

    def test_buy_today_is_locked_until_next_trade_day_including_holiday(self):
        bought = datetime(2026, 9, 30, 10, tzinfo=SHANGHAI)
        account, _ = apply_trade(new_account(), STOCK, 'buy', 100, quote(bought), bought)
        position = account['positions']['000001']
        self.assertEqual(sellable_quantity(position, bought), 0)
        self.assertEqual(sellable_quantity(position, datetime(2026, 10, 1, 10, tzinfo=SHANGHAI)), 0)
        self.assertEqual(sellable_quantity(position, NOW), 100)
        self.assert_error('T_PLUS_ONE', apply_trade, account, STOCK, 'sell', 100, quote(bought), bought)

    def test_sell_next_day_records_net_profit_and_clears_position(self):
        account, _ = apply_trade(new_account(), STOCK, 'buy', 100, quote(), NOW)
        tomorrow = NOW + timedelta(days=1)
        account, trade = apply_trade(account, STOCK, 'sell', 100, quote(tomorrow, 10.5), tomorrow)
        self.assertEqual(trade['stamp_fen'], 53)
        self.assertEqual(trade['realized_pnl_fen'], 3945)
        self.assertEqual(account['cash_fen'], 20_003_945)
        self.assertEqual(account['positions'], {})

    def test_fee_components_and_half_cent_boundaries(self):
        self.assertEqual(fees(100_000, 'buy'), {'commission_fen': 500, 'transfer_fen': 1, 'stamp_fen': 0, 'fees_fen': 501})
        self.assertEqual(fees(100_000, 'sell')['fees_fen'], 551)
        self.assertEqual(fees(50_000, 'buy')['transfer_fen'], 1)
        self.assertEqual(fees(1_668_334, 'buy')['commission_fen'], 501)

    def test_continuous_session_boundaries(self):
        for hour, minute, second, allowed in [(9,29,59,False),(9,30,0,True),(11,29,59,True),
                (11,30,0,False),(12,59,59,False),(13,0,0,True),(14,56,59,True),(14,57,0,False),(15,0,0,False)]:
            with self.subTest(time=(hour, minute, second)):
                self.assertEqual(session_state(NOW.replace(hour=hour, minute=minute, second=second))['can_trade'], allowed)

    def test_closed_and_unknown_calendar_refuse_new_trades(self):
        for day in [datetime(2026,10,1,10,tzinfo=SHANGHAI), datetime(2026,10,10,10,tzinfo=SHANGHAI),
                    datetime(2027,1,4,10,tzinfo=SHANGHAI)]:
            self.assertFalse(session_state(day)['can_trade'])
            self.assert_error('CALENDAR_UNKNOWN' if day.year == 2027 else 'MARKET_CLOSED',
                              apply_trade, new_account(), STOCK, 'buy', 100, quote(), day)

    def test_main_and_chinext_buy_lots_and_limits(self):
        for code in ['000001', '600036', '300750']:
            stock = {'code': code, 'name': code}
            for quantity in [1, 99, 101, True, 100.0, 0, -100]:
                self.assert_error('INVALID_QUANTITY', apply_trade, new_account(), stock, 'buy', quantity, quote(), NOW)
            account, _ = apply_trade(new_account(), stock, 'buy', 200, quote(), NOW)
            self.assertEqual(account['positions'][code]['quantity'], 200)
        self.assertEqual(board_rules('300750')['max_quantity'], 150_000)
        self.assertEqual(board_rules('600036')['max_quantity'], 1_000_000)
        self.assert_error('INVALID_QUANTITY', apply_trade, new_account(), {'code':'300750','name':'宁德时代'}, 'buy', 150_100, quote(), NOW)

    def test_star_accepts_one_share_steps_after_200_and_rejects_upper_limit(self):
        self.assert_error('INVALID_QUANTITY', apply_trade, new_account(), STAR, 'buy', 199, quote(), NOW)
        account, _ = apply_trade(new_account(), STAR, 'buy', 201, quote(), NOW)
        self.assertEqual(account['positions']['688981']['quantity'], 201)
        self.assert_error('INVALID_QUANTITY', apply_trade, new_account(), STAR, 'buy', 50_001, quote(), NOW)
        for code in ['689009', '430047', 'bad', '123456']:
            self.assert_error('UNSUPPORTED_STOCK', board_rules, code)

    def test_cash_shortage_and_missing_position_rejected(self):
        self.assert_error('INSUFFICIENT_CASH', apply_trade, new_account(), STOCK, 'buy', 100, quote(price=2000), NOW)
        self.assert_error('INSUFFICIENT_POSITION', apply_trade, new_account(), STOCK, 'sell', 100, quote(), NOW)

    def test_sale_cannot_consume_locked_buy_and_reduces_oldest_lot_first(self):
        account, _ = apply_trade(new_account(), STOCK, 'buy', 200, quote(), NOW)
        tomorrow = NOW + timedelta(days=1)
        account, _ = apply_trade(account, STOCK, 'buy', 100, quote(tomorrow), tomorrow)
        self.assert_error('T_PLUS_ONE', apply_trade, account, STOCK, 'sell', 300, quote(tomorrow), tomorrow)
        account, _ = apply_trade(account, STOCK, 'sell', 200, quote(tomorrow), tomorrow)
        position = account['positions']['000001']
        self.assertEqual(position['quantity'], 100)
        self.assertEqual(sellable_quantity(position, tomorrow), 0)

    def test_partial_cost_rounding_is_exhausted_when_star_odd_lot_clears(self):
        account, _ = apply_trade(new_account(), STAR, 'buy', 201, quote(price=1.01), NOW)
        tomorrow = NOW + timedelta(days=1)
        account, _ = apply_trade(account, STAR, 'sell', 200, quote(tomorrow, 1.04), tomorrow)
        self.assertEqual(account['positions']['688981']['cost_fen'], 103)
        account, _ = apply_trade(account, STAR, 'sell', 1, quote(tomorrow, 1.04), tomorrow)
        self.assertEqual(account['positions'], {})
        self.assertEqual(account['cash_fen'] - 20_000_000, account['realized_pnl_fen'])

    def test_main_odd_lot_can_be_kept_or_sold_whole_never_split(self):
        account, _ = apply_trade(new_account(), STOCK, 'buy', 300, quote(), NOW)
        position = account['positions']['000001']
        position['quantity'] = 299
        position['lots'][0]['quantity'] = 299
        tomorrow = NOW + timedelta(days=1)
        for amount in [99, 100, 199, 200, 299]:
            result, _ = apply_trade(account, STOCK, 'sell', amount, quote(tomorrow), tomorrow)
            self.assertEqual(result['positions'].get('000001', {}).get('quantity', 0), 299 - amount)
        for amount in [1, 98, 101, 201, 298]:
            self.assert_error('INVALID_QUANTITY', apply_trade, account, STOCK, 'sell', amount, quote(tomorrow), tomorrow)

    def test_bad_quotes_and_cross_session_data_cannot_execute(self):
        for price in [float('nan'), float('inf'), 0, -1, 'invalid', True]:
            self.assert_error('QUOTE_UNAVAILABLE', valid_quote, cache(price=price), NOW)
        for changed in [{}, {'candles': []}, {**cache(), 'error': 'source failed'},
                        {**cache(NOW-timedelta(days=1)), 'fetched_at': NOW.isoformat()}]:
            self.assert_error('QUOTE_UNAVAILABLE' if not changed.get('candles') or changed.get('error') else 'QUOTE_STALE', valid_quote, changed, NOW)
        afternoon = NOW.replace(hour=13)
        old = cache(NOW.replace(hour=11, minute=30)); old['fetched_at'] = afternoon.isoformat()
        self.assert_error('QUOTE_STALE', valid_quote, old, afternoon)

    def test_quote_freshness_exact_boundaries(self):
        for age, allowed in [(180,True),(181,False),(-60,True),(-61,False)]:
            data = cache(NOW - timedelta(seconds=age)); data['fetched_at'] = NOW.isoformat()
            if allowed:
                self.assertEqual(valid_quote(data, NOW)['price_fen'], 1000)
            else:
                self.assert_error('QUOTE_STALE', valid_quote, data, NOW)

    def test_malformed_cache_roots_are_unavailable_and_use_last_fill_for_valuation(self):
        account, _ = apply_trade(new_account(), STOCK, 'buy', 100, quote(), NOW)
        for raw in [None, [], 'invalid']:
            with self.subTest(raw=raw):
                self.assert_error('QUOTE_UNAVAILABLE', valid_quote, raw, NOW)
                result = value_account(account, {'000001': raw}, NOW)
                self.assertEqual(result['market_value_fen'], 100_000)
                self.assertEqual(result['positions'][0]['valuation_status'], 'estimated')

    def test_collection_freshness_exact_boundaries(self):
        for age, allowed in [(90,True),(91,False),(-1,False)]:
            data = cache(); data['fetched_at'] = (NOW-timedelta(seconds=age)).isoformat()
            if allowed:
                self.assertEqual(valid_quote(data, NOW)['price_fen'], 1000)
            else:
                self.assert_error('QUOTE_STALE', valid_quote, data, NOW)

    def test_out_of_session_bar_is_invalid_even_when_fetched_at_is_current(self):
        afternoon = NOW.replace(hour=13)
        data = cache(afternoon - timedelta(minutes=1))
        data['fetched_at'] = afternoon.isoformat()
        self.assert_error('QUOTE_UNAVAILABLE', valid_quote, data, afternoon)

    def test_failed_refresh_preserves_last_valid_cached_price_for_valuation_only(self):
        account, _ = apply_trade(new_account(), STOCK, 'buy', 100, quote(), NOW)
        data = cache(price=11)
        data['error'] = 'temporary fetch failure'
        result = value_account(account, {'000001': data}, NOW)
        self.assertEqual(result['market_value_fen'], 110_000)
        self.assertEqual(result['positions'][0]['valuation_status'], 'stale')
        self.assert_error('QUOTE_UNAVAILABLE', valid_quote, data, NOW)

    def test_valuation_reports_missing_and_stale_prices_without_zeroing_assets(self):
        account, _ = apply_trade(new_account(), STOCK, 'buy', 100, quote(), NOW)
        estimated = value_account(account, {}, NOW)
        self.assertEqual(estimated['market_value_fen'], 100_000)
        self.assertEqual(estimated['total_pnl_fen'], -501)
        self.assertEqual(estimated['positions'][0]['valuation_status'], 'estimated')
        later = NOW + timedelta(days=1)
        stale = value_account(account, {'000001': cache(NOW, 11)}, later)
        self.assertEqual(stale['market_value_fen'], 110_000)
        self.assertEqual(stale['unrealized_pnl_fen'], 9499)
        self.assertEqual(stale['positions'][0]['valuation_status'], 'stale')


if __name__ == '__main__':
    unittest.main()
