import copy
import unittest

from experiments.news_processing.contract import input_digest
from experiments.news_processing.fusion import assemble_reviewed, assert_weak_training_row, assert_training_support
from tests.test_news_processing import IDENTITY, record, reference, teacher_record


class ReviewFusionTests(unittest.TestCase):
    def setUp(self):
        self.row = record()
        self.row.update(split='train', human_verified=False, input_sha256=input_digest(self.row))
        self.source = dict(reference(), id=self.row['id'], input_sha256=input_digest(self.row), human_verified=False, reviewer_id='source_a')
        self.primary = {self.row['id']: teacher_record(self.row)}
        self.audit = {self.row['id']: teacher_record(self.row, 'audit')}

    def assemble(self, **kwargs):
        return assemble_reviewed([self.row], self.primary, self.audit, IDENTITY,
            kwargs.pop('source_a', {self.row['id']:self.source}),
            kwargs.pop('source_b', {self.row['id']:dict(self.source,reviewer_id='source_b')}), **kwargs)

    def test_consensus_without_matching_source_review_stays_pending(self):
        result = self.assemble(source_a={}, source_b={})
        self.assertEqual(result['datasets']['train'], [])
        self.assertFalse(result['training_gate']['passed'])

    def test_source_input_hash_and_quotes_must_match(self):
        for field, bad in [('input_sha256','other'), ('news_quote','invented quote')]:
            source = dict(self.source); source[field] = bad
            result = self.assemble(source_a={self.row['id']:source}, source_b={})
            self.assertEqual(result['datasets']['train'], [])

    def test_coarse_source_disagreement_is_pending_even_if_teacher_agrees(self):
        other = dict(self.source, reviewer_id='source_b', industry_relation='CONDITIONAL', missing_conditions=['Unknown supplier exposure'])
        self.assertEqual(self.assemble(source_b={self.row['id']:other})['datasets']['train'], [])

    def test_agreement_is_weak_and_multiple_pairs_do_not_fill_group_gate(self):
        result = self.assemble()
        row = result['datasets']['train'][0]
        self.assertFalse(row['human_verified'])
        self.assertEqual(row['reference']['status'], 'weak_consensus')
        self.assertFalse(result['training_gate']['passed'])
        assert_weak_training_row(row)

    def test_disagreement_requires_explicit_root_and_two_source_reviews(self):
        self.audit = {}
        self.assertEqual(self.assemble()['datasets']['train'], [])
        root = dict(self.source, reviewer_id='root', root_review_rationale='Source steel price and literal upstream support cost transmission.')
        self.assertEqual(self.assemble(source_b={}, root_adjudications={self.row['id']:root})['datasets']['train'], [])
        result = self.assemble(root_adjudications={self.row['id']:root})
        row = result['datasets']['train'][0]
        self.assertEqual(row['reference']['status'], 'weak_source_adjudication')
        assert_weak_training_row(row)

    def test_same_reviewer_cannot_supply_two_adjudication_votes(self):
        self.audit = {}
        root = dict(self.source, reviewer_id='root', root_review_rationale='Explicit source cost transmission check.')
        result = self.assemble(source_b={self.row['id']:self.source}, root_adjudications={self.row['id']:root})
        self.assertEqual(result['datasets']['train'], [])

    def test_fake_source_adjudication_or_changed_expected_is_rejected(self):
        row = self.assemble()['datasets']['train'][0]
        fake = copy.deepcopy(row); fake['reference'] = {'status':'weak_source_adjudication','expected':fake['expected']}
        with self.assertRaises(ValueError): assert_weak_training_row(fake)
        row['expected']['own_event'] = 'YES'
        with self.assertRaises(ValueError): assert_weak_training_row(row)

    def test_inherited_consensus_is_explicitly_marked_not_new_blind_review(self):
        row = self.assemble(source_a={},source_b={}, inherited_ids={self.row['id']})['datasets']['train'][0]
        self.assertTrue(row['reference']['inherited_development_consensus'])
        self.assertEqual(row['reference']['source_reviews'], [])

    def test_train_runner_recomputes_support_before_reserving_run(self):
        datasets = self.assemble()['datasets']
        with self.assertRaises(ValueError): assert_training_support(datasets, {'passed':True})


if __name__ == '__main__': unittest.main()
