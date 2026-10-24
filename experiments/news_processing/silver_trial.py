"""One explicit research-only Laya training trial over source-supported silver."""
import argparse
import hashlib
import json
import math
import shutil
import time
from collections import Counter
from pathlib import Path

from .contract import canonical, digest
from .storage import read_jsonl, write_json, write_jsonl

QUESTIONS={
    'fact_scope':{'type':'choice','instructions':'只判断给定事实的主体范围，不把同行具体事件当作全行业事件。',
        'criteria':{'COMPANY_EVENT':'具体公司或其相关主体的审批、合同、经营、财务或证券事件。',
                    'INDUSTRY_SIGNAL':'全行业或商品市场的统计、价格、供需等公共信号。'}},
    'event_relation':{'type':'choice','instructions':'只判断指定单事件对目标公司的关系；不判断整篇是否无关。',
        'criteria':{'OWN':'有来源主体证据的目标公司当前、持续或计划中的自身事件。',
                    'DIRECT':'具体业务暴露支持的一步外部传导。',
                    'CONDITIONAL':'存在业务联系，但还需要具体产品、地区、客户或采购等暴露条件。',
                    'NONE':'当前单事件与提供的目标主营背景没有实质连接。'}}}
SETTINGS={'epochs':2,'micro_batch':1,'grad_accum':8,'encoder_lr':1e-5,'head_lr':5e-5,
          'min_lr':1e-6,'loss':'soft-ce','shuffle_options':['choice'],'seed':42,
          'max_len':3072,'head_max_len':512,'amp':True,'gradient_checkpointing':True,
          'label_smoothing':0.04,'log_every':50}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def adapt(source):
    if source.get('sha256')!=digest({k:v for k,v in source.items() if k!='sha256'}):raise ValueError('Silver record changed')
    if source.get('label_tier')!='source_supported_silver_candidate' or source.get('human_verified') is not False:
        raise ValueError('This trial accepts only explicitly nonhuman silver candidates')
    messages=source['messages']
    if [m['role'] for m in messages]!=['system','user','assistant']:raise ValueError('Unexpected message roles')
    inputs=json.loads(messages[1]['content']);target=json.loads(messages[2]['content'])
    article=inputs['article'];evidence=article['evidence'];byid={q['id']:q['text'] for q in evidence}
    if len(byid)!=len(evidence):raise ValueError('Duplicate news evidence ID')
    is_fact=source['task']=='fact_structuring'
    if not is_fact and source['task']!='event_target_relation':raise ValueError('Unsupported task')
    qid='fact_scope' if is_fact else 'event_relation';label=target['scope' if is_fact else 'relation']
    if label not in QUESTIONS[qid]['criteria']:raise ValueError('Unsupported label; never remap to NONE')
    ids=inputs['fact_evidence_ids'] if is_fact else inputs['event']['news_evidence_ids']
    if not ids or any(id_ not in byid for id_ in ids):raise ValueError('Missing event evidence')
    event=[byid[id_] for id_ in ids]
    # Prices use complete selected rows, headers and footnotes; prose relations retain background.
    table=any(' | ' in q['text'] for q in evidence)
    context=[q['text'] for q in evidence] if not is_fact and not table else [evidence[0]['text']]+[
        q['text'] for q in evidence if q['text'].startswith('注：')]
    state={'news_context':context,'event_evidence':event,'publication_date':article.get('publication_date','')}
    if not is_fact:
        c=inputs['company']['company']
        state['target_company']={k:c.get(k,[] if k in {'products','upstream','downstream','regions'} else '')
            for k in ('code','name','products','upstream','downstream','regions')}
    return {'id':source['id'],'article_id':source['lineage'].get('article_id',source['id'].split(':')[0].split('__')[0]),
            'group_id':source['lineage']['group_id'],'source_record_sha256':source['sha256'],
            'state':state,'questions':{qid:QUESTIONS[qid]},'expected':{qid:label},
            'reference_type':'AI_source_supported_silver','human_verified':False}


