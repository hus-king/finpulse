"""Train a preregistered small company/news/time attention GRU experiment.

Uses frozen vectors, observed company availability in BOTH news protocols, and
the same purged splits as the original baseline. All outputs are research-only.
"""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import platform
import random
import time

import numpy as np
import torch
from torch import nn

from backend.attention_direction import AttentionDirectionModel
from backend.attention_direction_data import available_cards, indexed_features
from backend.news_direction import build_samples, temporal_split, tiny_temporal_holdout
from scripts.train_news_direction import (DirectionModel, fit as fit_baseline, load_vectors, metrics,
                                          probabilities, tensors, write_json)

SEED = 42
BASELINES = ('market_logistic', 'news_logistic', 'fusion_gru')
ATTENTION_MODELS = ('hierarchical_gru', 'company_attention_gru')
RECENCY_MODELS = {'session_attention': {'news_time_width': 8},
                  'recency_1d': {'news_time_width': 8, 'recency_half_life': 1.},
                  'recency_3d': {'news_time_width': 8, 'recency_half_life': 3.}}
FEATURE_NAMES = ('news_ids', 'news_time', 'fact_ids', 'day_market', 'day_time', 'market')


def seed_all():
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)


def as_tensors(features):
    return {name: torch.as_tensor(features[name], dtype=torch.long if name in ('news_ids', 'fact_ids') else torch.float32)
            for name in FEATURE_NAMES}


def batch(data, indices):
    selected = {k: v[indices] for k, v in data.items()}
    # Trim padding within a minibatch, retaining at least one slot for null cases.
    articles = max(1, int((selected['news_ids'] != 0).sum(-1).max()))
    facts = max(1, int((selected['fact_ids'] != 0).sum(-1).max()))
    selected['news_ids'] = selected['news_ids'][:, :, :articles]
    selected['news_time'] = selected['news_time'][:, :, :articles]
    selected['fact_ids'] = selected['fact_ids'][:, :facts]
    return selected


def attention_probabilities(model, data):
    model.eval()
    result = []
    with torch.inference_mode():
        for start in range(0, len(data['market']), 64):
            result.append(torch.sigmoid(model(**batch(data, slice(start, start + 64)))))
    return torch.cat(result).numpy()


def fit_attention(kind, partitions, data, vectors, fixed_epochs=None, *, model_options=None):
    seed_all()
    train = data['train']
    market = train['market']
    day = train['day_market'].reshape(-1, 6)
    options = {'use_company': kind == 'company_attention_gru', **(model_options or {})}
    model = AttentionDirectionModel(vectors, market.mean(0), market.std(0, unbiased=False).clamp_min(1e-6),
                                    day.mean(0), day.std(0, unbiased=False).clamp_min(1e-6),
                                    **options)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.003, weight_decay=.01)
    labels = torch.tensor([s['label'] for s in partitions['train']], dtype=torch.float32)
    best_loss, best_state, best_epoch, stale = float('inf'), None, 0, 0
    loss_fn = nn.BCEWithLogitsLoss()
    started = time.monotonic()
    for epoch in range(1, (fixed_epochs or 200) + 1):
        model.train()
        order = torch.randperm(len(labels))
        for start in range(0, len(order), 64):
            indices = order[start:start + 64]
            optimizer.zero_grad()
            loss = loss_fn(model(**batch(train, indices)), labels[indices])
            if not torch.isfinite(loss):
                raise ValueError('Nonfinite attention-model training loss')
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1)
            optimizer.step()
        if fixed_epochs:
            best_state, best_epoch = copy.deepcopy(model.state_dict()), epoch
        else:
            model.eval()
            validation_labels = torch.tensor([s['label'] for s in partitions['validation']], dtype=torch.float32)
            losses = []
            with torch.inference_mode():
                for start in range(0, len(validation_labels), 64):
                    indices = slice(start, start + 64)
                    losses.append(nn.functional.binary_cross_entropy_with_logits(
                        model(**batch(data['validation'], indices)), validation_labels[indices], reduction='sum'))
            validation_loss = float(torch.stack(losses).sum() / len(validation_labels))
            if validation_loss < best_loss - 1e-5:
                best_loss, best_state, best_epoch, stale = validation_loss, copy.deepcopy(model.state_dict()), epoch, 0
            else:
                stale += 1
            if stale >= 25:
                break
    model.load_state_dict(best_state)
    return model, {'best_epoch': best_epoch, 'parameters': sum(p.numel() for p in model.parameters() if p.requires_grad),
                   'seconds': time.monotonic() - started,
                   'validation': metrics(partitions['validation'], attention_probabilities(model, data['validation']))
                   if 'validation' in partitions else None}


