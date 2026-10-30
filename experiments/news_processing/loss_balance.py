"""Full-coverage semantic row weights for the pinned micro-batch-one Laya trial."""
import math
from collections import Counter


def row_weights(rows):
    counts=Counter((next(iter(r['expected'])),next(iter(r['expected'].values()))) for r in rows)
    tasks={q for q,_ in counts};classes=Counter(q for q,_ in counts)
    return [len(rows)/(len(tasks)*classes[q]*counts[q,label])
            for r in rows for q,label in r['expected'].items()]


def train_weighted(backend,model,tok,items,config,device,max_len,head_max_len,row_ids,weights,*,trace=None,on_epoch_end=None):
    """Retain native optimizer, option shuffling, accumulation and scheduler.

    Laya 0.4.1 has no row-weight argument. Its soft CE is a scalar for micro_batch=1;
    bind that scalar to the source item before option permutation and restore hooks.
    No change to target distributions or to installed package files is necessary.
    """
    if config.micro_batch!=1 or config.loss!='soft-ce':raise ValueError('Weighted adapter requires micro_batch=1 and soft-ce')
    if len(items)!=len(weights) or len(items)!=len(row_ids) or len(set(row_ids))!=len(items):raise ValueError('Weight/item mismatch')
    if any(not math.isfinite(x) or x<=0 for x in weights):raise ValueError('Invalid row loss weight')
    lookup={id(item):(key,weight) for item,key,weight in zip(items,row_ids,weights)}
    if len(lookup)!=len(items):raise ValueError('Repeated item object')
    original_encode,original_loss=backend.encode_item,backend.soft_ce_loss
    pending=[];seen=[] if trace is None else trace
    def encode(tok,item,*args,**kwargs):
        if pending:raise ValueError('Weighted adapter observed more than one item per loss')
        if id(item) not in lookup:raise ValueError('Laya changed source item identity before weighting')
        pending.append(lookup[id(item)])
        return original_encode(tok,item,*args,**kwargs)
    def loss(logits,target,mask):
        if len(pending)!=1:raise ValueError('Missing semantic row weight')
        key,weight=pending.pop();value=original_loss(logits,target,mask)*weight
        seen.append({'id':key,'weight':weight});return value
    backend.encode_item,backend.soft_ce_loss=encode,loss
    try:
        result=backend.train_model(model,tok,items,config,device,max_len,head_max_len,on_epoch_end=on_epoch_end)
        if pending or Counter(x['id'] for x in seen)!=Counter({key:config.epochs for key in row_ids}):
            raise ValueError('Training did not cover every original row in every epoch')
        for epoch in range(config.epochs):
            if Counter(x['id'] for x in seen[epoch*len(items):(epoch+1)*len(items)])!=Counter(row_ids):
                raise ValueError('Epoch omitted or duplicated a row')
        return result
    finally:
        backend.encode_item,backend.soft_ce_loss=original_encode,original_loss
