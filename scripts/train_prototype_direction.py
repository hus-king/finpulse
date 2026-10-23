"""Train the company-news prototype/cosine candidates and compare against a
no-learning zero-shot baseline and the legacy logistic/GRU baselines.

Reuses the existing direction data pipeline (build_samples, indexed_features,
temporal split) so every candidate sees the same cleaned corpus and split.
"""
import argparse
import copy
import hashlib
import json
import random
from pathlib import Path

import numpy as np
import torch
from torch import nn

from backend.attention_direction_data import indexed_features
from backend.news_direction import build_samples, temporal_split, tiny_temporal_holdout
from backend.prototype_direction import PrototypeDirectionModel, zero_shot_logits
from scripts.train_attention_direction import as_tensors, read_inputs
from scripts.train_news_direction import fit as fit_baseline, metrics, probabilities, write_json

SEED = 42
BASELINES = ('market_logistic', 'news_logistic', 'fusion_gru')
SCHEMES = {
    'cosine_prototype': {'aggregate': 'attention', 'head': 'cosine'},
    'cosine_mean': {'aggregate': 'mean', 'head': 'cosine'},
    'linear_head': {'aggregate': 'attention', 'head': 'linear'},
}


def seed_all():
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)


def model_logits(model, data):
    model.eval()
    out = []
    with torch.inference_mode():
        for start in range(0, len(data['market']), 64):
            chunk = {k: v[start:start + 64] for k, v in data.items()}
            articles = max(1, int((chunk['news_ids'] != 0).sum(-1).max()))
            facts = max(1, int((chunk['fact_ids'] != 0).sum(-1).max()))
            out.append(model(news_ids=chunk['news_ids'][:, :, :articles],
                             fact_ids=chunk['fact_ids'][:, :facts]))
    return torch.cat(out)


def fit_prototype(options, partitions, features, table, fixed_epochs=None):
    seed_all()
    model = PrototypeDirectionModel(table, **options)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.003, weight_decay=.01)
    labels = {name: torch.tensor([s['label'] for s in rows], dtype=torch.float32)
              for name, rows in partitions.items()}
    loss_fn = nn.BCEWithLogitsLoss()
    best_loss, best_state, stale, best_epoch = float('inf'), None, 0, 0
    for epoch in range(1, (fixed_epochs or 200) + 1):
        model.train()
        optimizer.zero_grad()
        loss = loss_fn(model(news_ids=features['train']['news_ids'],
                             fact_ids=features['train']['fact_ids']), labels['train'])
        if not torch.isfinite(loss):
            raise ValueError('Non-finite training loss')
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1)
        optimizer.step()
        if fixed_epochs:
            best_state, best_epoch = copy.deepcopy(model.state_dict()), epoch
            continue
        model.eval()
        with torch.inference_mode():
            val_loss = float(loss_fn(model(news_ids=features['validation']['news_ids'],
                                           fact_ids=features['validation']['fact_ids']), labels['validation']))
        if val_loss < best_loss - 1e-5:
            best_loss, best_state, stale, best_epoch = val_loss, copy.deepcopy(model.state_dict()), 0, epoch
        else:
            stale += 1
        if stale >= 25:
            break
    model.load_state_dict(best_state)
    result = {'best_epoch': best_epoch, 'parameters': sum(p.numel() for p in model.parameters() if p.requires_grad),
              'validation': metrics(partitions['validation'], torch.sigmoid(model_logits(model, features['validation'])).numpy())
              if 'validation' in partitions else None}
    return model, result


def prompt_embeddings(model_path):
    import torch
    from transformers import AutoModel, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = AutoModel.from_pretrained(model_path, local_files_only=True).eval()
    texts = ['该股票未来三个交易日股价上涨', '该股票未来三个交易日股价下跌']
    tokens = tokenizer(texts, padding=True, return_tensors='pt')
    with torch.inference_mode():
        hidden = model(**tokens, use_cache=False).last_hidden_state[:, -1, :]
        vec = torch.nn.functional.normalize(hidden.float(), p=2, dim=1)
    return vec[0], vec[1]


