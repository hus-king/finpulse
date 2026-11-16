from copy import deepcopy
import unittest
from backend.hot_news_attention import attach_attention, cluster_events, group_reports
from tests.test_hot_news_board import topic, model_row


class AttentionTests(unittest.TestCase):
    def setUp(self):
        titles = ['姆巴佩言论引发争议', '补水啦终止与姆巴佩合作', '东鹏饮料回应姆巴佩争议']
        self.topics = []
        for index, title in enumerate(titles):
            item = topic(title, '隔离测试资料：姆巴佩相关言论引发品牌合作方回应和解约。')
            item['sources'] = [{'id': 'baidu', 'rank': 1 + index, 'url': 'https://example.test/topic', 'stale': False}]
            item['screening'] = model_row(item); self.topics.append(item)
        self.groups = [{'topic_ids': [row['id'] for row in self.topics]}]

    def test_multiple_trend_titles_form_one_event_with_all_news_material(self):
        result = attach_attention(group_reports(self.topics, self.groups), [], 1000)[0]
        self.assertEqual(result['attention']['mentions'], 3)
        self.assertEqual(result['attention']['platforms'], 1)
        self.assertTrue(result['attention']['priority'])
        self.assertEqual(len(result['members']), 3)
        self.assertIn('东鹏饮料回应', result['_content'])
        self.assertEqual(len(result['sources']), 1)

    def test_raw_event_context_is_grouped_before_a_brand_response_is_screened(self):
        rows = deepcopy(self.topics)
        for item in rows: item.pop('screening')
        result = group_reports(rows, self.groups)[0]
        self.assertNotIn('screening', result)
        self.assertIn('补水啦终止', result['summary'])
        self.assertIn('东鹏饮料回应', result['_content'])

    def test_repeated_reads_and_duplicate_snapshot_do_not_inflate_history(self):
        current = group_reports(self.topics, self.groups)
        first = attach_attention(current, [], 1000)
        same = attach_attention(current, first, 1000)
        self.assertEqual(same[0]['attention']['snapshots'], 1)
        next_snapshot = attach_attention(current, same, 1600)
        self.assertEqual(next_snapshot[0]['attention']['snapshots'], 2)
        self.assertEqual(next_snapshot[0]['attention']['mentions'], 3)
        expired = attach_attention(current, next_snapshot, 90000)
        self.assertEqual(expired[0]['attention']['snapshots'], 1)

    def test_news_copies_and_stale_trends_do_not_fabricate_heat(self):
        rows = deepcopy(self.topics)
        for item in rows: item['sources'][0].update(id='tushare_cls', rank=None)
        result = attach_attention(group_reports(rows, self.groups), [], 1000)[0]
        self.assertEqual(result['attention']['mentions'], 0)
        self.assertEqual(result['attention']['score'], 0)
        self.assertFalse(result['attention']['priority'])
        rows = deepcopy(self.topics)
        for item in rows: item['sources'][0]['stale'] = True
        self.assertEqual(attach_attention(group_reports(rows, self.groups), [], 1000)[0]['attention']['snapshots'], 0)

    def test_similarity_groups_reports_without_truncating_screening_evidence(self):
        rows = [
            topic('南卫股份实控人因涉嫌内幕交易被刑事拘留', '共同报道材料' * 40 + '后续裁定细节甲'),
            topic('南卫股份实控人李平因涉嫌内幕交易被刑事拘留', '共同报道材料' * 40 + '后续裁定细节乙'),
            topic('全美机场航班延误', ''),
        ]
        result = cluster_events(rows)
        self.assertEqual(len(result), 2)
        self.assertEqual([member['id'] for member in result[0]['members']], [row['id'] for row in rows[:2]])
        self.assertEqual(result[1]['id'], rows[-1]['id'])
        self.assertIn('后续裁定细节甲', result[0]['summary'])
        self.assertIn('后续裁定细节乙', result[0]['summary'])

    def test_shared_text_after_prefix_does_not_create_a_match(self):
        shared = '后续详细报道涉及同一个具体项目' * 10
        rows = [topic('苹果发布新品', '甲乙丙丁戊' * 40 + shared),
                topic('央行宣布降准', '己庚辛壬癸' * 40 + shared)]
        self.assertEqual(len(cluster_events(rows)), 2)

    def test_bridge_cannot_join_two_dissimilar_reports(self):
        rows = [topic('甲乙丙丁戊己庚', ''), topic('甲乙丙丁戊己庚辛壬癸子丑寅', ''),
                topic('庚辛壬癸子丑寅', '')]
        result = cluster_events(rows)
        self.assertEqual([len(group['members']) for group in result], [2, 1])

    def test_similar_catalogue_reports_about_distinct_drugs_remain_separate(self):
        rows = [topic('长春高新：控股子公司药品新纳入国家医保目录', ''),
                topic('润都股份：创新药盐酸去甲乌药碱注射液纳入国家医保目录', '')]
        self.assertEqual(len(cluster_events(rows)), 2)
