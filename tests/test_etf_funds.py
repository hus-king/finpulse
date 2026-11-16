from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from backend.auth import AuthStore
from backend.etf_funds import (
    EtfFundsService, NAMESPACE, SYMBOLS, _flow, expected_session,
)
from backend.news_cleaning import SHANGHAI
from backend.research_store import ResearchStore


def sample(code, day, shares=1000, nav=2.0, turnover=100):
    return dict(code=code, trade_date=day, exchange=SYMBOLS[code],
                total_shares=shares, unit_nav=nav, turnover_cny=turnover,
                shares_source='交易所份额', nav_source='净值', turnover_source='日线')


class EtfFundsTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ResearchStore(AuthStore(Path(self.temp.name) / 'funds.sqlite'))
        self.store.initialize()
        self.now = datetime(2026, 11, 16, 9, tzinfo=SHANGHAI)
        self.collector = Mock()
        self.service = EtfFundsService(self.store, clock=lambda: self.now, collector=self.collector)

    def test_target_does_not_advance_before_nine_or_during_holiday(self):
        self.assertEqual(expected_session(self.now), date(2026, 11, 13))
        self.assertEqual(expected_session(self.now.replace(hour=8)), date(2026, 11, 12))
        self.assertEqual(expected_session(self.now.replace(day=14)), date(2026, 11, 12))
        self.assertEqual(expected_session(self.now.replace(month=10, day=8)), date(2026, 9, 30))

    def test_source_retry_preserves_existing_fields_and_first_observed_time(self):
        day = date(2026, 11, 13)
        self.collector.return_value = dict(rows=[sample('510300', day.isoformat())], errors={})
        self.service.collect(day, day, refresh=True)
        data = self.store.get(NAMESPACE, day.isoformat())
        self.now += timedelta(minutes=20)
        self.collector.return_value['rows'] = [sample('510300', day.isoformat(), nav=None, turnover=None)]
        self.service.collect(day, day, refresh=True)
        self.assertEqual(data, self.store.get(NAMESPACE, day.isoformat()))

    def test_out_of_range_and_duplicate_source_rows_are_rejected(self):
        day = date(2026, 11, 13)
        row = sample('510300', day.isoformat())
        for rows in ([row, row], [sample('510300', '2026-11-12')]):
            self.collector.return_value = dict(rows=rows, errors={})
            with self.assertRaises(ValueError):
                self.service.collect(day, day)
            self.assertIsNone(self.store.get(NAMESPACE, day.isoformat()))

    def test_aggregation_keeps_partial_and_missing_sessions_blank(self):
        d0, d1, d2 = '2026-11-10', '2026-11-12', '2026-11-13'
        for day, first in [(d0, 1), (d1, 3), (d2, 0)]:
            records = {code: sample(code, day, 1000 + first * 100) for code in SYMBOLS}
            if day == d2:
                records.pop('159919')
            self.store.put(NAMESPACE, day, records)
        snapshot = self.service.snapshot()
        self.assertEqual(snapshot['status'], 'stale')
        self.assertEqual(snapshot['latest_date'], d1)
        group = snapshot['groups'][0]
        self.assertEqual(group['history'][-2]['date'], '2026-11-11')
        self.assertIsNone(group['history'][-1]['net_flow'])
        self.assertIsNone(group['rows'][0]['share_change'])

    def test_volume_and_redemptions_remain_separate(self):
        first = date(2026, 1, 5)
        days = [(first + timedelta(days=i)).isoformat() for i in range(62)]
        records = {day: {'510050': sample('510050', day)} for day in days}
        records[days[-1]]['510050'] = sample('510050', days[-1], shares=900, turnover=190)
        group = self.service._group('sh000016', '上证50', {'510050': 'ETF'}, days, records, {})
        row = group['rows'][0]
        self.assertEqual(row['share_change'], -100)
        self.assertEqual(row['net_flow'], -200)
        self.assertEqual(row['flow_alert'], 'out')
        self.assertEqual(row['flow_5d'], -200)
        self.assertAlmostEqual(row['volume_ratio'], 1.9)
        self.assertFalse(row['volume_alert'])
        self.assertEqual(group['history'][-1]['net_flow'], -200)

    def test_nav_discontinuity_has_no_estimated_flow(self):
        earlier = sample('510300', '2026-11-12', shares=1000, nav=2)
        later = sample('510300', '2026-11-13', shares=2000, nav=1)
        self.assertEqual(_flow(later, earlier), (None, None))

    def test_known_small_conversion_has_no_estimated_flow(self):
        earlier = sample('159919', '2019-01-10', shares=1000, nav=3.4)
        later = sample('159919', '2019-01-11', shares=1110, nav=3.1)
        self.assertEqual(_flow(later, earlier), (None, None))