def zero_shot_probs(table, features, up, down):
    with torch.inference_mode():
        logits = zero_shot_logits(torch.as_tensor(table, dtype=torch.float32),
                                  features['news_ids'], features['fact_ids'], up, down)
    return torch.sigmoid(logits).numpy()


def train(corpus_path, news_path, company_path, model_path, output):
    torch.set_num_threads(4)
    corpus, vectors, metadata, bundle, fact_meta, vector_ids, table = read_inputs(corpus_path, news_path, company_path)
    output.mkdir(parents=True, exist_ok=False)
    up, down = prompt_embeddings(model_path) if model_path else (None, None)
    report = {'schema_version': 'prototype-direction-v1', 'seed': SEED,
              'candidates': [*BASELINES, *SCHEMES, 'zero_shot'],
              'width': 32, 'optimizer': 'AdamW', 'learning_rate': .003, 'weight_decay': .01,
              'max_epochs': 200, 'patience': 25, 'protocols': ['observed', 'publication_replay'],
              'corpus_sha256': metadata['corpus_sha256'], 'embedding': metadata,
              'protocol_results': {}}
    for mode in report['protocols']:
        rows, audit = build_samples(corpus, mode)
        try:
            parts, split = temporal_split(rows)
            fixed = None
        except ValueError:
            try:
                parts, split = tiny_temporal_holdout(rows)
            except ValueError as exc:
                report['protocol_results'][mode] = {'status': 'insufficient_data', 'audit': audit, 'reason': str(exc)}
                write_json(output / 'report.json', report)
                continue
            fixed = 30
        features = {name: as_tensors(indexed_features(samples, corpus, bundle, vector_ids)[0])
                    for name, samples in parts.items()}
        entry = {'status': 'smoke_test_only' if fixed else ('strict_time_holdout' if mode == 'observed' else 'exploratory_replay'),
                 'audit': audit, 'split': split, 'models': {}}
        print(json.dumps({'protocol': mode, 'split': split, 'samples': {k: len(v) for k, v in parts.items()}}), flush=True)
        # Legacy baselines (use market + pooled news features).
        trained = {}
        for kind in BASELINES:
            model, result, data = fit_baseline(kind, parts, vectors, metadata['dimension'], fixed)
            trained[kind] = model
            entry['models'][kind] = {**result, 'test': metrics(parts['test'], probabilities(model, data['test']))}
            print(json.dumps({'protocol': mode, 'fit': kind, 'epoch': result['best_epoch']}), flush=True)
        # Prototype/cosine schemes.
        for kind, options in SCHEMES.items():
            model, result = fit_prototype(options, parts, features, table, fixed)
            trained[kind] = model
            entry['models'][kind] = {**result, 'test': metrics(parts['test'], torch.sigmoid(model_logits(model, features['test'])).numpy())}
            print(json.dumps({'protocol': mode, 'fit': kind, 'epoch': result['best_epoch'], 'parameters': result['parameters']}), flush=True)
        # No-learning baseline.
        if up is not None:
            entry['models']['zero_shot'] = {'best_epoch': None, 'parameters': 0, 'validation': None,
                                            'test': metrics(parts['test'], zero_shot_probs(table, features['test'], up, down))}
        # Majority class reference.
        labels = np.asarray([s['label'] for s in parts['test']])
        majority = float(labels.mean() >= .5)
        entry['models']['majority_class'] = {'test': metrics(parts['test'], np.full(len(labels), majority))}
        report['protocol_results'][mode] = entry
    write_json(output / 'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--news-embeddings', type=Path, required=True)
    parser.add_argument('--company-dir', type=Path, required=True)
    parser.add_argument('--model', type=Path, help='Qwen3 model for the zero-shot prompt embeddings')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = train(args.corpus, args.news_embeddings, args.company_dir, args.model, args.output)
    for mode, entry in report['protocol_results'].items():
        if 'models' not in entry:
            continue
        best = max((m for m in entry['models'] if m != 'majority_class'), key=lambda k: entry['models'][k]['test']['balanced_accuracy'] or 0)
        print(json.dumps({'protocol': mode, 'best_balanced_accuracy': best,
                          'test': {k: entry['models'][k]['test'] for k in entry['models']}}, ensure_ascii=False))
