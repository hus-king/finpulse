from copy import deepcopy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from backend.hot_news_attention import attach_attention, cluster_events, group_reports, validate_groups
from tests.test_hot_news_board import topic, model_row


class AttentionTests(unittest.TestCase):
    def setUp(self):
        titles = ['姆巴佩言论引发争议', '补水啦终止与姆巴佩合作', '东鹏饮料回应姆巴佩争议']
        self.topics = []
        for index, title in enumerate(titles):
            item = topic(title, '隔离测试资料：姆巴佩相关言论引发品牌合作方回应和解约。')
            item['sources'] = [{'id': 'baidu', 'rank': 1 + index, 'url': 'https://example.test/topic', 'stale': False}]
            item['screening'] = model_row(item); self.topics.append(item)
        self.groups = [{'label': '姆巴佩言论争议与品牌解约', 'anchor': '姆巴佩', 'topic_ids': [row['id'] for row in self.topics]}]

    def test_multiple_trend_titles_form_one_event_with_all_news_material(self):
        groups = validate_groups(json.dumps({'groups': self.groups}), self.topics)
        result = attach_attention(group_reports(self.topics, groups), [], 1000)[0]
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

    def test_missing_repeated_unknown_ids_and_unshared_anchor_are_rejected(self):
        for change in ({'topic_ids': [self.topics[0]['id']]}, {'topic_ids': [row['id'] for row in self.topics] + ['unknown']},
                       {'anchor': '公司'}, {'anchor': '另一事件'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_groups(json.dumps({'groups': [{**self.groups[0], **change}]}), self.topics)

    def test_unmentioned_and_empty_groups_retain_every_singleton(self):
        group = {**self.groups[0], 'topic_ids': [row['id'] for row in self.topics[:2]]}
        result = validate_groups(json.dumps({'groups': [group]}), self.topics)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]['topic_ids'], [self.topics[2]['id']])
        self.assertEqual(len(validate_groups('{"groups":[]}', self.topics)), 3)


class GroupProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_compact_ids_are_resolved_and_reasoning_has_a_bounded_budget(self):
        fixture = AttentionTests(); fixture.setUp()
        rows = fixture.topics
        for row in rows: row.pop('screening')
        async def complete(messages, **kwargs):
            self.assertEqual(kwargs['max_tokens'], 8000)
            sent = json.loads(messages[1]['content'])['topics']
            self.assertEqual(sent[0]['id'], 't0')
            self.assertTrue(all(len(row['summary']) <= 120 for row in sent))
            return {'content': json.dumps({'groups': [{'label': '姆巴佩争议', 'anchor': '姆巴佩', 'topic_ids': ['t0', 't1']} ]})}
        store = SimpleNamespace(get=Mock(return_value=None), put=Mock())
        result, warning = await cluster_events(rows, AsyncMock(side_effect=complete), store, ['fixture'])
        self.assertIsNone(warning)
        ids = [member['id'] for row in result for member in row['members']]
        self.assertEqual(set(ids), {row['id'] for row in rows})
