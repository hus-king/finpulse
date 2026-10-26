import json
import unittest

from backend.hot_news_board import rule_screen, validate_screening
from backend.hot_sectors import present_sectors, rule_sectors


def topic(title, summary=''):
    return {'id': 'test', 'title': title, 'summary': summary}


class SectorTests(unittest.TestCase):
    def test_energy_producer_and_user_have_opposite_possible_business_effects(self):
        item = topic('国际原油价格大幅上涨')
        rows = {row['id']: row for row in rule_sectors(item)}
        self.assertEqual(rows['oil_gas']['direction'], 'positive')
        self.assertEqual(rows['aviation']['direction'], 'negative')
        self.assertEqual(rows['refining']['direction'], 'mixed')
        self.assertTrue(all(row['evidence'] in item['title'] for row in rows.values()))
        rows = {row['id']: row for row in rule_sectors(topic('国际原油价格持续下跌'))}
        self.assertEqual(rows['oil_gas']['direction'], 'negative')
        self.assertEqual(rows['aviation']['direction'], 'positive')

    def test_strait_disruption_maps_transport_and_energy_with_conditions(self):
        rows = {row['id']: row for row in rule_sectors(topic('伊朗宣布封锁霍尔木兹海峡'))}
        self.assertEqual(rows['shipping']['direction'], 'mixed')
        self.assertEqual(rows['oil_gas']['direction'], 'positive')
        self.assertEqual(rows['aviation']['direction'], 'negative')

    def test_general_tariffs_do_not_assume_all_exporters_have_same_exposure(self):
        rows = rule_sectors(topic('美国宣布加征进口关税'))
        self.assertEqual(rows[0]['id'], 'export_manufacturing')
        self.assertEqual(rows[0]['direction'], 'mixed')
        rows = rule_sectors(topic('美国降低关税，涵盖汽车产品'))
        self.assertTrue(all(row['direction'] == 'positive' for row in rows))

    def test_denial_fandom_and_stock_price_only_do_not_become_business_benefits(self):
        self.assertEqual(rule_screen(topic('伊朗否认封锁霍尔木兹海峡'))['sectors'], [])
        self.assertEqual(rule_screen(topic('两位明星红毯同框'))['sectors'], [])
        for title in ('原油概念股大幅上涨', '黄金股票股价持续上涨'):
            with self.subTest(title=title):
                self.assertTrue(all(row['direction'] in ('unclear', 'mixed') for row in rule_sectors(topic(title))))

    def test_legacy_snapshots_receive_transparent_read_only_rule_sectors(self):
        item = topic('国际原油价格上涨')
        item['screening'] = {'decision': 'keep', 'method': 'model'}
        rows = present_sectors(item)
        self.assertEqual(rows[0]['name'], '油气开采')
        self.assertEqual(rows[0]['method'], 'rules')
        item['screening']['sectors'] = []
        self.assertEqual(present_sectors(item), [])

    def test_model_sectors_validate_ids_quotes_uniqueness_and_exclusion(self):
        item = topic('国际原油价格上涨')
        good = {'id': 'test', 'decision': 'keep', 'channels': ['cost'], 'reason': '可能改变收入与成本',
                'evidence': item['title'], 'sectors': [{'id': 'oil_gas', 'direction': 'positive',
                'reason': '价格上行可能改善生产端收入', 'evidence': item['title']}]}
        self.assertEqual(validate_screening(json.dumps({'items': [good]}), [item])[0]['sectors'][0]['id'], 'oil_gas')
        for change in ({'id': 'unknown'}, {'evidence': '不存在的新闻事实'}, {'direction': '涨停'}):
            row = {**good, 'sectors': [{**good['sectors'][0], **change}]}
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_screening(json.dumps({'items': [row]}), [item])
        for change in ({'sectors': good['sectors'] * 2}, {'decision': 'exclude', 'channels': []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_screening(json.dumps({'items': [{**good, **change}]}), [item])


if __name__ == '__main__': unittest.main()
