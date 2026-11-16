from datetime import date, timedelta
from unittest import TestCase
from unittest.mock import patch

from scripts import build_stock_bond


def dates(start, count):
    return [(start+timedelta(days=i)).isoformat() for i in range(count)]


class StockBondTests(TestCase):
    def test_flow_uses_opening_assets_and_skips_a_missing_observation(self):
        days = dates(date(2020,1,1),45)
        code = '511010'
        funds = {(day,code):(100,2,1) for day in days}
        for day in days[19:]:
            funds[day,code] = (110,3,1)
        funds.pop((days[21],code))
        flows = build_stock_bond.aggregate_flows(days,funds)

        self.assertAlmostEqual(flows[days[20]], 15.)
        self.assertNotIn(days[41], flows)
        self.assertEqual(flows[days[42]], 0.)

    def test_split_is_not_measured_as_net_subscription(self):
        days = dates(date(2020,1,1),21)
        code = '511010'
        funds = {(day,code):(100,2,1) for day in days}
        funds[days[20],code] = (200,1,2)
        self.assertEqual(build_stock_bond.aggregate_flows(days,funds),{days[20]:0.})

    def test_fixed_basket_starts_when_all_members_have_a_full_window(self):
        days = dates(date(2020,1,1),22)
        funds = {(day,'511010'):(100,2,1) for day in days}
        funds.update({(day,'511260'):(100,3,1) for day in days[1:]})
        funds[days[-1],'511260'] = (110,3,1)
        flows = build_stock_bond.aggregate_flows(days,funds,('511010','511260'))
        self.assertEqual(flows,{days[-1]:6.})

    def test_groups_compare_to_preceding_values_only(self):
        days = dates(date(2020,1,25),37)
        rates = dict.fromkeys(days,2.)
        flows = dict.fromkeys(days,0.)
        rates[days[6]],flows[days[6]] = 1.9,.1
        rates[days[-1]],flows[days[-1]] = 2.1,-.1
        with patch.multiple(build_stock_bond,YIELD_HISTORY=6,FLOW_HISTORY=6):
            groups = build_stock_bond.group_months(days,rates,flows)
        self.assertEqual(groups['low_strong'],[6])
        self.assertEqual(groups['high_weak'],[len(days)-1])

    def test_forward_return_starts_at_second_trading_day_open(self):
        prices = [(100,110)]*22
        prices[1] = (1000,1000)
        prices[2] = (50,60)
        results = build_stock_bond.summarize(prices,[0,1])
        self.assertEqual(results[0],dict(days=20,n=1,mean_pct=120.,median_pct=120.,positive_pct=100.))
        self.assertIsNone(results[1]['mean_pct'])
