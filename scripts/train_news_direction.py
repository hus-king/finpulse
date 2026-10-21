"""Train/test or predict with frozen news vectors and small CPU classifiers.

Examples:
  python -m scripts.train_news_direction train --corpus corpus.json --embeddings vectors.npz --output run
  python -m scripts.train_news_direction predict --corpus corpus.json --embeddings vectors.npz --artifacts run/publication_replay
"""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import platform
import random

import numpy as np
import torch
from torch import nn

from backend.news_direction import (MARKET_NAMES, NEWS_DAYS, SCHEMA_VERSION,
                                    build_samples, temporal_split, tiny_temporal_holdout)

SEED = 42
CANDIDATES = ('market_logistic', 'news_logistic', 'fusion_mlp', 'fusion_gru')


class DirectionModel(nn.Module):
    def __init__(self, kind, dimension, market_mean, market_scale):
        super().__init__()
        if kind not in CANDIDATES:
            raise ValueError('Unknown model kind')
        self.kind = kind
        self.register_buffer('market_mean', torch.as_tensor(market_mean, dtype=torch.float32))
        self.register_buffer('market_scale', torch.as_tensor(market_scale, dtype=torch.float32))
        market_width = len(MARKET_NAMES)
        if kind == 'market_logistic':
            self.head = nn.Linear(market_width, 1)
        elif kind == 'news_logistic':
            self.head = nn.Linear(dimension + 2, 1)
        elif kind == 'fusion_mlp':
            self.head = nn.Sequential(nn.Linear(dimension + 2 + market_width, 32), nn.ReLU(),
                                      nn.Dropout(.2), nn.Linear(32, 1))
        else:
            self.projection = nn.Sequential(nn.Linear(dimension + 2, 32), nn.Tanh())
            self.gru = nn.GRU(32, 32, batch_first=True)
            self.head = nn.Sequential(nn.Linear(32 + market_width, 16), nn.ReLU(),
                                      nn.Dropout(.2), nn.Linear(16, 1))

    def forward(self, market, sequence, pooled):
        market = (market - self.market_mean) / self.market_scale
        if self.kind == 'market_logistic':
            joined = market
        elif self.kind == 'news_logistic':
            joined = pooled
        elif self.kind == 'fusion_mlp':
            joined = torch.cat((market, pooled), dim=1)
        else:
            _, hidden = self.gru(self.projection(sequence))
            joined = torch.cat((market, hidden[-1]), dim=1)
        return self.head(joined).squeeze(-1)


def load_vectors(path):
    with np.load(path, allow_pickle=False) as saved:
        metadata = json.loads(str(saved['metadata']))
        ids, matrix = saved['ids'].tolist(), saved['vectors'].copy()
    if (len(ids) != len(set(ids)) or matrix.ndim != 2 or len(ids) != len(matrix)
            or matrix.shape[1] != metadata['dimension'] or not np.isfinite(matrix).all()):
        raise ValueError('Invalid embedding cache')
    return dict(zip(ids, matrix)), metadata


def tensors(samples, vectors, dimension):
    sequence = np.zeros((len(samples), NEWS_DAYS, dimension + 2), dtype=np.float32)
    pooled = np.zeros((len(samples), dimension + 2), dtype=np.float32)
    for i, sample in enumerate(samples):
        all_ids = []
        for day, ids in enumerate(sample['news']):
            if ids:
                sequence[i, day, :dimension] = np.mean([vectors[key] for key in ids], axis=0)
                sequence[i, day, dimension:] = [np.log1p(len(ids)), 1]
                all_ids.extend(ids)
        if all_ids:
            pooled[i, :dimension] = np.mean([vectors[key] for key in all_ids], axis=0)
            pooled[i, dimension:] = [np.log1p(len(all_ids)), 1]
    arrays = [np.asarray([s['market'] for s in samples], dtype=np.float32), sequence, pooled]
    if not all(np.isfinite(a).all() for a in arrays):
        raise ValueError('Non-finite features')
    return tuple(torch.from_numpy(a) for a in arrays)


