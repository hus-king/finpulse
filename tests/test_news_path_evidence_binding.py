import copy
import unittest

from experiments.news_processing import path_evidence_binding as binding
from tests import test_news_path_first_dataset as fixtures


class PathEvidenceBindingTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.PathFirstDatasetTests()
        fixture.setUp()
        self.a, self.c, self.raw = fixture.a, fixture.c, fixture.p
        self.raw['intermediates'][0]['bridge_ids'] = ['IGS']

    def test_missing_product_citation_can_bind_only_to_its_already_cited_owner(self):
        original = copy.deepcopy(self.raw)
        pair, delta = binding.validate_saved_path(self.a, self.c, self.raw)
        self.assertEqual(pair['relation'], 'DISTANT')
        self.assertEqual(delta[0]['AI_selected_bridge_ids'], ['IGS'])
        self.assertEqual(delta[0]['backend_bound_bridge_ids'], ['IBS'])
        self.assertEqual(self.raw, original)
        completed, _ = binding.bind_provided_process_evidence(self.a, self.c, self.raw)
        completed['intermediates'][0]['bridge_ids'] = original['intermediates'][0]['bridge_ids']
        self.assertEqual(completed, original)

    def test_binding_cannot_select_an_uncited_owner_or_repair_a_foreign_id(self):
        for ids in [[], ['foreign']]:
            raw = copy.deepcopy(self.raw)
            raw['intermediates'][0]['bridge_ids'] = ids
            completed, delta = binding.bind_provided_process_evidence(self.a, self.c, raw)
            self.assertEqual(completed, raw)
            self.assertEqual(delta, [])
            with self.assertRaises(ValueError):
                binding.validate_saved_path(self.a, self.c, raw)

    def test_different_owners_cannot_be_joined_into_an_input_output_process(self):
        article, catalog = copy.deepcopy(self.a), copy.deepcopy(self.c)
        catalog['T'] = {**copy.deepcopy(catalog['S']), 'name': '另一家公司',
                        'evidence': [catalog['S']['evidence'][0]], 'industry_evidence': []}
        article['industry_context'][0].update(company_code='T', company_name='另一家公司')
        completed, delta = binding.bind_provided_process_evidence(article, catalog, self.raw)
        self.assertEqual(delta, [])
        with self.assertRaises(ValueError):
            binding.validate_saved_path(article, catalog, completed)

    def test_missing_conversion_or_a_shorter_exposure_still_fails(self):
        article, catalog = copy.deepcopy(self.a), copy.deepcopy(self.c)
        catalog['S']['industry_evidence'][0]['text'] = '行业铁矿石基金和钢材基金同时上涨。'
        article['industry_context'][1]['text'] = catalog['S']['industry_evidence'][0]['text']
        with self.assertRaises(ValueError):
            binding.validate_saved_path(article, catalog, self.raw)
        catalog = copy.deepcopy(self.c)
        catalog['A']['evidence'].append({'id': 'BA2', 'text': '本公司直接采购铁矿石。', 'field': 'upstream'})
        with self.assertRaisesRegex(ValueError, 'shorter'):
            binding.validate_saved_path(self.a, catalog, self.raw)
