import unittest
from datetime import datetime

from backend.news_cleaning import SHANGHAI
from backend.replay_market import replay_market_context


class ReplayMarketTests(unittest.TestCase):
    def context(self, clock):
        return replay_market_context({'candles': [{'date': '2026-10-30', 'close': 100}]},
                                     {'refreshing': True, 'error': None},
                                     datetime.fromisoformat(clock).replace(tzinfo=SHANGHAI))

    def test_intraday_uses_last_completed_session(self):
        data = self.context('2026-11-03T10:00:00')
        self.assertEqual(data['completed_through'], '2026-11-02')
        self.assertTrue(data['benchmark']['refreshing'])
        self.assertEqual(data['benchmark']['candles'][0]['close'], 100)

    def test_after_close_and_weekends(self):
        self.assertEqual(self.context('2026-11-03T15:01:00')['completed_through'], '2026-11-03')
        self.assertEqual(self.context('2026-11-01T12:00:00')['completed_through'], '2026-10-30')

    def test_verified_holidays_and_unknown_year_remain_distinct(self):
        data = self.context('2026-10-08T08:00:00')
        days = {day['date']: day['is_trading'] for day in data['calendar']}
        self.assertFalse(days['2026-10-01'])
        self.assertTrue(days['2026-10-08'])
        self.assertIsNone(self.context('2027-01-04T16:00:00')['completed_through'])


if __name__ == '__main__':
    unittest.main()
