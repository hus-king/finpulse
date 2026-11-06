"""Audit native research candidates and select a reviewed subset without replacement.

This module does not relabel, establish novelty against an older corpus, or admit
training data. Callers keep their complete source corpus; returned rows are copies.
Event-group counts measure the supplied group_id, not proven independence.
"""
from collections import Counter
from copy import deepcopy

from .contract import digest


TASK_LABELS = {
    'fact_scope': ('COMPANY_EVENT', 'INDUSTRY_SIGNAL'),
    'event_relation': ('DIRECT', 'CONDITIONAL', 'DISTANT', 'SENTIMENT', 'NONE', 'UNKNOWN'),
}
RESEARCH_FLAGS = dict(human_verified=False, independent_test=False,
                      training_admitted=False, delete_news=False)


def _target(target_per_class):
    if isinstance(target_per_class, bool) or not isinstance(target_per_class, int) or target_per_class < 1:
        raise ValueError('target_per_class must be a positive integer')


def _collect(rows):
    seen_ids = {}
    candidates = {}
    invalid = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'].strip():
            raise ValueError('Native row requires a nonempty string id at index ' + str(index))
        identifier = row['id']
        fingerprint = digest(row)
        if identifier in seen_ids:
            detail = 'identical content' if seen_ids[identifier] == fingerprint else 'conflicting content'
            raise ValueError('Duplicate id with ' + detail + ': ' + identifier)
        seen_ids[identifier] = fingerprint
        expected, questions, state = (row.get(k) for k in ('expected', 'questions', 'state'))
        reason = None
        if not isinstance(expected, dict) or not isinstance(questions, dict) or len(expected) != 1 or set(expected) != set(questions):
            reason = 'exactly one matching question and expected label required'
        else:
            question, label = next(iter(expected.items()))
            if question not in TASK_LABELS:
                reason = 'unsupported question'
            elif not isinstance(label, str) or label not in TASK_LABELS[question]:
                reason = 'missing or unsupported label; never remapped to NONE'
            elif not isinstance(questions[question], dict) or not isinstance(questions[question].get('criteria'), dict) or label not in questions[question]['criteria']:
                reason = 'expected label absent from question criteria'
        if not reason and (not isinstance(state, dict) or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('article_id', 'group_id'))):
            reason = 'state, article_id and group_id required'
        if not reason and any(k in row and row[k] is not False for k in RESEARCH_FLAGS):
            reason = 'research flags must be false when present'
        if not reason and question == 'event_relation':
            company = state.get('target_company')
            if not isinstance(company, dict) or not any(isinstance(company.get(k), str) and company[k].strip() for k in ('code', 'name')):
                reason = 'relation row requires target company identity'
        if reason:
            invalid.append({'id': identifier, 'reason': reason})
            continue
        # Different IDs do not make identical model inputs into new observations.
        key = digest({'state': state, 'questions': questions})
        candidates.setdefault(key, []).append(row)
    buckets = {(q, label): [] for q, labels in TASK_LABELS.items() for label in labels}
    duplicates = []
    for candidates_for_input in candidates.values():
        ordered = sorted(candidates_for_input, key=lambda row: row['id'])
        question = next(iter(ordered[0]['expected']))
        if len({row['expected'][question] for row in ordered}) > 1:
            invalid.extend({'id': row['id'], 'reason': 'conflicting labels for identical model input'} for row in ordered)
            continue
        kept = ordered[0]
        buckets[question, kept['expected'][question]].append(kept)
        duplicates.extend({'id': row['id'], 'kept_id': kept['id']} for row in ordered[1:])
    return buckets, sorted(invalid, key=lambda item: item['id']), sorted(duplicates, key=lambda item: item['id'])


def _company(row):
    if 'event_relation' not in row['expected']:
        return ''
    company = row['state']['target_company']
    return next(company[k].strip() for k in ('code', 'name')
                if isinstance(company.get(k), str) and company[k].strip())