def read_inputs(corpus_path, news_path, company_path):
    corpus = json.loads(corpus_path.read_text())
    vectors, metadata = load_vectors(news_path)
    if metadata['corpus_sha256'] != hashlib.sha256(corpus_path.read_bytes()).hexdigest():
        raise ValueError('News vector/corpus hash mismatch')
    facts, fact_meta = load_vectors(company_path / 'company-embeddings.npz')
    bundle_path = company_path / 'company-cards.json'
    bundle = json.loads(bundle_path.read_text())
    if fact_meta['cards_sha256'] != hashlib.sha256(bundle_path.read_bytes()).hexdigest():
        raise ValueError('Company cards/vector hash mismatch')
    if set(facts) != {c['id'] for c in bundle['cards']}:
        raise ValueError('Company vector/card IDs mismatch')
    for key in ('weights_sha256', 'dimension', 'max_length', 'pooling'):
        if fact_meta[key] != metadata[key]:
            raise ValueError('News/company encoder settings differ: ' + key)
    if set(vectors) & set(facts):
        raise ValueError('News/company ID namespaces overlap')
    all_vectors = {**vectors, **facts}
    ids = sorted(all_vectors)
    table = np.concatenate((np.zeros((1, metadata['dimension']), dtype=np.float32),
                            np.stack([all_vectors[key] for key in ids])))
    return corpus, vectors, metadata, bundle, fact_meta, ids, table


def load_saved_model(artifacts, manifest, kind):
    if kind not in manifest['checkpoints_sha256']:
        raise ValueError('Model is not in the saved manifest')
    checkpoint = artifacts / (kind + '.pt')
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != manifest['checkpoints_sha256'][kind]:
        raise ValueError('Checkpoint hash mismatch')
    state = torch.load(checkpoint, map_location='cpu', weights_only=True)
    if kind in BASELINES:
        model = DirectionModel(kind, manifest['embedding']['dimension'], state['market_mean'], state['market_scale'])
    else:
        options = {'use_company': kind != 'hierarchical_gru', **manifest.get('model_options', {}).get(kind, {})}
        model = AttentionDirectionModel(state['vectors'], state['market_mean'], state['market_scale'],
                                        state['day_mean'], state['day_scale'], width=manifest['width'], **options)
    model.load_state_dict(state)
    return model.eval()


