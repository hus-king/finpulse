import json
import unittest
from copy import deepcopy
from backend.hot_stock_scoring import validate_scores


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.article = {'title': '某品牌产品制动测试争议引发广泛关注', 'content': '测试视频引发产品安全争议，厂商宣布配合进一步调查。', 'date_status': 'provider_timestamp'}
        self.stocks = [{'code': '600418', 'name': '江淮汽车', 'business': {'main_business': '汽车的研发、生产及销售', 'status': 'ok'},
                        'relationships': [{'id': 'brand-card', 'text': '测试资料：该品牌由江淮汽车承担生产制造。', 'source': '隔离测试资料', 'url': 'https://example.test/disclosure'}]}]
        self.row = {'code': '600418', 'relation': 'indirect', 'score': -7, 'direction': 'negative', 'confidence': 'medium',
                    'news_evidence': ['测试视频引发产品安全争议'], 'business_evidence': '',
                    'relationship_evidence': [{'id': 'brand-card', 'quote': '该品牌由江淮汽车承担生产制造'}],
                    'mechanism': '安全争议通过制造关系形成股价负面压力。', 'uncertainty': '测试争议不等于缺陷已定性，需跟进调查。'}

    def check(self, row): return validate_scores(json.dumps({'items': [row]}, ensure_ascii=False), self.article, self.stocks)

    def test_nonzero_extremes_are_model_scores_and_relations_have_source_links(self):
        for score in (-10, -1, 1, 10):
            row = {**self.row, 'score': score, 'direction': 'positive' if score > 0 else 'negative'}
            result = self.check(row)[0]
            self.assertEqual(result['score'], score)
            self.assertEqual(result['relationship_evidence'][0]['url'], 'https://example.test/disclosure')
            self.assertEqual(result['confidence'], 'low')

    def test_zero_missing_fraction_boolean_range_and_conflicting_sign_are_rejected(self):
        for score in (0, None, -11, 11, -6.5, True, '-7'):
            with self.subTest(score=score), self.assertRaises(ValueError): self.check({**self.row, 'score': score})
        for patch in ({'direction': 'mixed'}, {'direction': 'positive'}, {'news_evidence': ['编造的新闻引文根本没有出现']}):
            with self.subTest(patch=patch), self.assertRaises(ValueError): self.check({**self.row, **patch})
        row = deepcopy(self.row); row.pop('score')
        with self.assertRaises(ValueError): self.check(row)

    def test_unknown_relation_is_unscored_not_zero_and_no_brand_relationship_is_guessed(self):
        for relation in ('none', 'uncertain'):
            row = {**self.row, 'relation': relation, 'score': None, 'direction': 'unclear', 'news_evidence': [], 'relationship_evidence': []}
            self.assertIsNone(self.check(row)[0]['score'])
            with self.assertRaises(ValueError): self.check({**row, 'score': 0})
        for quotes in ([], [{'id': 'invented', 'quote': '品牌属于该上市公司'}], [{'id': 'brand-card', 'quote': '不存在的生产关系'}]):
            with self.subTest(quotes=quotes), self.assertRaises(ValueError): self.check({**self.row, 'relationship_evidence': quotes})

    def test_direct_relation_needs_stock_identity_and_business_quotes_remain_checked(self):
        with self.assertRaises(ValueError): self.check({**self.row, 'relation': 'direct'})
        with self.assertRaises(ValueError): self.check({**self.row, 'business_evidence': '公司承包了全国全部汽车制造业务'})