def _report(rows, buckets, invalid, duplicates, target):
    classes = {q: {} for q in TASK_LABELS}
    underfilled = []
    available = []
    for (question, label), bucket in buckets.items():
        gap = max(0, target - len(bucket))
        classes[question][label] = {
            'available': len(bucket), 'gap': gap,
            'event_groups': len({row['group_id'] for row in bucket}),
            'articles': len({row['article_id'] for row in bucket}),
            'targets': len({_company(row) for row in bucket} - {''}),
        }
        if gap:
            underfilled.append({'question': question, 'label': label, 'gap': gap})
        available.extend(bucket)
    return {
        'target_per_class': target, 'target_total': target * sum(map(len, TASK_LABELS.values())),
        'input_rows': len(rows), 'available_rows': len(available),
        'event_groups': len({row['group_id'] for row in available}),
        'articles': len({row['article_id'] for row in available}),
        'classes': classes, 'underfilled_classes': underfilled, 'target_met': not underfilled,
        'invalid_rows': invalid, 'duplicate_content_rows': duplicates,
        'event_count_basis': 'Distinct supplied group_id values; independence is not established.',
        **RESEARCH_FLAGS,
    }


def audit_inventory(rows, target_per_class=200):
    """Count eight separate classes; report malformed/conflicting/duplicate rows.

    Duplicate IDs raise even when their content matches. Identical model inputs
    with distinct IDs count once; conflicting labels for one input count zero.
    This audits existing labels without certifying the current annotation rubric.
    """
    _target(target_per_class)
    rows = list(rows)
    return _report(rows, *_collect(rows), target_per_class)


def select_balanced(rows, *, reviewed_ids, target_per_class=200):
    """Select at most the target per class from explicitly reviewed candidate IDs.

    Review here authorizes subset selection only; it does not establish human gold.
    Input order cannot change selection. Scarce classes stay short, without draws
    with replacement. Within each class, prefer less-used groups, then targets.
    """
    _target(target_per_class)
    rows = list(rows)
    source_inventory = audit_inventory(rows, target_per_class)
    reviewed_ids = set(reviewed_ids)
    if any(not isinstance(identifier, str) for identifier in reviewed_ids):
        raise ValueError('reviewed_ids must contain string IDs')
    unknown = reviewed_ids - {row['id'] for row in rows}
    if unknown:
        raise ValueError('Unknown reviewed IDs: ' + ', '.join(sorted(unknown)))
    reviewed = [row for row in rows if row['id'] in reviewed_ids]
    buckets, invalid, duplicates = _collect(reviewed)
    # Selecting one reviewed alias cannot resolve a contradiction already known
    # in the full pool. Review authorizes selection, not label adjudication.
    known_invalid = {item['id']: item for item in source_inventory['invalid_rows']}
    buckets = {key: [row for row in bucket if row['id'] not in known_invalid]
               for key, bucket in buckets.items()}
    invalid = sorted({item['id']: item for item in invalid + [
        known_invalid[identifier] for identifier in reviewed_ids if identifier in known_invalid
    ]}.values(), key=lambda item: item['id'])
    duplicates = [item for item in duplicates if item['id'] not in known_invalid]
    reviewed_inventory = _report(reviewed, buckets, invalid, duplicates, target_per_class)
    selected = []
    for bucket in buckets.values():
        remaining = list(bucket)
        groups, targets = Counter(), Counter()
        for _ in range(min(target_per_class, len(remaining))):
            row = min(remaining, key=lambda row: (groups[row['group_id']], targets[_company(row)], row['id']))
            remaining.remove(row)
            groups[row['group_id']] += 1
            targets[_company(row)] += 1
            selected.append({**deepcopy(row), **RESEARCH_FLAGS})
    selection_inventory = audit_inventory(selected, target_per_class)
    return {
        'rows': selected, 'source_inventory': source_inventory,
        'reviewed_inventory': reviewed_inventory, 'selection_inventory': selection_inventory,
        'unreviewed_ids': sorted(row['id'] for row in rows if row['id'] not in reviewed_ids),
        'target_met': selection_inventory['target_met'], **RESEARCH_FLAGS,
    }