def train(corpus_path, news_path, company_path, output, *, recency=False):
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    corpus, vectors, embedding, bundle, fact_meta, vector_ids, table = read_inputs(corpus_path, news_path, company_path)
    if bool(corpus.get('direction_policy')) != recency:
        raise ValueError('Use --recency with the prepared session corpus; legacy training needs a legacy corpus')
    attention_options = RECENCY_MODELS if recency else {kind: {} for kind in ATTENTION_MODELS}
    output.mkdir(parents=True, exist_ok=False)
    plan = {'schema_version': 'recency-attention-direction-v2' if recency else 'company-attention-direction-v1', 'seed': SEED,
            'candidates': [*BASELINES, *attention_options], 'width': 32, 'batch_size_attention': 64,
            'optimizer': 'AdamW', 'learning_rate_attention': .003, 'weight_decay': .01,
            'max_epochs': 200, 'patience': 25, 'dropout': .2, 'tiny_fixed_epochs': 30,
            'early_stopping_metric': 'validation BCE on logits',
            'tiny_preset_model': 'recency_3d' if recency else 'company_attention_gru',
            'selection': 'validation balanced_accuracy then log_loss; frozen before test',
            'company_availability': 'known_at <= cutoff in all protocols; no static snapshot replay',
            'protocols': ['observed', 'publication_replay'], 'training_device': 'cpu',
            'corpus_sha256': embedding['corpus_sha256'], 'news_embedding': embedding, 'company_embedding': fact_meta,
            'architectural_changes_vs_old_gru': ['article-level news pooling', 'company cross attention',
                                               'daily market sequence', 'article age and missing-time flags', 'temporal attention'],
            'ablation': 'hierarchical_gru disables company cards and company-present flag; other features unchanged'}
    if recency:
        plan.update(model_options=attention_options, data_policy=corpus['direction_policy'],
                    recency_prior='article amplitude exp(-ln(2)*publication trading age/half-life) outside softmax; state-readout log prior',
                    company_safety_rule='Disable company input when training has zero news-and-company samples; do not learn from test-period facts',
                    ablation='session_attention has identical features and parameters, with both recency priors disabled')
    root = Path(__file__).resolve().parents[1]
    plan['source_sha256'] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
        'backend/news_direction.py', 'backend/attention_direction_data.py', 'backend/attention_direction.py',
        'scripts/train_news_direction.py', 'scripts/train_attention_direction.py', 'scripts/embed_company_facts.py',
        'scripts/embed_direction_news.py', 'backend/direction_news_filter.py', 'scripts/prepare_direction_corpus.py')}
    write_json(output / 'plan.json', plan)
    report = {**plan, 'runtime': {'python': platform.python_version(), 'torch': str(torch.__version__), 'numpy': np.__version__},
              'limitations': ['Observed data has very few independent dates; tiny results are pipeline smoke tests.',
                              'Publication replay uses later-collected news and is exploratory only.',
                              'Company business descriptions lack original release timestamps and financial exposures.',
                              'All company versions obey observed availability; some training periods have no company facts.',
                              'Independence of strict test dates must be checked against earlier experiment date lists.',
                              'Unadjusted direction, incomplete adjustment references, no transaction-cost backtest.',
                              'Scores and attention weights are uncalibrated and are not causal explanations.'], 'protocol_results': {}}
    for mode in plan['protocols']:
        rows, audit = build_samples(corpus, mode)
        audit.pop('calendar')
        fixed = None
        try:
            parts, split = temporal_split(rows)
        except ValueError:
            try:
                parts, split = tiny_temporal_holdout(rows)
            except ValueError as exc:
                report['protocol_results'][mode] = {'status': 'insufficient_data', 'audit': audit, 'reason': str(exc)}
                write_json(output / 'report.json', report)
                continue
            fixed = 30
        features, coverage = {}, {}
        for name, samples in parts.items():
            values, coverage[name] = indexed_features(samples, corpus, bundle, vector_ids)
            features[name] = as_tensors(values)
        entry = {'status': 'smoke_test_only' if fixed else ('strict_time_holdout' if mode == 'observed' else 'exploratory_replay'), 'audit': audit,
                 'split': split, 'coverage': coverage, 'models': {},
                 'company_learning_warning': 'No news+company training samples' if not coverage['train'].get('with_news_and_company') else None}
        report['protocol_results'][mode] = entry
        fitted_options = copy.deepcopy(attention_options)
        if recency and not coverage['train'].get('with_news_and_company'):
            for options in fitted_options.values():
                options['use_company'] = False
            entry['company_input_disabled'] = True
        trained, datasets = {}, {}
        for kind in BASELINES:
            model, result, data = fit_baseline(kind, parts, vectors, embedding['dimension'], fixed)
            trained[kind], datasets[kind], entry['models'][kind] = model, data, result
            print(json.dumps({'protocol': mode, 'fit': kind, 'epoch': result['best_epoch']}), flush=True)
        for kind, options in fitted_options.items():
            model, result = fit_attention('hierarchical_gru' if kind == 'hierarchical_gru' else 'company_attention_gru',
                                          parts, features, table, fixed, model_options=options)
            trained[kind], entry['models'][kind] = model, result
            print(json.dumps({'protocol': mode, 'fit': kind, 'epoch': result['best_epoch'], 'parameters': result['parameters']}), flush=True)
        # Model selection is complete before calculating any test score.
        selected = plan['tiny_preset_model'] if fixed else max(plan['candidates'], key=lambda k: (
            entry['models'][k]['validation']['balanced_accuracy']
            if entry['models'][k]['validation']['balanced_accuracy'] is not None else -1,
            -entry['models'][k]['validation']['log_loss']))
        entry['selected_model'] = selected
        prior = np.mean([s['label'] for s in parts['train']])
        predictions = {'majority_prior': np.full(len(parts['test']), prior)}
        entry['majority_prior'] = metrics(parts['test'], predictions['majority_prior'])
        artifact = output / mode
        artifact.mkdir()
        hashes = {}
        for kind, model in trained.items():
            p = attention_probabilities(model, features['test']) if kind in attention_options else probabilities(model, datasets[kind]['test'])
            predictions[kind] = p
            entry['models'][kind]['test'] = metrics(parts['test'], p)
            for subset_name, include in (
                ('test_with_news', lambda s: s['news_count'] > 0),
                ('test_with_company', lambda s: bool(available_cards(bundle, s['code'], s['cutoff']))),
            ):
                indices = [i for i, s in enumerate(parts['test']) if include(s)]
                entry['models'][kind][subset_name] = metrics([parts['test'][i] for i in indices], p[indices]) if indices else None
            entry['models'][kind]['test_by_date'] = {
                day: metrics([s for s in parts['test'] if s['date'] == day], [v for s, v in zip(parts['test'], p) if s['date'] == day])
                for day in sorted({s['date'] for s in parts['test']})}
            checkpoint = artifact / (kind + '.pt')
            torch.save(model.state_dict(), checkpoint)
            hashes[kind] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        manifest = {'schema_version': plan['schema_version'], 'mode': mode, 'status': entry['status'],
                    'selected_model': selected, 'width': 32, 'vector_ids': vector_ids, 'checkpoints_sha256': hashes,
                    'corpus_sha256': plan['corpus_sha256'], 'company_cards_sha256': fact_meta['cards_sha256'],
                    'embedding': embedding, 'eligible_for_trading': False, 'calibration': 'uncalibrated',
                    'company_availability': plan['company_availability']}
        if recency:
            manifest.update(model_options=fitted_options, data_policy=plan['data_policy'])
        write_json(artifact / 'manifest.json', manifest)
        entry['reload_verification'] = {}
        for kind in trained:
            restored = load_saved_model(artifact, manifest, kind)
            p = (attention_probabilities(restored, features['test']) if kind in attention_options
                 else probabilities(restored, datasets[kind]['test']))
            error = float(np.max(np.abs(p - predictions[kind])))
            if error > 1e-7:
                raise ValueError('Saved model predictions changed: ' + kind)
            entry['reload_verification'][kind] = {'max_absolute_difference': error, 'samples': len(p)}
        if recency and selected in attention_options:
            without_news = {k: v.clone() for k, v in features['test'].items()}
            without_news['news_ids'].zero_()
            without_news['news_time'].zero_()
            without_news['day_time'][..., 2:].zero_()
            p = attention_probabilities(trained[selected], without_news)
            entry['selected_news_removal_diagnostic'] = {
                'mean_absolute_score_change': float(np.mean(np.abs(p - predictions[selected]))),
                'metrics_without_news': metrics(parts['test'], p),
                'note': 'Post-selection diagnostic, not an independently trained baseline or causal effect'}
        with (artifact / 'test_predictions.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['code', 'date', 'label_end', 'label', 'return_3d', 'news_count', *predictions])
            writer.writeheader()
            for i, sample in enumerate(parts['test']):
                writer.writerow({**{k: sample[k] for k in ('code', 'date', 'label_end', 'label', 'return_3d', 'news_count')},
                                 **{kind: float(values[i]) for kind, values in predictions.items()}})
        write_json(output / 'report.json', report)
        print(json.dumps({'protocol': mode, 'status': entry['status'], 'selected_before_test': selected,
                          'test': {k: v['test'] for k, v in entry['models'].items()}}), flush=True)
    return report


def predict(corpus_path, news_path, company_path, artifacts, output, kind='company_attention_gru'):
    manifest = json.loads((artifacts / 'manifest.json').read_text())
    corpus, _, embedding, bundle, fact_meta, vector_ids, _ = read_inputs(corpus_path, news_path, company_path)
    kind = kind or manifest['selected_model']
    if manifest['schema_version'] not in ('company-attention-direction-v1', 'recency-attention-direction-v2') or vector_ids != manifest['vector_ids']:
        raise ValueError('Incompatible attention artifact or vector ID order')
    if fact_meta['cards_sha256'] != manifest['company_cards_sha256']:
        raise ValueError('Company cards differ from this trained artifact')
    for key in ('weights_sha256', 'dimension', 'max_length', 'pooling', 'input_format', 'long_text_strategy'):
        if embedding.get(key) != manifest['embedding'].get(key):
            raise ValueError('News encoder/settings differ from the trained artifact: ' + key)
    if manifest.get('data_policy') != corpus.get('direction_policy'):
        raise ValueError('Prediction data policy differs from training')
    model = load_saved_model(artifacts, manifest, kind)
    samples, _ = build_samples(corpus, 'observed', prediction=True)
    if not samples:
        result = {'status': 'insufficient_data', 'eligible_for_trading': False, 'model': kind,
                  'reason': 'No latest samples satisfy time, market and adjustment checks', 'items': []}
        write_json(output, result)
        return result
    values, coverage = indexed_features(samples, corpus, bundle, vector_ids)
    data = as_tensors(values)
    if kind in BASELINES:
        vectors, _ = load_vectors(news_path)
        p_all = probabilities(model, tensors(samples, vectors, embedding['dimension']))
        attention = None
    else:
        with torch.inference_mode():
            logits, attention = model(**data, explain=True)
        p_all = torch.sigmoid(logits).numpy()
    items = []
    for i, sample in enumerate(samples):
        weights = attention['news'][i].numpy() if attention is not None else None
        top = sorted([{'id': key, 'day_offset': day - 9, 'weight_within_day': float(weights[day, j]),
                       'recency_multiplier': float(attention['news_decay'][i, day, j]),
                       'effective_weight': float(attention['news_effective_weight'][i, day, j])}
                      for day, ids in enumerate(sample['news']) for j, key in enumerate(ids)] if weights is not None else [],
                     key=lambda x: x['effective_weight'], reverse=True)[:5]
        p = float(p_all[i])
        items.append({'code': sample['code'], 'as_of': sample['cutoff'], 'available_news_count': sample['news_count'],
                      'company_available': bool((data['fact_ids'][i] != 0).any()),
                      'company_used': bool(getattr(model, 'use_company', False) and (data['fact_ids'][i] != 0).any()),
                      'up_probability_uncalibrated': p, 'direction': 'up' if p >= .5 else 'down',
                      'temporal_attention_oldest_to_newest': attention['time'][i].tolist() if attention is not None else None, 'top_news_within_day': top})
    result = {'status': 'research_only', 'eligible_for_trading': False, 'model': kind,
              'training_protocol': manifest['mode'], 'evaluation_status': manifest['status'],
              'horizon_trading_days': 3, 'coverage': coverage, 'attention_note': 'Selection weights, not causal evidence or calibrated confidence', 'items': items}
    write_json(output, result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('train', 'predict'))
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--news-embeddings', type=Path, required=True)
    parser.add_argument('--company-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path)
    parser.add_argument('--recency', action='store_true', help='Train preregistered session/recency candidates on a prepared v2 corpus')
    parser.add_argument('--model', choices=(*BASELINES, *ATTENTION_MODELS, *RECENCY_MODELS))
    args = parser.parse_args()
    if args.command == 'train':
        train(args.corpus, args.news_embeddings, args.company_dir, args.output, recency=args.recency)
    else:
        if args.artifacts is None:
            parser.error('--artifacts is required for predict')
        result = predict(args.corpus, args.news_embeddings, args.company_dir, args.artifacts, args.output, kind=args.model)
        print(json.dumps({'prediction_file': str(args.output), 'items': len(result['items'])}))