def metrics(samples, probabilities):
    from sklearn.metrics import (accuracy_score, balanced_accuracy_score, brier_score_loss,
                                 log_loss, matthews_corrcoef, roc_auc_score)
    y = np.asarray([s['label'] for s in samples])
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-7, 1 - 1e-7)
    pred = (p >= .5).astype(int)
    both_classes = len(set(y)) == 2
    return {'samples': len(y), 'dates': len({s['date'] for s in samples}),
            'accuracy': float(accuracy_score(y, pred)),
            'balanced_accuracy': float(balanced_accuracy_score(y, pred)) if both_classes else None,
            'mcc': float(matthews_corrcoef(y, pred)),
            'roc_auc': float(roc_auc_score(y, p)) if both_classes else None,
            'brier': float(brier_score_loss(y, p)), 'log_loss': float(log_loss(y, p, labels=[0, 1])),
            'up_fraction': float(y.mean()), 'predicted_up_fraction': float(pred.mean())}


def probabilities(model, data):
    model.eval()
    with torch.inference_mode():
        return torch.sigmoid(model(*data)).numpy()


def fit(kind, partitions, vectors, dimension, fixed_epochs=None):
    # Every candidate sees the same rows and uses train-only normalization.
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    data = {name: tensors(rows, vectors, dimension) for name, rows in partitions.items()}
    market = data['train'][0]
    mean = market.mean(0)
    scale = market.std(0, unbiased=False).clamp_min(1e-6)
    model = DirectionModel(kind, dimension, mean, scale)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.01 if 'logistic' in kind else .003,
                                  weight_decay=.01)
    labels = {name: torch.tensor([s['label'] for s in rows], dtype=torch.float32)
              for name, rows in partitions.items()}
    loss_fn = nn.BCEWithLogitsLoss()
    best_loss, best_state, stale, best_epoch = float('inf'), None, 0, 0
    for epoch in range(1, (fixed_epochs or 200) + 1):
        model.train()
        optimizer.zero_grad()
        loss = loss_fn(model(*data['train']), labels['train'])
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
            validation_loss = float(loss_fn(model(*data['validation']), labels['validation']))
        if validation_loss < best_loss - 1e-5:
            best_loss, best_state, stale, best_epoch = validation_loss, copy.deepcopy(model.state_dict()), 0, epoch
        else:
            stale += 1
        if stale >= 25:
            break
    model.load_state_dict(best_state)
    result = {'best_epoch': best_epoch, 'parameters': sum(p.numel() for p in model.parameters()),
              'validation': metrics(partitions['validation'], probabilities(model, data['validation']))
              if 'validation' in partitions else None}
    return model, result, data


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def train(corpus_path, embeddings_path, output):
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    raw = corpus_path.read_bytes()
    corpus = json.loads(raw)
    corpus_hash = hashlib.sha256(raw).hexdigest()
    vectors, embedding_meta = load_vectors(embeddings_path)
    if embedding_meta['corpus_sha256'] != corpus_hash:
        raise ValueError('Embedding cache belongs to a different corpus; re-embed this export')
    report = {'schema_version': SCHEMA_VERSION, 'seed': SEED, 'corpus_sha256': corpus_hash,
              'exported_at': corpus['exported_at'], 'source_counts': corpus.get('source_counts', {}),
              'runtime': {'python': platform.python_version(), 'torch': torch.__version__,
                          'numpy': np.__version__, 'training_device': 'cpu'},
              'training': {'max_epochs': 200, 'early_stopping_patience': 25,
                           'early_stopping_metric': 'validation BCE', 'weight_decay': .01,
                           'tiny_holdout_fixed_epochs': 30, 'tiny_holdout_preset_model': 'fusion_gru',
                           'learning_rate': {'logistic': .01, 'neural': .003}},
              'embedding': embedding_meta, 'selection': 'highest validation balanced_accuracy, then lowest validation log_loss',
              'calibration': 'uncalibrated; scores are experimental, not validated confidence',
              'limitations': [
                  'Publication replay uses texts collected later; it is not a point-in-time backtest.',
                  'Only body-verified extracted text is used; this does not independently authenticate its source.',
                  'The universe is limited to already collected stocks; no survivorship-neutral universe is claimed.',
                  'Targets are unadjusted close direction; samples without adjusted references remain flagged.',
                  'Historical bars may have been revised. Frozen pretrained embeddings can contain historical knowledge.',
                  'Few, overlapping test dates and correlated stocks cannot establish statistical significance or trading profit.',
              ], 'protocols': {}}
    for mode in ('observed', 'publication_replay'):
        samples, audit = build_samples(corpus, mode)
        audit.pop('calendar')
        entry = {'audit': audit}
        report['protocols'][mode] = entry
        fixed_epochs = None
        try:
            partitions, split = temporal_split(samples)
        except ValueError as exc:
            entry['three_way_split_unavailable'] = str(exc)
            try:
                partitions, split = tiny_temporal_holdout(samples)
                fixed_epochs = 30
            except ValueError as small_exc:
                entry.update(status='insufficient_data', reason=str(small_exc))
                continue
        # Do not fit a nominal news model if its training interval contains no news.
        if not any(s['news_count'] for s in partitions['train']):
            entry.update(status='insufficient_data', reason='No news-bearing training samples')
            continue
        split.update({name + '_with_news': sum(s['news_count'] > 0 for s in rows)
                      for name, rows in partitions.items()})
        entry.update(status='smoke_test_only' if fixed_epochs else 'trained', split=split, models={})
        models, datasets = {}, {}
        for kind in CANDIDATES:
            model, result, data = fit(kind, partitions, vectors, embedding_meta['dimension'], fixed_epochs)
            models[kind], datasets[kind] = model, data
            entry['models'][kind] = result
            print(json.dumps({'protocol': mode, 'model': kind, **result}), flush=True)
        # Freeze selection before looking at ANY test metrics.
        if fixed_epochs:
            selected = 'fusion_gru'  # Preset before testing; never select by tiny test performance.
            entry['selection'] = 'preset fusion_gru, 30 epochs; no validation available'
        else:
            selected = max(CANDIDATES, key=lambda name: (
                entry['models'][name]['validation']['balanced_accuracy']
                if entry['models'][name]['validation']['balanced_accuracy'] is not None else -1,
                -entry['models'][name]['validation']['log_loss']))
            entry['selection'] = report['selection']
        entry['selected_model'] = selected
        prior = sum(s['label'] for s in partitions['train']) / len(partitions['train'])
        test_predictions = {'majority_prior': np.full(len(partitions['test']), prior)}
        entry['majority_prior'] = {'training_up_fraction': prior,
                                   'test': metrics(partitions['test'], test_predictions['majority_prior'])}
        artifact_dir = output / mode
        artifact_dir.mkdir(parents=True, exist_ok=True)
        checkpoints = {}
        for kind, model in models.items():
            probs = probabilities(model, datasets[kind]['test'])
            test_predictions[kind] = probs
            entry['models'][kind]['test'] = metrics(partitions['test'], probs)
            subset = [i for i, s in enumerate(partitions['test']) if s['news_count']]
            entry['models'][kind]['test_with_news'] = metrics(
                [partitions['test'][i] for i in subset], probs[subset]) if subset else None
            entry['models'][kind]['test_by_date'] = {
                day: metrics([s for s in partitions['test'] if s['date'] == day],
                             [p for s, p in zip(partitions['test'], probs) if s['date'] == day])
                for day in sorted({s['date'] for s in partitions['test']})}
            checkpoint = artifact_dir / (kind + '.pt')
            torch.save(model.state_dict(), checkpoint)
            checkpoints[kind] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        with (artifact_dir / 'test_predictions.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['code', 'date', 'label_end', 'label', 'return_3d',
                                                        'news_count', *test_predictions])
            writer.writeheader()
            for i, sample in enumerate(partitions['test']):
                writer.writerow({**{key: sample[key] for key in ('code', 'date', 'label_end', 'label', 'return_3d', 'news_count')},
                                 **{name: float(p[i]) for name, p in test_predictions.items()}})
        manifest = {'schema_version': SCHEMA_VERSION, 'mode': mode, 'selected_model': selected,
                    'evaluation_status': entry['status'], 'selection': entry['selection'],
                    'market_features': MARKET_NAMES, 'news_days': NEWS_DAYS, 'embedding': embedding_meta,
                    'corpus_sha256': corpus_hash, 'calibration': report['calibration'],
                    'eligible_for_trading': False, 'split': split, 'checkpoints_sha256': checkpoints}
        write_json(artifact_dir / 'manifest.json', manifest)
    write_json(output / 'report.json', report)
    print(json.dumps({'report': str(output / 'report.json'),
                      'protocols': {k: v['status'] for k, v in report['protocols'].items()}}), flush=True)
    return report


