"""Fixed-checkpoint inference for a separately frozen development comparison.

References must already be frozen. They are never passed to the model. This
command performs no optimization or confidence calibration, and saves raw logits.
"""
import argparse
import hashlib
import json
from pathlib import Path

from .contract import input_digest, model_state
from .infer import format_predictions, inference_rows
from .pipeline import quality_reasons
from .storage import read_jsonl, write_json, write_jsonl


def checked_freeze(root):
    root = Path(root)
    freeze = json.loads((root / 'comparison_freeze.json').read_text())
    if freeze.get('independent_test') is not False or freeze.get('production_enabled') is not False:
        raise ValueError('Development-only comparison required')
    for name, expected in freeze['sha256'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()): raise ValueError('Foreign freeze path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected: raise ValueError('Frozen file changed: '+name)
    if 'inputs.jsonl' not in freeze['sha256'] or 'source_references.jsonl' not in freeze['sha256']:
        raise ValueError('Inputs and separate source references must be frozen')
    return freeze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--candidate', required=True)
    args = parser.parse_args(); root = args.root
    freeze = checked_freeze(root); spec = freeze['checkpoints'][args.candidate]
    checkpoint = Path(spec['path'])
    if hashlib.sha256((checkpoint/'model.safetensors').read_bytes()).hexdigest() != spec['weight_sha256']:
        raise ValueError('Fixed checkpoint changed')
    for name, expected in spec['files_sha256'].items():
        path=(checkpoint/name).resolve()
        if not path.is_relative_to(checkpoint.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('Fixed checkpoint artifact changed: '+name)
    records = [r for r in read_jsonl(root/'inputs.jsonl') if not quality_reasons(r)]
    out = root/'benchmarks'/args.candidate
    out.mkdir(parents=True, exist_ok=False)
    write_json(out/'reservation.json', {'freeze_sha256':hashlib.sha256((root/'comparison_freeze.json').read_bytes()).hexdigest(),
               'actor_id':args.candidate, 'checkpoint':spec, 'optimization_performed':False})
    import torch
    import laya.train as t
    from laya.common import build_head, encode_text, serialize_state
    torch.set_num_threads(6); torch.set_num_interop_threads(1)
    model, tok, _ = t.load_checkpoint(str(checkpoint)); model.to(torch.device('cuda')); model.eval()
    max_len = freeze['max_len']; head_len = freeze['head_max_len']
    supplied = inference_rows(records)
    items, skipped = t.items_from_rows(tok, supplied, max_len, head_len)
    if skipped or len(items) != 2*len(records): raise ValueError('Question skipped')
    metadata = [(r,qid) for r,row in zip(records,supplied) for qid in row['questions']]
    audit = []
    for item,(record,qid) in zip(items,metadata):
        head = build_head(tok,item['q'],head_len)[0]
        if len(head) != len(build_head(tok,item['q'],8192)[0]): raise ValueError('Head would truncate')
        if any(len(encode_text(tok,text,add_special_tokens=False)['input_ids']) > 48 for text in item['q']['crit'].values()):
            raise ValueError('Option would truncate')
        n = len(encode_text(tok,serialize_state(model_state(record)),add_special_tokens=False)['input_ids'])
        audit.append({'id':record['id'],'question':qid,'sequence_tokens':n+len(head)+1})
    write_json(out/'token_audit.json', {'max_sequence_tokens':max(x['sequence_tokens'] for x in audit), 'items':audit})
    if any(x['sequence_tokens']>max_len for x in audit): raise ValueError('State would truncate')
    logits = t.calibration_records(model,tok,items,torch.device('cuda'),max_len,head_len,batch_size=1)
    mapping = [(r,qid,list(item['q']['crit'])) for (r,qid),item in zip(metadata,items)]
    predictions = format_predictions(records,mapping,[list(map(float,x[1])) for x in logits],spec['weight_sha256'])
    write_jsonl(out/'raw_predictions.jsonl', predictions)
    write_jsonl(out/'predictions.jsonl', [{'id':p['id'],'input_sha256':input_digest(r),'actor_id':args.candidate,
                'labels':{qid:v['label'] for qid,v in p['prediction'].items()}} for r,p in zip(records,predictions)])
    write_json(out/'summary.json', {'pairs':len(predictions),'optimization_performed':False,
               'raw_temperature':1,'calibration_fitted':False,'production_enabled':False,'deleted':0})
    print(json.dumps({'pairs':len(predictions),'candidate':args.candidate,'optimization_performed':False}))


if __name__ == '__main__': main()
