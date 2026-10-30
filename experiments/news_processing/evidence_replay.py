"""Explicit post-hoc development replay; never rewrites original teacher results."""
import argparse
import json
import shutil
from pathlib import Path

from .contract import digest
from .evidence import article_packet, company_packet, validate_facts, validate_pair
from .evidence_pipeline import _answers, export_review, sha
from .evidence_review import export
from .storage import read_jsonl, write_json, write_jsonl


def replay(source, destination):
    source,destination=Path(source),Path(destination)
    if destination.exists():raise ValueError('Use a new replay directory')
    freeze=json.loads((source/'facts_freeze.json').read_text())
    for name,expected in freeze.items():
        actual=sha(source/name) if (source/name).exists() else None
        if actual!=expected:raise ValueError('Original fact phase changed')
    records=read_jsonl(source/'inputs.jsonl');byid={r['id']:r for r in records}
    articles={r['article_id']:article_packet(r['article_id'],r['state']['news']) for r in records}
    destination.mkdir(parents=True);(destination/'teacher').mkdir()
    shutil.copyfile(source/'inputs.jsonl',destination/'inputs.jsonl')
    declaration={'type':'post_hoc_development_replay','independent_test':False,'human_verified':False,
        'selection_rule':'first structurally valid response by reserved attempt number; otherwise first invalid response',
        'identity_rule':'exact full requested pair ID, or an exact code unique within the original requested batch',
        'semantic_labels_changed':False,'production_enabled':False,'training_admitted':False,
        'source_inputs_sha256':sha(source/'inputs.jsonl'),'source_fact_freeze_sha256':sha(source/'facts_freeze.json'),
        'source_raw_answers_sha256':sha(source/'teacher'/'raw_answers.jsonl'),
        'validator_sha256':sha(Path(__file__).with_name('evidence.py')),
        'parser_sha256':sha(Path(__file__).with_name('evidence_pipeline.py'))}
    write_json(destination/'declaration.json',declaration)
    facts={}
    for mode in ('primary','audit'):
        facts[mode]={}
        for row in read_jsonl(source/'teacher'/('facts_'+mode+'.jsonl')):
            original=row['evidence_valid']
            try:answer=validate_facts(articles[row['id']],row['answer']);valid,error=True,None
            except (ValueError,KeyError,TypeError) as exc:answer,valid,error=row['answer'],False,str(exc)
            facts[mode][row['id']]={'id':row['id'],'answer':answer,'evidence_valid':valid,'error':error,
                                  'original_evidence_valid':original,'post_hoc_revalidation':True,'human_verified':False}
        write_jsonl(destination/'teacher'/('facts_'+mode+'.jsonl'),facts[mode].values())
    attempts={x['attempt']:x for x in read_jsonl(source/'teacher'/'attempts.jsonl')}
    raw=sorted(read_jsonl(source/'teacher'/'raw_answers.jsonl'),key=lambda r:r['attempt'])
    results={mode:{} for mode in facts};errors=[]
    for row in raw:
        if row['stage']!='pairs':continue
        attempt=attempts[row['attempt']];mode=row['mode'];items=[]
        for id_ in attempt['ids']:
            r=byid[id_];fact=facts[mode].get(r['article_id'])
            if not fact or not fact['evidence_valid']:break
            items.append({'news':articles[r['article_id']],'facts':fact['answer'],'company':company_packet(r)})
        if len(items)!=len(attempt['ids']):continue
        try:answers=_answers(items,row['raw'])
        except (ValueError,KeyError,TypeError) as exc:
            errors.append({'attempt':row['attempt'],'error':str(exc)});continue
        for item in items:
            id_=item['company']['id'];answer=answers[id_]
            try:checked=validate_pair(item['news'],item['facts'],item['company'],answer);valid,error=True,None
            except (ValueError,KeyError,TypeError) as exc:checked,valid,error=answer,False,str(exc)
            if results[mode].get(id_,{}).get('evidence_valid'):continue
            if id_ in results[mode] and not valid:continue
            results[mode][id_]={'id':id_,'answer':checked,'evidence_valid':valid,'error':error,
                               'original_attempt':row['attempt'],'post_hoc_revalidation':True,'human_verified':False}
    for mode in results:write_jsonl(destination/'teacher'/('pairs_'+mode+'.jsonl'),results[mode].values())
    write_json(destination/'replay_errors.json',errors)
    summary=export_review(destination,records)
    summary.update(type='post_hoc_development_replay',new_provider_calls=0,source_attempts=len(attempts),
                   original_coverage_not_rewritten=True)
    write_json(destination/'review_summary.json',summary);export(destination)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('destination',type=Path)
    args=parser.parse_args();print(json.dumps(replay(args.source,args.destination),ensure_ascii=False))
