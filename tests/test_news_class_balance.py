import copy
import unittest

from experiments.news_processing import class_balance as balance


def row(identifier, label='DIRECT', question='event_relation', group=None, target='000001'):
    return {
        'id': identifier, 'article_id': 'article-' + identifier, 'group_id': group or 'group-' + identifier,
        'state': {'event_evidence': ['event-' + identifier],
                  'target_company': {'code': target, 'name': 'Company ' + target}},
        'questions': {question: {'type': 'choice', 'criteria': {key: key for key in balance.TASK_LABELS[question]}}},
        'expected': {question: label}, 'reference_type': 'AI_source_supported_silver',
    }


class NewsClassBalanceTests(unittest.TestCase):
    def test_scope_and_relation_inventory_has_all_eight_classes_and_gaps(self):
        rows = [row('one'), row('scope', 'INDUSTRY_SIGNAL', 'fact_scope')]
        report = balance.audit_inventory(rows, target_per_class=2)
        self.assertEqual(report['target_total'], 16)
        self.assertEqual(report['available_rows'], 2)
        self.assertEqual(report['classes']['event_relation']['DIRECT']['gap'], 1)
        self.assertEqual(report['classes']['fact_scope']['COMPANY_EVENT']['available'], 0)
        self.assertEqual(len(report['underfilled_classes']), 8)
        self.assertFalse(report['target_met'])

    def test_missing_unknown_and_own_labels_do_not_become_none(self):
        rows = [row('own', 'OWN'), row('missing', None), row('unknown', 'UNKNOWN')]
        report = balance.audit_inventory(rows)
        self.assertEqual(report['classes']['event_relation']['NONE']['available'], 0)
        self.assertEqual(report['classes']['event_relation']['UNKNOWN']['available'], 1)
        self.assertEqual({item['id'] for item in report['invalid_rows']}, {'own', 'missing'})

    def test_duplicate_ids_raise_for_identical_and_conflicting_rows(self):
        original = row('duplicate')
        with self.assertRaisesRegex(ValueError, 'Duplicate id with identical content'):
            balance.audit_inventory([original, copy.deepcopy(original)])
        changed = copy.deepcopy(original)
        changed['expected']['event_relation'] = 'NONE'
        with self.assertRaisesRegex(ValueError, 'Duplicate id with conflicting content'):
            balance.audit_inventory([original, changed])

    def test_duplicate_model_inputs_count_once_and_conflicting_labels_count_zero(self):
        first = row('a')
        duplicate = {**copy.deepcopy(first), 'id': 'b', 'article_id': 'alias'}
        report = balance.audit_inventory([duplicate, first])
        self.assertEqual(report['available_rows'], 1)
        self.assertEqual(report['duplicate_content_rows'], [{'id': 'b', 'kept_id': 'a'}])
        duplicate['expected']['event_relation'] = 'NONE'
        conflict = balance.audit_inventory([first, duplicate])
        self.assertEqual(conflict['available_rows'], 0)
        self.assertEqual(len(conflict['invalid_rows']), 2)
        duplicate['questions']['event_relation']['instructions'] = 'A different question definition'
        self.assertEqual(balance.audit_inventory([first, duplicate])['available_rows'], 2)

    def test_selection_requires_explicit_review_and_never_fills_short_classes(self):
        rows = [row('reviewed'), row('not-reviewed', 'NONE')]
        result = balance.select_balanced(rows, reviewed_ids=['reviewed'], target_per_class=2)
        self.assertEqual([item['id'] for item in result['rows']], ['reviewed'])
        self.assertEqual(result['unreviewed_ids'], ['not-reviewed'])
        self.assertEqual(result['source_inventory']['available_rows'], 2)
        self.assertEqual(result['reviewed_inventory']['available_rows'], 1)
        self.assertFalse(result['target_met'])
        with self.assertRaisesRegex(ValueError, 'Unknown reviewed IDs'):
            balance.select_balanced(rows, reviewed_ids=['absent'])

    def test_review_subset_cannot_bypass_a_known_pool_label_conflict(self):
        first = row('a')
        conflicting = {**copy.deepcopy(first), 'id': 'b', 'article_id': 'alias',
                       'expected': {'event_relation': 'NONE'}}
        result = balance.select_balanced([first, conflicting], reviewed_ids=['a'])
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['reviewed_inventory']['available_rows'], 0)
        self.assertEqual(result['reviewed_inventory']['invalid_rows'], [
            {'id': 'a', 'reason': 'conflicting labels for identical model input'}])
        self.assertEqual(len(result['source_inventory']['invalid_rows']), 2)

    def test_selection_is_deterministic_diversifies_groups_and_then_targets(self):
        rows = [row('a', group='g1', target='t1'), row('b', group='g1', target='t2'),
                row('c', group='g2', target='t1'), row('d', group='g3', target='t2')]
        ids = [item['id'] for item in rows]
        first = balance.select_balanced(rows, reviewed_ids=ids, target_per_class=3)
        second = balance.select_balanced(reversed(rows), reviewed_ids=ids, target_per_class=3)
        self.assertEqual(first['rows'], second['rows'])
        self.assertEqual([item['id'] for item in first['rows']], ['a', 'd', 'c'])
        selected_class = first['selection_inventory']['classes']['event_relation']['DIRECT']
        self.assertEqual(selected_class['event_groups'], 3)
        self.assertEqual(selected_class['targets'], 2)

    def test_all_classes_at_target_are_balanced_without_replacement(self):
        rows = [row(question + '-' + label + '-' + str(i), label, question)
                for question, labels in balance.TASK_LABELS.items() for label in labels for i in range(3)]
        result = balance.select_balanced(rows, reviewed_ids=[item['id'] for item in rows], target_per_class=2)
        self.assertTrue(result['target_met'])
        self.assertEqual(len(result['rows']), 16)
        self.assertEqual(len({item['id'] for item in result['rows']}), 16)
        for classes in result['selection_inventory']['classes'].values():
            self.assertTrue(all(item['available'] == 2 for item in classes.values()))

    def test_selection_preserves_input_and_does_not_promote_silver(self):
        rows = [row('one')]
        before = copy.deepcopy(rows)
        result = balance.select_balanced(rows, reviewed_ids=['one'], target_per_class=1)
        self.assertEqual(rows, before)
        for flag in balance.RESEARCH_FLAGS:
            self.assertIs(result[flag], False)
            self.assertIs(result['rows'][0][flag], False)
        self.assertEqual(result['rows'][0]['reference_type'], 'AI_source_supported_silver')
        result['rows'][0]['state']['event_evidence'].append('changed copy')
        self.assertEqual(rows, before)

    def test_invalid_native_shape_and_true_research_flags_are_reported(self):
        rows = [row('shape'), row('flag'), row('target'), row('criteria')]
        rows[0]['expected']['fact_scope'] = 'INDUSTRY_SIGNAL'
        rows[1]['training_admitted'] = True
        rows[2]['state'].pop('target_company')
        rows[3]['questions']['event_relation']['criteria'].pop('DIRECT')
        report = balance.audit_inventory(rows)
        self.assertEqual(report['available_rows'], 0)
        self.assertEqual(len(report['invalid_rows']), 4)

    def test_target_is_positive_and_200_is_only_the_default(self):
        for target in (0, -1, True, 1.5):
            with self.subTest(target=target), self.assertRaises(ValueError):
                balance.audit_inventory([], target)
        result = balance.select_balanced([row('one')], reviewed_ids=['one'], target_per_class=201)
        self.assertEqual(result['selection_inventory']['target_per_class'], 201)
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['selection_inventory']['classes']['event_relation']['DIRECT']['gap'], 200)
        self.assertFalse(result['target_met'])
