"""Declared development contrasts and literal admission of selected profile pages."""
import copy
import argparse
import json
import re
import shutil
from pathlib import Path

from . import development
from .contract import digest
from .evidence import article_packet,resolve
from .evidence_pipeline import sha
from .storage import read_jsonl,write_json,write_jsonl
from .subjects import build_registry,bindings,normalized_view

SELECTION={
    '42f9fb274bb70db6ee68':['600276','600196','600031','601857'],
    '8bfbfc3cd2db823ddbac':['600276','600196','000977','601336'],
    '29885d7fcd5331913592':['600031','600703','600196','601919'],
    '4e72feb9af911ba91abf':['600031','002714','601857','601919'],
    '7402da85cd47550de5d9':['000977','600703','601857','600196'],
    '336d08cc7ee0026a386c':['600031','000977','601857','601336'],
    '9294f5ca978687551cf3':['601919','600031','600703','600196'],
    '5a54ed5a7e36483b60bf':['601857','600031','000977','601336'],
}


def admit_profiles(packet,output):
    sources={s['code']:s for s in packet};profiles=[];pending=[]
    if not isinstance(output,dict) or not isinstance(output.get('profiles'),list):raise ValueError('Invalid profile envelope')
    seen=set()
    for p in output['profiles']:
        code=p.get('code')
        try:
            if code not in sources or code in seen:raise ValueError('Duplicate or foreign profile code')
            seen.add(code);source=sources[code];pages={p['page']:p['text'] for p in source['pages']}
            for k in ('name','formal_name'):
                if not isinstance(p.get(k),str) or not p[k].strip():raise ValueError('Missing profile identity')
            identities=p.get('identities')
            if not isinstance(identities,list) or {i.get('role') for i in identities}!={'code','name','formal_name'}:raise ValueError('Incomplete source identities')
            facts=p.get('facts')
            if not isinstance(facts,list):raise ValueError('Missing profile facts')
            profile={'code':code,'name':p['name'],'formal_name':p['formal_name'],'industry':'',
                     'products':[],'upstream':[],'downstream':[],'regions':[],'selected_fact_evidence':[],
                     'identity_evidence':copy.deepcopy(identities),'profile_source':{'url':source['source_url'],'sha256':source['raw_sha256']},
                     'human_verified':False,'reference_type':'independent_AI_selected_verbatim_background'}
            for item in identities+facts:
                page=item.get('pdf_page');quote=item.get('quote')
                if page not in pages or not isinstance(quote,str) or not quote or quote not in pages[page]:raise ValueError('Nonliteral or foreign profile quote')
                if 'role' in item:
                    text=normalized_view(quote)[0]
                    identity=code if item['role']=='code' else p['name'] if item['role']=='name' else p['formal_name']
                    if identity not in text:raise ValueError('Identity quote does not bind requested company')
                    if item['role']=='code' and not re.search(r'(?<!\d)'+code+r'(?!\d)',text):raise ValueError('Partial identity code')
                else:
                    field=item.get('field')
                    if field not in {'products','upstream','downstream','regions'} or not isinstance(item.get('use_boundary'),str) or not item['use_boundary'].strip():raise ValueError('Invalid profile fact field/boundary')
                    profile[field].append(quote)
                    profile['selected_fact_evidence'].append({'fact':quote,'exact_quote':quote,'pdf_page':page,
                        'field':field,'source_sha256':source['raw_sha256'],'url':source['source_url'],
                        'quote_start':pages[page].index(quote),'use_boundary':item['use_boundary'],'human_verified':False})
            if not profile['products']:raise ValueError('No concrete product evidence')
            profiles.append(profile)
        except (ValueError,KeyError,TypeError) as exc:pending.append({'code':code,'error':str(exc),'raw_profile':p,'human_verified':False})
    for code in sources.keys()-seen:pending.append({'code':code,'error':'missing_profile_output','human_verified':False})
    return profiles,pending


def _annual_identities(registry,profiles,packet):
    sources={s['code']:s for s in packet}
    for profile in profiles:
        code=profile['code'];source=sources[code];pages={p['page']:p['text'] for p in source['pages']}
        article=article_packet('profile-'+code,{'title':source['source_url'],'text':'\n'.join(pages.values()),'market_rows':[]})
        page_offsets={};offset=len(source['source_url'])+1
        for page,text in pages.items():
            page_offsets[page]=offset;offset+=len(text)+1
        registry['sources'][article['id']]={'packet':article,'raw_sha256':source['raw_sha256'],'url':source['source_url']}
        company=registry['companies'].setdefault(code,{'code':code,'aliases':[]})
        proof_byrole={}
        for item in profile['identity_evidence']:
            page_text=pages[item['pdf_page']]
            if page_text.count(item['quote'])!=1:raise ValueError('Ambiguous identity quote within declared page')
            start=page_offsets[item['pdf_page']]+page_text.index(item['quote']);end=start+len(item['quote'])
            ids=[q['id'] for q in article['evidence'] if q['end']>start and q['start']<end]
            proof_byrole[item['role']]={'article_id':article['id'],'article_sha256':article['sha256'],
                'quotes':resolve(article,ids),'evidence_ids':ids,'matched_span':[start,end],
                'pdf_page':item['pdf_page'],'raw_sha256':source['raw_sha256']}
        for role,kind in [('name','annual_security_name'),('formal_name','annual_formal_name')]:
            company['aliases'].append({'name':profile[role],'kind':kind,'issuer_code':code,
                'proof':proof_byrole[role],'issuer_proof':proof_byrole['code']})
    registry['sha256']=digest({k:v for k,v in registry.items() if k!='sha256'})


