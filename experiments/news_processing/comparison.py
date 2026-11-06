"""Development comparison against a separate frozen weak source reference."""
from collections import Counter

from .contract import QUESTIONS, input_digest
from .pipeline import quality_reasons


def indexed(rows):
    result = {}
    for row in rows:
        if row['id'] in result: raise ValueError('Duplicate comparison id')
        result[row['id']] = row
    return result


def summarize(records, references, candidates, *, actor_registry=None):
    inputs = indexed(records); refs = indexed(references)
    if not actor_registry: raise ValueError('Frozen actor registry is required')
    def actor(name, role):
        info = actor_registry.get(name, {})
        if info.get('role') != role or not info.get('identity') or not info.get('generation_id'):
            raise ValueError('Unregistered actor or role')
        return info
    def check_bound(rows):
        for rid, row in rows.items():
            if rid not in inputs or row.get('input_sha256') != input_digest(inputs[rid]):
                raise ValueError('Comparison artifact does not match input')
    check_bound(refs)
    for ref in refs.values():
        actors = ref.get('actor_ids', [])
        if len(set(actors)) < 2 or ref.get('human_verified') is not False:
            raise ValueError('Expected separate AI source actors; no human-gold claim')
        generations = {actor(a, 'source')['generation_id'] for a in actors}
        if len(generations) < 2: raise ValueError('References require separate recorded generations')
        if quality_reasons(inputs[ref['id']]): raise ValueError('Pending material cannot be scored')
        if set(ref['expected']) != set(QUESTIONS): raise ValueError('Incomplete reference questions')
        for qid, value in ref['expected'].items():
            if value not in QUESTIONS[qid]['criteria']: raise ValueError('Invalid reference label')
    models = {}
    for name, rows in candidates.items():
        predictions = indexed(rows); check_bound(predictions)
        for rid, pred in predictions.items():
            if not pred.get('actor_id'): raise ValueError('Missing candidate actor')
            candidate_actor = actor(pred['actor_id'], 'candidate')
            source_actors = [actor(a,'source') for a in refs.get(rid, {}).get('actor_ids', [])]
            if any(candidate_actor['identity'] == a['identity'] or candidate_actor['generation_id'] == a['generation_id']
                   for a in source_actors):
                raise ValueError('Candidate cannot grade against its own source reference')
            for qid, value in pred.get('labels', {}).items():
                if qid not in QUESTIONS or value not in {*QUESTIONS[qid]['criteria'], 'ABSTAIN', None}:
                    raise ValueError('Invalid candidate label')
        questions = {}
        for qid in QUESTIONS:
            counts = Counter()
            for rid, ref in refs.items():
                actual = ref['expected'][qid]
                labels = predictions.get(rid, {}).get('labels', {})
                predicted = (labels[qid] or 'ABSTAIN') if qid in labels else 'MISSING'
                counts[(actual, predicted)] += 1
            support = lambda actual: sum(n for (a, _), n in counts.items() if a == actual)
            questions[qid] = {'support':len(refs), 'event_groups':len({inputs[r]['group_id'] for r in refs}),
                'decided':sum(n for (_, p), n in counts.items() if p not in {'ABSTAIN','MISSING'}),
                'abstentions':sum(n for (_, p), n in counts.items() if p=='ABSTAIN'),
                'missing':sum(n for (_, p), n in counts.items() if p=='MISSING'),
                'correct':sum(n for (a, p), n in counts.items() if a == p),
                'confusion':{a:{p:counts[a,p] for p in [*QUESTIONS[qid]['criteria'], 'ABSTAIN','MISSING']}
                             for a in QUESTIONS[qid]['criteria']}}
            if qid == 'own_event':
                questions[qid].update(positive_support=support('YES'), positive_identified=counts['YES','YES'],
                                      negative_support=support('NO'), false_own=counts['NO','YES'])
            else:
                questions[qid].update(clear_support=support('CLEAR'), clear_identified=counts['CLEAR','CLEAR'],
                    clear_retained=counts['CLEAR','CLEAR']+counts['CLEAR','POSSIBLE'],
                    possible_support=support('POSSIBLE'), possible_identified=counts['POSSIBLE','POSSIBLE'],
                    none_support=support('NONE'), none_promoted_clear=counts['NONE','CLEAR'])
        models[name] = questions
    return {'retained':len(records), 'deleted':0, 'scored_weak_pairs':len(refs),
            'unscored_pairs':len(records)-len(refs), 'human_verified':False,
            'independent_test':False, 'production_enabled':False, 'models':models}
