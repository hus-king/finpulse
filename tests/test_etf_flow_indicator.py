from datetime import date, timedelta
from unittest import TestCase

from backend.etf_flow_indicator import flow_indicator


class FlowIndicatorTests(TestCase):
    def setUp(self):
        start = date(2020, 1, 1)
        self.days = [(start + timedelta(days=i)).isoformat() for i in range(87)]
        self.codes = ('510050', '510300', '510500')
        self.groups = tuple((code,) for code in self.codes)
        self.records = {
            day: {code: dict(code=code, trade_date=day, total_shares=100, unit_nav=2) for code in self.codes}
            for day in self.days
        }

    def test_baseline_precedes_today_and_requires_two_same_direction_groups(self):
        for day in self.days[80:]:
            self.records[day]['510050']['total_shares'] = 110
        for day in self.days[81:]:
            self.records[day]['510050']['total_shares'] = 120
            self.records[day]['510300']['total_shares'] = 110
        series = flow_indicator(self.days, self.records, self.groups)
        first, second = series[:2]

        self.assertEqual(first['date'], self.days[80])
        self.assertEqual((first['agreeing_groups'], first['net'], first['score']), (1, 20, 0))
        self.assertEqual((second['agreeing_groups'], second['net'], second['amount']), (2, 40, 40))
        self.assertAlmostEqual(second['score'], 100 * 40 / 600, places=4)

    def test_missing_day_breaks_plot_and_never_carries_two_day_delta(self):
        self.records[self.days[81]].pop('510050')
        for day in self.days[82:]:
            self.records[day]['510050']['total_shares'] = 130
            self.records[day]['510300']['total_shares'] = 130
        series = {row['date']: row for row in flow_indicator(self.days, self.records, self.groups)}
        for day in self.days[81:83]:
            self.assertIsNone(series[day]['score'])
            self.assertIsNone(series[day]['net'])
        self.assertEqual(series[self.days[83]]['score'], 0)