def predict(corpus_path, embeddings_path, artifacts, output, kind=None):
    manifest = json.loads((artifacts / 'manifest.json').read_text())
    if manifest['schema_version'] != SCHEMA_VERSION or manifest['market_features'] != MARKET_NAMES:
        raise ValueError('Incompatible model manifest')
    corpus_bytes = corpus_path.read_bytes()
    corpus = json.loads(corpus_bytes)
    vectors, embedding_meta = load_vectors(embeddings_path)
    if embedding_meta['corpus_sha256'] != hashlib.sha256(corpus_bytes).hexdigest():
        raise ValueError('Embedding cache belongs to a different corpus')
    for key in ('weights_sha256', 'dimension', 'input_format', 'pooling', 'max_length'):
        if embedding_meta[key] != manifest['embedding'][key]:
            raise ValueError('Embedding model/settings differ from the trained artifact')
    samples, _ = build_samples(corpus, 'observed', prediction=True)
    if not samples:
        raise ValueError('No complete market windows for prediction')
    # All input tensors are built using observed availability, even for a replay-trained artifact.
    kind = kind or manifest['selected_model']
    if kind not in CANDIDATES:
        raise ValueError('Unknown model kind')
    checkpoint = artifacts / (kind + '.pt')
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != manifest['checkpoints_sha256'][kind]:
        raise ValueError('Checkpoint does not match this experiment manifest')
    state = torch.load(checkpoint, map_location='cpu', weights_only=True)
    model = DirectionModel(kind, embedding_meta['dimension'],
                           state['market_mean'], state['market_scale'])
    model.load_state_dict(state)
    probs = probabilities(model, tensors(samples, vectors, embedding_meta['dimension']))
    result = {'model': kind, 'default_model': manifest['selected_model'],
              'selection': manifest['selection'], 'evaluation_status': manifest['evaluation_status'],
              'uses_news': kind != 'market_logistic', 'training_protocol': manifest['mode'],
              'status': 'research_only', 'eligible_for_trading': False,
              'calibration': manifest['calibration'], 'horizon_trading_days': 3,
              'items': [{'code': s['code'], 'as_of': s['cutoff'], 'available_news_count': s['news_count'],
                         'up_probability_uncalibrated': float(p),
                         'direction': 'up' if p >= .5 else 'down'} for s, p in zip(samples, probs)]}
    write_json(output, result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('train', 'predict'):
        command = sub.add_parser(name)
        command.add_argument('--corpus', type=Path, required=True)
        command.add_argument('--embeddings', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True)
        if name == 'predict':
            command.add_argument('--artifacts', type=Path, required=True)
            command.add_argument('--model', choices=CANDIDATES,
                                 help='Inspect a trained candidate; default uses validation selection')
    args = parser.parse_args()
    if args.command == 'train':
        train(args.corpus, args.embeddings, args.output)
    else:
        predict(args.corpus, args.embeddings, args.artifacts, args.output, args.model)
