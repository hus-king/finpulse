import unittest

from backend.northbound import parse_flows


def trade(day, net, turnover=1500):
    return dict(TRADE_DATE=day, NET_DEAL_AMT=net, DEAL_AMT=turnover, INDEX_CLOSE_PRICE=2900)


class NorthboundTests(unittest.TestCase):
    def test_disclosure_change_keeps_turnover_and_drops_direction(self):
        trades = [
            trade('2024-08-12', 110), trade('2024-08-13', -140), trade('2024-08-14', 60),
            trade('2024-08-15', 100), trade('2024-08-16', -80), trade('2024-08-19', 150, 2000),
            trade('2024-08-20', None),
        ]
        rows = parse_flows(reversed(trades), '2024-08-19')
        self.assertEqual(len(rows), 6)
        self.assertIsNone(rows[3]['net_5d'])
        self.assertEqual(rows[4]['net_5d'], .5)
        self.assertEqual(rows[4]['net'], -.8)
        self.assertIsNone(rows[5]['net'])
        self.assertIsNone(rows[5]['net_5d'])
        self.assertEqual(rows[5]['turnover'], 20)
        self.assertEqual(rows[5]['turnover_5d'], 16)

    def test_missing_net_stays_missing(self):
        rows = parse_flows([trade('2024-08-16', None)], '2024-08-16')
        self.assertIsNone(rows[0]['net'])