def prepare(source,root,*,heldout_groups,research_silver,base_checkpoint,base_weight_sha256):
    if not research_silver:raise ValueError('Explicit research-only silver opt-in required')
    source,root=Path(source),Path(root)
    if root.exists():raise ValueError('New trial directory required')
    path=source/'silver_candidates.jsonl';rows=[adapt(r) for r in read_jsonl(path)]
    groups=set(heldout_groups)
    if not groups or not groups<={r['group_id'] for r in rows}:raise ValueError('Invalid held-out event groups')
    train=[r for r in rows if r['group_id'] not in groups];dev=[r for r in rows if r['group_id'] in groups]
    if not train or not dev:raise ValueError('Empty research role')
    if {r['article_id'] for r in train}&{r['article_id'] for r in dev}:raise ValueError('One article crosses roles')
    counts={name:{q:dict(Counter(r['expected'][q] for r in rs if q in r['expected'])) for q in QUESTIONS}
            for name,rs in [('train',train),('development',dev)]}
    if any(not counts['train'][q].get(label) for q in QUESTIONS for label in QUESTIONS[q]['criteria']):
        raise ValueError('Declared training class has no source support')
    root.mkdir(parents=True);shutil.copyfile(path,root/'source_silver.jsonl')
    write_jsonl(root/'train.jsonl',train);write_jsonl(root/'development.jsonl',dev)
    code=root/'code'/'experiments'/'news_processing';code.mkdir(parents=True)
    (code.parent/'__init__.py').write_text('');(code/'__init__.py').write_text('')
    for name in ['silver_trial.py','contract.py','storage.py']:shutil.copyfile(Path(__file__).with_name(name),code/name)
    protocol={'version':'silver-research-trial-v12','source_sha256':sha(path),'questions':QUESTIONS,'settings':SETTINGS,
        'base_checkpoint':base_checkpoint,'base_weight_sha256':base_weight_sha256,
        'heldout_event_groups':sorted(groups),'counts':counts,'research_silver_opt_in':True,
        'source_admission_unchanged':True,'human_verified':False,'independent_test':False,'production_enabled':False,
        'selection':'Fixed final epoch 2; no checkpoint selection, calibration or hyperparameter search',
        'sampling':'Original rows once per epoch; no class replication',
        'limitation':'All sources previously seen; company profiles recur across roles; absent development classes not evaluated.'}
    write_json(root/'protocol.json',protocol)
    write_json(root/'training_freeze.json',{'files':{str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*')) if p.is_file()}})
    return {'train':len(train),'development':len(dev),'counts':counts,'independent_test':False}


def metrics(predictions):
    result={}
    for q,question in QUESTIONS.items():
        labels=list(question['criteria']);selected=[p for p in predictions if p['question']==q]
        confusion={a:{b:0 for b in labels} for a in labels}
        for p in selected:confusion[p['expected']][p['label']]+=1
        perclass={};f1s=[]
        for a in labels:
            support=sum(confusion[a].values());predicted=sum(confusion[b][a] for b in labels);tp=confusion[a][a]
            f1=2*tp/(support+predicted) if support else None
            perclass[a]={'support':support,'correct':tp,'recall':tp/support if support else None,'f1':f1,'predicted':predicted}
            if support:f1s.append(f1)
        result[q]={'n':len(selected),'weak_agreement':sum(confusion[a][a] for a in labels)/len(selected) if selected else None,
            'supported_macro_f1':sum(f1s)/len(f1s) if f1s else None,'class_recall':perclass,'confusion':confusion,
            'mean_cross_entropy':sum(p['cross_entropy'] for p in selected)/len(selected) if selected and all('cross_entropy' in p for p in selected) else None}
    return result


def run(root,research_silver):
    if not research_silver:raise ValueError('Explicit research-only silver opt-in required')
    root=Path(root)
    for name,h in json.loads((root/'training_freeze.json').read_text())['files'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path)!=h:raise ValueError('Frozen trial changed: '+name)
    protocol=json.loads((root/'protocol.json').read_text());settings=protocol['settings'];base=Path(protocol['base_checkpoint'])
    if protocol['questions']!=QUESTIONS or settings!=SETTINGS:raise ValueError('Execution task or settings changed')
    if sha(base/'model.safetensors')!=protocol['base_weight_sha256']:raise ValueError('Original base weights changed')
    import torch
    import laya
    import laya.train as t
    torch.set_num_threads(6);torch.set_num_interop_threads(1);torch.manual_seed(settings['seed'])
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():raise ValueError('BF16 CUDA required')
    config=t.TrainConfig(**settings);config.shuffle_options=tuple(config.shuffle_options);config.validate()
    model,tok,cfg=t.load_checkpoint(str(base));device=torch.device('cuda')
    roles={role:read_jsonl(root/(role+'.jsonl')) for role in ('train','development')};items={};lengths=[]
    for role,rows in roles.items():
        items[role],skipped=t.items_from_rows(tok,rows,settings['max_len'],settings['head_max_len'],label_smoothing=config.label_smoothing if role=='train' else 0)
        full,full_skipped=t.items_from_rows(tok,rows,100000,100000)
        if skipped or full_skipped or len(items[role])!=len(rows) or len(full)!=len(rows):raise ValueError('Training item skipped')
        for r,item,whole in zip(rows,items[role],full):
            bounded=t.encode_item(tok,item,settings['max_len'],settings['head_max_len'])
            unbounded=t.encode_item(tok,whole,100000,100000)
            if bounded['ids']!=unbounded['ids'] or bounded['markers']!=unbounded['markers']:raise ValueError('Exact assembled input would truncate: '+r['id'])
            if item['k']!=len(next(iter(r['questions'].values()))['criteria']):raise ValueError('Wrong task head size')
            lengths.append({'id':r['id'],'role':role,'tokens':len(unbounded['ids']),'options':item['k']})
    write_json(root/'token_preflight.json',{'rows':lengths,'max_tokens':max(x['tokens'] for x in lengths),'truncated':0})
    with (root/'run_started.json').open('x') as f:json.dump({'freeze_sha256':sha(root/'training_freeze.json'),'started_at_unix':time.time()},f)
    print(json.dumps({'stage':'preflight','rows':len(lengths),'max_tokens':max(x['tokens'] for x in lengths)}),flush=True)
    original=t._forward
    def bf16_forward(m,batch,device,amp,detach_encoder):
        with torch.autocast('cuda',dtype=torch.bfloat16,enabled=amp):return original(m,batch,device,False,detach_encoder)
    t._forward=bf16_forward
    model.to(device);torch.cuda.reset_peak_memory_stats()
    def predict(role):
        records=t.calibration_records(model,tok,items[role],device,settings['max_len'],settings['head_max_len'],batch_size=1)
        output=[]
        for r,item,(_,logits,_,k) in zip(roles[role],items[role],records):
            labels=list(item['q']['crit']);values=[float(x) for x in logits]
            if k!=len(labels) or not all(math.isfinite(x) for x in values):raise ValueError('Invalid prediction logits')
            top=max(values);probs=[math.exp(x-top) for x in values];total=sum(probs);probs=[p/total for p in probs]
            q=next(iter(r['questions']));expected=r['expected'][q]
            output.append({'id':r['id'],'group_id':r['group_id'],'question':q,'expected':expected,
                'label':labels[max(range(k),key=lambda i:values[i])], 'probabilities':dict(zip(labels,probs)),
                'cross_entropy':top+math.log(total)-values[labels.index(expected)]})
        if len(output)!=len(roles[role]):raise ValueError('Missing prediction')
        return output
    baseline={role:predict(role) for role in roles}
    for role,rows in baseline.items():write_jsonl(root/('base_'+role+'_predictions.jsonl'),rows)
    progress=[];started=time.perf_counter()
    def epoch_done(epoch,loss):
        entry={'epoch':epoch+1,'mean_train_loss':loss,'elapsed_seconds':time.perf_counter()-started}
        if not math.isfinite(loss):raise ValueError('Nonfinite training loss')
        progress.append(entry);write_json(root/'progress.json',progress);print(json.dumps(entry),flush=True)
    losses=t.train_model(model,tok,items['train'],config,device,settings['max_len'],settings['head_max_len'],on_epoch_end=epoch_done)
    seconds=time.perf_counter()-started
    checkpoint=root/'checkpoint';cfg.pop('temperature_by_options',None);cfg['temperature']=[1,1,1]
    cfg.update(max_len=settings['max_len'],head_max_len=settings['head_max_len'],fine_tuned=True,
        training={'research_silver':True,'epochs':settings['epochs'],'production_enabled':False,'independent_test':False,'human_verified':False})
    t.save_checkpoint(model,tok,cfg,str(checkpoint));write_json(checkpoint/'questions.json',QUESTIONS)
    candidate={role:predict(role) for role in roles}
    for role,rows in candidate.items():write_jsonl(root/('candidate_'+role+'_predictions.jsonl'),rows)
    result={'completed':True,'epochs':settings['epochs'],'epoch_losses':losses,'optimization_seconds':seconds,
        'peak_reserved_gib':torch.cuda.max_memory_reserved()/1024**3,'gpu':torch.cuda.get_device_name(0),
        'laya_version':laya.__version__,'torch_version':torch.__version__,'checkpoint_sha256':sha(checkpoint/'model.safetensors'),
        'base':{role:metrics(rows) for role,rows in baseline.items()},'candidate':{role:metrics(rows) for role,rows in candidate.items()},
        'human_verified':False,'independent_test':False,'production_enabled':False,'automatic_deletion':False}
    write_json(root/'results.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--research-silver',action='store_true')
    p.add_argument('--prepare-from',type=Path);p.add_argument('--holdout-group',action='append');p.add_argument('--base-checkpoint');p.add_argument('--base-weight-sha256')
    args=p.parse_args()
    if args.prepare_from:
        print(json.dumps(prepare(args.prepare_from,args.root,heldout_groups=args.holdout_group or [],research_silver=args.research_silver,
            base_checkpoint=args.base_checkpoint,base_weight_sha256=args.base_weight_sha256),ensure_ascii=False))
    else:run(args.root,args.research_silver)