def prepare(source,root,profile_output):
    source,root=Path(source),Path(root)
    if (root/'protocol.json').exists():raise ValueError('Already frozen development root')
    material_list=json.loads((source/'materials.json').read_text());materials={m['id']:m for m in material_list}
    packet=json.loads((root/'profile_extraction_packet.json').read_text())
    import pdfplumber
    for item in packet:
        raw=(root/item['raw_file']).resolve()
        if not raw.is_relative_to(root.resolve()) or sha(raw)!=item['raw_sha256']:raise ValueError('Profile original digest mismatch')
        with pdfplumber.open(raw) as pdf:
            for selected_page in item['pages']:
                if (pdf.pages[selected_page['page']-1].extract_text() or '')!=selected_page['text']:
                    raise ValueError('Selected profile page differs from original PDF')
    new_profiles,pending=admit_profiles(packet,profile_output)
    write_json(root/'profile_admission_pending.json',pending)
    if pending:raise ValueError('Profile extraction requires source correction before preparation')
    profiles=json.loads((source/'profiles.json').read_text())+new_profiles
    bycode={p['code']:p for p in profiles}
    registry=build_registry(source,material_list);_annual_identities(registry,new_profiles,packet)
    write_json(root/'subject_registry.json',registry)
    for m in material_list:
        if m['id'] not in registry['sources'] and m['id'] not in SELECTION:continue
        dest=root/m['raw_file'];dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source/m['raw_file'],dest)
    previous=read_jsonl(source/'inputs.jsonl');roles={r['id'].split('__')[0]:r['split'] for r in previous}
    selected=[];records=[]
    for aid,codes in SELECTION.items():
        m=materials[aid]
        if any(m['quality'].get(k)!='ready' for k in ('body','date')):raise ValueError('Selected article is incomplete')
        if sha(root/m['raw_file'])!=m['source']['raw_sha256']:raise ValueError('Selected source mismatch')
        selected.append(m)
        for code in codes:
            records.append({'id':aid+'__'+code,'article_id':aid,'target_code':code,'source':m['source'],
                'state':{'company':bycode[code],'news':m['news']},'group_id':m['group_id'],'split':roles[aid],
                'event_group_review':m['event_group_review'],'body_sha256':m['body_sha256'],
                'subject_bindings':bindings(registry,code,aid),'human_verified':False,'development_only':True})
    write_json(root/'profiles.json',profiles);write_json(root/'materials.json',selected);write_jsonl(root/'inputs.jsonl',records)
    write_json(root/'contrast_declaration.json',{'selection':SELECTION,'selection_basis':'source types and business contrasts, before DeepSeek labels',
        'prior_source_exposure':'All news from seen v8 archive; new annual-report backgrounds first collected in this run',
        'independent_test':False,'human_verified':False,'synthetic_news':False})
    (root/'code_snapshot').mkdir()
    names=['development.py','development_intake.py','subjects.py','evidence.py','evidence_pipeline.py','contract.py','teacher.py','storage.py']
    for name in names:shutil.copyfile(Path(__file__).with_name(name),root/'code_snapshot'/name)
    paths=[root/n for n in ['intake_declaration.json','contrast_declaration.json','subject_registry.json','profiles.json','materials.json','inputs.jsonl','profile_extraction_packet.json','profile_extraction_original.json','profile_extraction_reviewed.json','profile_source_review.json']]
    paths+=list((root/'raw').iterdir())+list((root/'code_snapshot').iterdir())
    ledger=root.parent/'news-development-budget-20261013'
    write_json(root/'protocol.json',{'version':development.VERSION,'modes':['primary'],'prompts':development.PROMPTS,
        'shared_budget_root':str(ledger.resolve()),'frozen_files':{str(p.relative_to(root)):sha(p) for p in paths if p.is_file()},
        'teacher_policy':{'max_attempts':60,'token_soft_stop':250000,'token_hard_cap':250000,
                         'workers':2,'batch_size':2,'attempts_per_batch':2,'max_output_tokens':8000},
        'human_verified':False,'development_only':True,'independent_test':False,'training_admitted':False,'production_enabled':False})
    return {'articles':len(selected),'pairs':len(records),'companies':len(profiles),
            'registered_issuers':len(registry['companies']),'article_local_relations':len(registry['relations'])}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    parser.add_argument('--prepare-from',type=Path);parser.add_argument('--profile-output',type=Path);parser.add_argument('--config',type=Path)
    args=parser.parse_args()
    if args.prepare_from:
        if not args.profile_output:parser.error('--profile-output required for preparation')
        print(json.dumps(prepare(args.prepare_from,args.root,json.loads(args.profile_output.read_text())),ensure_ascii=False))
    else:
        if not args.config:parser.error('--config required for teacher run')
        from .evidence_pipeline import run
        protocol=json.loads((args.root/'protocol.json').read_text())
        print(json.dumps(run(args.root,json.loads(args.config.read_text()),protocol['teacher_policy'],contract=development,
                modes=tuple(protocol['modes']),shared_budget_root=Path(protocol['shared_budget_root'])),ensure_ascii=False))
