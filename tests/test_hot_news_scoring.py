import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from backend.hot_platforms import parse_platform
from backend.hot_news_scoring import validate_rubric, score_topic, repetition
from backend.hot_news_board import merge_topics, parse_baidu, parse_weibo, public_board
from backend.tushare_news import configured_sources

NOW = 1793361600


def topic(title='某车企宣布召回指定型号汽车，涉及10万辆', opinion=True):
    quote = title
    return {'id': title, 'title': title, 'summary': '', 'sources': [],
            'screening': {'decision': 'keep', 'channels': ['risk'],
                'event': {'novelty': 'new_development', 'importance': 'major_brand', 'actor': title[:3], 'new_fact': title},
                'rubric': {'identity': 6, 'target': 5, 'scope': 4, 'importance': 18,
                    'attitude': 5 if opinion else 0, 'intensity': 5 if opinion else 0, 'concentration': 0,
                    'action': 5, 'newness': 5, 'traceability': 5,
                    'evidence': {'specificity': quote, 'importance': quote, 'event': quote, 'opinion': quote if opinion else ''}}}}


class ScoringTests(unittest.TestCase):
    def test_safety_and_termination_examples_high_with_observed_attention(self):
        for title in ('尊界相关车型踏板测试争议，厂商发布回应声明', '东鹏饮料发布公告，宣布终止与姆巴佩的代言合作'):
            row = topic(title)
            row['_attention_history'] = [{'at': NOW-i*600, 'platforms': ['baidu', 'douyin', 'zhihu']} for i in range(3)]
            result = score_topic(row, NOW)
            self.assertEqual(result['total_score'], 75)
            self.assertEqual(result['decision'], 'core')
            self.assertEqual(result['repetition_detail']['same_platform_score'], 12)
            self.assertEqual(result['repetition_detail']['cross_platform_score'], 5)

    def test_new_major_event_does_not_require_repeat_or_opinion(self):
        row = topic(opinion=False)
        result = score_topic(row, NOW)
        self.assertEqual(result['total_score'], 48)
        self.assertTrue(result['major_event_supplement'])
        self.assertEqual(result['decision'], 'hot')
        self.assertEqual(result['opinion_evidence_status'], 'unknown')

    def test_vague_event_cannot_enter_by_repetition_alone(self):
        row = topic()
        row['screening']['rubric'].update(identity=0, target=0, scope=0, action=0, newness=0, traceability=0)
        row['_attention_history'] = [{'at': NOW-i*600, 'platforms': ['a', 'b', 'c', 'd', 'e']} for i in range(8)]
        self.assertEqual(score_topic(row, NOW)['decision'], 'exclude')

    def test_repeat_buckets_ignore_retry_and_expired_observations(self):
        row = topic()
        row['_attention_history'] = [{'at': NOW+i, 'platforms': ['baidu']} for i in (-20, -10, 0)]
        row['_attention_history'].append({'at': NOW-86401, 'platforms': ['weibo']})
        result = repetition(row, NOW)
        self.assertLessEqual(result['same_platform_count'], 2)
        self.assertEqual(result['platform_count'], 1)
        original = deepcopy(row)
        self.assertEqual(repetition(row, NOW), repetition(row, NOW))
        self.assertEqual(row, original)

    def test_same_platform_has_more_weight_than_cross_platform(self):
        row = topic()
        row['_attention_history'] = [{'at': NOW-i*600, 'platforms': ['baidu']} for i in range(6)]
        self.assertEqual(repetition(row, NOW)['same_platform_score'], 22)
        row['_attention_history'] = [{'at': NOW, 'platforms': ['a', 'b', 'c', 'd', 'e']}]
        self.assertEqual(repetition(row, NOW)['cross_platform_score'], 8)

    def test_no_fake_quotes_or_unsupported_scores(self):
        row = topic()
        rubric = row['screening']['rubric']
        parsed = validate_rubric(rubric, row['title'])
        self.assertEqual(parsed['traceability'], 2)
        invalid = deepcopy(rubric); invalid['evidence']['opinion'] = '全网一致抵制该品牌'
        with self.assertRaises(ValueError): validate_rubric(invalid, row['title'])
        invalid = deepcopy(rubric); invalid['importance'] = 24
        with self.assertRaises(ValueError): validate_rubric(invalid, row['title'])

    def test_missing_evidence_earns_zero_instead_of_unearned_points(self):
        row = topic()
        rubric = row['screening']['rubric']
        rubric['evidence']['opinion'] = ''
        rubric['evidence']['importance'] = ''
        parsed = validate_rubric(rubric, row['title'])
        self.assertEqual(parsed['attitude'] + parsed['intensity'] + parsed['concentration'], 0)
        self.assertEqual(parsed['importance'], 0)

    def test_candidate_is_not_returned_by_public_api(self):
        row = topic()
        row['newsworthiness'] = {'decision': 'candidate'}
        self.assertEqual(public_board({'items': [row]})['items'], [])


class PlatformTests(unittest.TestCase):
    def test_all_new_platforms_cap_at_twenty(self):
        for source, response in [
            ('bilibili', {'code': 0, 'data': {'trending': {'list': [{'keyword': f'话题{i}', 'heat_score': 12} for i in range(50)]}}}),
            ('zhihu', {'data': [{'target': {'id': str(i+1), 'title': f'问题{i}', 'excerpt': '问题描述'}, 'detail_text': '12万热度'} for i in range(50)]}),
            ('toutiao', {'data': [{'Title': f'新闻{i}', 'ClusterIdStr': str(i+1), 'HotValue': '123'} for i in range(50)]}),
            ('douyin', {'status_code': 0, 'word_list': [{'word': f'话题{i}', 'hot_value': 123} for i in range(50)]}),
        ]:
            with self.subTest(source=source):
                result = parse_platform(source, response)
                self.assertEqual(len(result), 20)
                self.assertEqual(result[-1]['rank'], 20)
                self.assertTrue(all(row['url'].startswith('https://') for row in result))

    def test_baidu_weibo_and_cached_source_caps(self):
        rows = [{'word': f'新闻{i}', 'index': i} for i in range(50)]
        raw = '<!--s-data:'+json.dumps({'data': {'cards': [{'content': rows}]}})+'-->'
        self.assertEqual(len(parse_baidu(raw)), 20)
        self.assertEqual(len(parse_weibo({'data': {'realtime': rows}})), 20)
        items = [{'title': f'新闻{i}', 'rank': i+1, 'heat': None, 'url': 'https://example.com', 'summary': ''} for i in range(50)]
        self.assertEqual(len(merge_topics({'baidu': {'items': items, 'collected_at': 'now'}})), 20)

    def test_only_two_tushare_sources_enabled(self):
        with patch('backend.tushare_news.settings', return_value={'base_url': 'https://example.com', 'token': 'fixture'}):
            self.assertEqual(set(configured_sources()), {'tushare_cls', 'tushare_major'})

    def test_errors_and_html_do_not_become_empty_success(self):
        from backend.providers import ProviderError
        for response in ({'code': -1}, {'data': []}, {}, '<html>login</html>'):
            with self.assertRaises(ProviderError): parse_platform('zhihu', response)


if __name__ == '__main__': unittest.main()
