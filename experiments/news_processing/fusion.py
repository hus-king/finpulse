"""Blind source checks and explicit AI adjudication, never human ground truth."""
import copy
from collections import Counter

from .contract import COARSE, QUESTIONS, input_digest, validate_reference
from .dataset import ROLES
from .pipeline import quality_reasons
from .sources import assert_role_disjoint, class_group_gate
from .teacher import reference_consensus


def source_reference(record, raw):
    if not isinstance(raw, dict) or raw.get('id') != record['id']: return None
    if raw.get('input_sha256') != input_digest(record) or raw.get('human_verified', False) is not False: return None
    try: ref = validate_reference(record, raw)
    except (ValueError, TypeError): return None
    if ref['own_event'] == 'UNKNOWN' or ref['industry_relation'] == 'UNKNOWN': return None
    return ref


def expected_for(ref):
    return {'own_event': ref['own_event'], 'industry_relation': COARSE[ref['industry_relation']]}


def independent_reviewers(reviews, root):
    ids = [r.get('reviewer_id') for r in reviews+[root]]
    return all(isinstance(i,str) and i.strip() for i in ids) and len(set(ids)) == 3


def assert_weak_training_row(row, inherited_registry=None):
    ref = row['reference']
    if row.get('human_verified') is not False or row['input_sha256'] != input_digest(row):
        raise ValueError('Weak row identity or provenance changed')
    if ref.get('status') not in ('weak_consensus', 'weak_source_adjudication') or ref.get('human_verified') is not False:
        raise ValueError('Unapproved reference status')
    if row['expected'] != ref.get('expected'): raise ValueError('Expected labels differ from reference')
    if set(row['expected']) != set(QUESTIONS) or any(row['expected'][q] not in QUESTIONS[q]['criteria'] for q in QUESTIONS):
        raise ValueError('Invalid expected labels')
    if ref['status'] == 'weak_consensus':
        raw = ref.get('source_reviews')
        inherited = ref.get('inherited_development_consensus')
        if not isinstance(raw,list) or not isinstance(inherited,bool):
            raise ValueError('Teacher-only consensus lacks source/inherited provenance')
        reviews = [source_reference(row,r) for r in raw]
        if any(r is None or not isinstance(r.get('reviewer_id'),str) or not r['reviewer_id'].strip() for r in reviews):
            raise ValueError('Invalid ordinary source-review evidence')
        if inherited:
            entry = (inherited_registry or {}).get(row['id'])
            if entry != {'input_sha256':input_digest(row),'expected':row['expected']}:
                raise ValueError('Inherited consensus is not in frozen input/label registry')
        elif not reviews: raise ValueError('Ordinary consensus requires source review')
        if reviews and (not any(expected_for(r)==row['expected'] for r in reviews)
                        or any(expected_for(r)!=expected_for(reviews[0]) for r in reviews)):
            raise ValueError('Ordinary source review disagrees with expected labels')
    if ref['status'] == 'weak_source_adjudication':
        raw = ref.get('source_reviews', [])
        if len(raw) != 2: raise ValueError('Adjudication requires two blind source reviews')
        reviews = [source_reference(row, r) for r in raw]
        root = source_reference(row, ref.get('root_adjudication'))
        if root is None or any(r is None for r in reviews): raise ValueError('Invalid adjudication evidence')
        if not independent_reviewers(reviews, root): raise ValueError('Reviewer identities are not independent')
        if len(root.get('root_review_rationale', '').strip()) < 10: raise ValueError('Missing explicit root review rationale')
        if any(expected_for(r) != row['expected'] for r in reviews+[root]):
            raise ValueError('Source reviews and root do not agree on labels')


def assert_training_support(datasets, declared_gate):
    if set(datasets) != set(ROLES): raise ValueError('Incomplete training roles')
    assert_role_disjoint([r for rows in datasets.values() for r in rows])
    for role, rows in datasets.items():
        if any(r['split'] != role for r in rows): raise ValueError('Row stored in wrong role')
    actual = class_group_gate(datasets, minimum=2)
    if not declared_gate.get('passed') or not actual['passed']:
        raise ValueError('Missing actual class/event-group support before training')
    if declared_gate.get('group_counts') != actual['group_counts']:
        raise ValueError('Declared training support differs from actual rows')
    return actual


def assemble_reviewed(records, primary, audit, model_identity, source_a, source_b,
                      root_adjudications=None, inherited_ids=None):
    assert_role_disjoint(records)
    root_adjudications, inherited_ids = root_adjudications or {}, inherited_ids or set()
    datasets, queue, registry = {role: [] for role in ROLES}, [], {}
    for record in records:
        rid = record['id']
        consensus = reference_consensus(record, primary.get(rid), audit.get(rid), model_identity)
        raw_reviews = [source_a.get(rid), source_b.get(rid)]
        reviews = [source_reference(record, r) for r in raw_reviews]
        valid = [r for r in reviews if r is not None]
        inherited = rid in inherited_ids
        source_disagreement = len(valid) == 2 and expected_for(valid[0]) != expected_for(valid[1])
        approved = consensus['status'] == 'weak_consensus' and not source_disagreement and ((inherited and not valid) or any(expected_for(r) == consensus['expected'] for r in valid))
        selected = {**consensus, 'source_reviews': valid, 'inherited_development_consensus': inherited,
                    'human_verified': False}
        root = source_reference(record, root_adjudications.get(rid))
        if not approved and not quality_reasons(record) and len(valid) == 2 and root is not None:
            if independent_reviewers(valid, root) and len(root.get('root_review_rationale', '').strip()) >= 10 and all(expected_for(r) == expected_for(root) for r in valid):
                selected.update(status='weak_source_adjudication', expected=expected_for(root),
                    root_adjudication=root, reference_type='independent_AI_source_reviews_and_explicit_AI_adjudication')
                approved = True
        if approved:
            row = {**record, 'expected':copy.deepcopy(selected['expected']), 'reference':copy.deepcopy(selected)}
            if inherited and selected['status']=='weak_consensus':
                registry[rid] = {'input_sha256':input_digest(record),'expected':copy.deepcopy(selected['expected'])}
            assert_weak_training_row(row,registry)
            datasets[record['split']].append(row)
        else:
            selected.update(status='pending_review', expected={})
        queue.append({**selected, 'id':rid, 'split':record['split'], 'quality_reasons':quality_reasons(record),
                      'dual_teacher_consensus':consensus, 'raw_source_reviews':raw_reviews,
                      'source_coarse_disagreement':source_disagreement})
    support = {role:{qid:dict(Counter(r['expected'][qid] for r in rows)) for qid in QUESTIONS}
               for role,rows in datasets.items()}
    return {'datasets':datasets, 'review_queue':queue, 'inherited_reference_registry':registry,
            'training_gate':{**class_group_gate(datasets, minimum=2), 'support':support},
            'development_only':True, 'independent_test':False, 'human_verified':False}
