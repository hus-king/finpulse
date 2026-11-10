from datetime import datetime
import unittest

from backend.cb_research_data import collect_snapshot
from backend.news_cleaning import SHANGHAI


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=SHANGHAI)


def quote(code, when, amount=100_000_000):
    return {
        'f2': 113, 'f6': amount, 'f12': code, 'f13': 1, 'f14': '示例转债',
        'f124': int(when.timestamp()), 'f229': 10, 'f232': '605001',
        'f234': '示例正股', 'f235': 10,
    }


def metadata(code, **extra):
    return {
        'SECUCODE': code + '.SH', 'PAR_VALUE': 100, 'RATING': 'AA',
        'EXPIRE_DATE': '2028-02-01 00:00:00', 'LISTING_DATE': '2026-05-01 00:00:00',
        **extra,
    }


def source(quotes, terms):
    def fetch(url, params):
        if 'push2.' in url:
            return {'data': {'total': len(quotes), 'diff': quotes}}
        return {'code': 0, 'result': {'pages': 1, 'count': len(terms), 'data': terms}}
    return fetch


class ConvertibleBondSourceTest(unittest.TestCase):
    def test_dated_quotes_keep_unverified_inputs_missing(self):
        fetch = source(
            [quote('113001', at(6, 15)), quote('113002', at(5, 15))],
            [metadata('113001'), metadata('113002')],
        )
        result = collect_snapshot(now=at(6, 16), fetch=fetch)
        self.assertEqual(result['as_of'], '2026-11-06')
        self.assertTrue(result['close_confirmed'])
        rows = {row['code']: row for row in result['rows']}
        row = rows['113001.SH']
        self.assertEqual(row['price'], 113)
        self.assertEqual(row['conversion_value'], 100)
        self.assertEqual(row['rating'], 'AA')
        self.assertEqual(row['risk'], 'unknown')
        self.assertIsNone(row['stock_return_20d'])
        self.assertIsNone(row['turnover_20d'])
        self.assertTrue(row['tradable'])
        self.assertIsNone(rows['113002.SH']['price'])
        self.assertFalse(rows['113002.SH']['tradable'])

    def test_intraday_quotes_are_dated_but_never_confirmed_as_close(self):
        fetch = source([quote('113001', at(6, 14))], [metadata('113001')])
        result = collect_snapshot(now=at(6, 14, 1), fetch=fetch)
        self.assertEqual(result['as_of'], '2026-11-06')
        self.assertFalse(result['close_confirmed'])
        self.assertTrue(result['warnings'])

    def test_stale_market_is_not_falsely_dated(self):
        fetch = source([quote('113001', at(5, 15))], [metadata('113001')])
        with self.assertRaisesRegex(ValueError, '收盘报价'):
            collect_snapshot(now=at(6, 16), fetch=fetch)

    def test_announced_delisting_is_visible_but_last_trade_is_not_guessed(self):
        fetch = source(
            [quote('113001', at(6, 15))],
            [metadata('113001', DELIST_DATE='2026-11-12 00:00:00')],
        )
        row = collect_snapshot(now=at(6, 16), fetch=fetch)['rows'][0]
        self.assertEqual(row['risk'], 'exit')
        self.assertIsNone(row.get('last_trade_date'))
