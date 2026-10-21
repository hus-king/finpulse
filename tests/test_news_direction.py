import copy
from datetime import date, timedelta
import importlib.util
import unittest

from backend.news_direction import (action_in_window, build_samples, documents, market_features,
                                    publication_time, temporal_split, tiny_temporal_holdout, valid_bars)


def fixture():
    days, day = [], date(2026, 1, 1)
    while len(days) < 90:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    bars = [{'date': d, 'open': 100 + i, 'close': 100 + i, 'high': 101 + i,
             'low': 99 + i, 'volume': 1000 + i} for i, d in enumerate(days)]
    article = {'id': 'old', 'code': '600519', 'company': '贵州茅台', 'title': '测试事件',
               'url': 'https://example.com/news', 'text': '一份此前已发布的公司新闻。' * 15,
               'publication_date': days[25], 'publication_timestamp': days[25] + 'T14:00:00+08:00',
               'first_seen_at': days[28] + 'T10:00:00+08:00',
               'text_source': 'extracted_body', 'date_status': 'body_verified'}
    return {'schema_version': 1, 'exported_at': days[-1] + 'T18:00:00+08:00',
            'news': [article], 'markets': {'600519': {'raw': bars, 'adjusted': []}}}, days


class DirectionDataTests(unittest.TestCase):
    def test_export_sqlite_connection_cannot_write_or_create_missing_database(self):
        from pathlib import Path
        import sqlite3
        import tempfile
        from types import SimpleNamespace
        from scripts.export_direction_data import read_connection
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.db'
            store = SimpleNamespace(dialect='sqlite', path=path)
            with self.assertRaises(sqlite3.OperationalError):
                with read_connection(store):
                    pass
            self.assertFalse(path.exists())
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE example(value INTEGER)')
            with read_connection(store) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM example').fetchone()[0], 0)
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute('INSERT INTO example VALUES(1)')

    def test_first_seen_controls_observed_protocol(self):
        corpus, days = fixture()
        observed, _ = build_samples(corpus)
        replay, _ = build_samples(corpus, 'publication_replay')
        self.assertEqual(observed[0]['date'], days[28])
        self.assertEqual(replay[0]['date'], days[25])
        self.assertEqual(observed[0]['news_count'], 1)
        self.assertEqual(observed[0]['label_end'], days[31])

    def test_date_only_news_waits_until_next_close(self):
        corpus, days = fixture()
        article = corpus['news'][0]
        article['publication_timestamp'] = days[25]
        article['first_seen_at'] = days[25] + 'T10:00:00+08:00'
        self.assertEqual(publication_time(article).hour, 23)
        samples, _ = build_samples(corpus, 'publication_replay')
        self.assertEqual(samples[0]['news_count'], 0)
        self.assertEqual(samples[1]['news_count'], 1)

    def test_after_close_news_and_intraday_candle_are_excluded(self):
        corpus, days = fixture()
        corpus['exported_at'] = days[40] + 'T14:59:00+08:00'
        corpus['news'][0]['first_seen_at'] = days[39] + 'T15:01:00+08:00'
        prediction, _ = build_samples(corpus, prediction=True)
        self.assertEqual(prediction[0]['date'], days[39])
        self.assertEqual(prediction[0]['news_count'], 0)

    def test_future_price_changes_label_but_not_features(self):
        corpus, days = fixture()
        before, _ = build_samples(corpus, 'publication_replay')
        bar = corpus['markets']['600519']['raw'][28]
        bar.update(open=50, close=50, high=51, low=49)
        after, _ = build_samples(corpus, 'publication_replay')
        left = next(s for s in before if s['date'] == days[25])
        right = next(s for s in after if s['date'] == days[25])
        self.assertEqual(left['market'], right['market'])
        self.assertEqual(left['news'], right['news'])
        self.assertNotEqual(left['label'], right['label'])

    def test_missing_trading_bar_does_not_shift_horizon(self):
        corpus, days = fixture()
        corpus['markets']['000001'] = copy.deepcopy(corpus['markets']['600519'])
        del corpus['markets']['600519']['raw'][27]
        samples, audit = build_samples(corpus, 'publication_replay')
        self.assertFalse(any(s['date'] == days[25] for s in samples))
        self.assertGreater(audit['skipped']['missing_or_suspended_bar'], 0)

    def test_latest_unseen_version_never_replaces_known_text(self):
        corpus, days = fixture()
        new = {**corpus['news'][0], 'id': 'new', 'text': '后来更新的完全不同正文。' * 20,
               'first_seen_at': days[30] + 'T16:00:00+08:00'}
        corpus['news'].append(new)
        before, _ = build_samples(corpus)
        sample = next(s for s in before if s['date'] == days[30])
        self.assertEqual([x for day in sample['news'] for x in day], ['old'])
        sample = next(s for s in before if s['date'] == days[31])
        self.assertEqual([x for day in sample['news'] for x in day], ['new'])

    def test_reprints_are_not_independent_samples_or_news(self):
        corpus, days = fixture()
        corpus['news'].append({**corpus['news'][0], 'id': 'reprint', 'url': 'https://other.example/reprint'})
        samples, _ = build_samples(corpus)
        self.assertEqual(samples[0]['news_count'], 1)
        self.assertEqual(len({(s['code'], s['date']) for s in samples}), len(samples))

    def test_fragments_and_unverified_dates_are_rejected(self):
        corpus, _ = fixture()
        corpus['news'] += [{**corpus['news'][0], 'id': 'fragment', 'text_source': 'search_fragments'},
                           {**corpus['news'][0], 'id': 'unknown', 'date_status': 'metadata_only'}]
        accepted, rejected = documents(corpus)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, {'not_full_body': 1, 'date_not_body_verified': 1})

    def test_bad_price_and_conflicting_dates_fail_closed(self):
        corpus, _ = fixture()
        bars = corpus['markets']['600519']['raw']
        with self.assertRaises(ValueError):
            valid_bars([bars[0], {**bars[0], 'volume': 0}])
        with self.assertRaises(ValueError):
            valid_bars([{**bars[0], 'close': float('nan')}])
        with self.assertRaises(ValueError):
            market_features([bars[0]] * 20)

    def test_affine_adjustment_does_not_mistake_cash_offset_for_an_action(self):
        corpus, _ = fixture()
        raw = corpus['markets']['600519']['raw'][:21]
        adjusted = {b['date']: {**b, **{k: b[k] - 2 for k in ('open', 'close', 'high', 'low')}} for b in raw}
        self.assertFalse(action_in_window(raw, adjusted))
        for b in raw[10:]:
            adjusted[b['date']]['close'] -= 1
        self.assertTrue(action_in_window(raw, adjusted))
        self.assertIsNone(action_in_window(raw, {}))

    def test_flat_target_is_not_a_down_label(self):
        corpus, days = fixture()
        bars = corpus['markets']['600519']['raw']
        bars[28].update(open=125, close=125, high=126, low=124)
        samples, audit = build_samples(corpus, 'publication_replay')
        self.assertFalse(any(s['date'] == days[25] for s in samples))
        self.assertGreater(audit['skipped']['flat_target'], 0)

    def test_all_stocks_share_split_dates_and_label_intervals_do_not_overlap(self):
        samples = []
        for i in range(40):
            for code in ['600519', '000001']:
                samples.append({'code': code, 'date': (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
                                'label_end': (date(2026, 1, 1) + timedelta(days=i + 3)).isoformat(),
                                'label': i % 2})
        parts, audit = temporal_split(samples)
        self.assertLess(max(s['label_end'] for s in parts['train']), audit['validation_start'])
        self.assertLess(max(s['label_end'] for s in parts['validation']), audit['test_start'])
        self.assertFalse(set(s['date'] for s in parts['train']) & set(s['date'] for s in parts['test']))
        self.assertEqual(audit['purged_samples'], 12)

    def test_short_history_is_not_randomly_split(self):
        with self.assertRaisesRegex(ValueError, 'need at least 15'):
            temporal_split([{'date': '2026-10-09'}])

    def test_tiny_holdout_purges_intervals_without_inventing_validation(self):
        samples = [{'date': (date(2026, 10, 8) + timedelta(days=i)).isoformat(),
                    'label_end': (date(2026, 10, 11) + timedelta(days=i)).isoformat(),
                    'label': label} for i in range(6) for label in (0, 1)]
        parts, audit = tiny_temporal_holdout(samples)
        self.assertEqual(len(parts['train']), 2)
        self.assertEqual(len(parts['test']), 4)
        self.assertIsNone(audit['validation'])
        self.assertLess(max(s['label_end'] for s in parts['train']), audit['test_start'])


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('sklearn'),
                     'Requires optional training dependencies (available on the GPU server)')
class TrainingIsolationTests(unittest.TestCase):
    def test_test_prices_and_labels_cannot_change_fit_or_scaler(self):
        import numpy as np
        import torch
        from scripts.train_news_direction import fit
        torch.set_num_threads(1)
        vectors = {'a': np.ones(8, dtype=np.float32)}
        def rows(n):
            return [{'date': f'2026-01-{i + 1:02}', 'label': i % 2,
                     'market': [float(i)] * 10, 'news': [['a']] + [[]] * 9} for i in range(n)]
        parts = {'train': rows(8), 'validation': rows(4), 'test': rows(4)}
        changed = copy.deepcopy(parts)
        for sample in changed['test']:
            sample['label'] = 1 - sample['label']
            sample['market'] = [1000000.] * 10
        for fixed_epochs in (None, 30):
            original = parts if fixed_epochs is None else {k: v for k, v in parts.items() if k != 'validation'}
            modified = changed if fixed_epochs is None else {k: v for k, v in changed.items() if k != 'validation'}
            first, _, _ = fit('fusion_gru', original, vectors, 8, fixed_epochs)
            second, _, _ = fit('fusion_gru', modified, vectors, 8, fixed_epochs)
            for key, value in first.state_dict().items():
                self.assertTrue(torch.equal(value, second.state_dict()[key]), key)
            self.assertTrue(torch.equal(first.market_mean, torch.full((10,), 3.5)))

    def test_saved_prediction_uses_observed_news_and_checks_artifact_integrity(self):
        import hashlib
        import json
        from pathlib import Path
        import tempfile
        import numpy as np
        import torch
        from backend.news_direction import MARKET_NAMES, SCHEMA_VERSION
        from scripts.train_news_direction import DirectionModel, predict
        corpus, days = fixture()
        corpus['news'][0].update(publication_date=days[85],
                                 publication_timestamp=days[85] + 'T14:00:00+08:00',
                                 first_seen_at=days[-1] + 'T16:00:00+08:00')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus_path = root / 'corpus.json'
            corpus_path.write_text(json.dumps(corpus))
            meta = {'corpus_sha256': hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
                    'weights_sha256': 'test', 'dimension': 8, 'input_format': 'test',
                    'pooling': 'test', 'max_length': 1024}
            np.savez(root / 'vectors.npz', ids=np.asarray(['old']), vectors=np.ones((1, 8), dtype=np.float32),
                     metadata=np.asarray(json.dumps(meta)))
            model = DirectionModel('news_logistic', 8, np.zeros(10), np.ones(10))
            with torch.no_grad():
                model.head.weight.zero_()
                model.head.bias.zero_()
                model.head.weight[0, 8] = 1
            checkpoint = root / 'news_logistic.pt'
            torch.save(model.state_dict(), checkpoint)
            manifest = {'schema_version': SCHEMA_VERSION, 'market_features': MARKET_NAMES,
                        'embedding': meta, 'selected_model': 'news_logistic',
                        'mode': 'publication_replay', 'calibration': 'uncalibrated',
                        'selection': 'test', 'evaluation_status': 'test',
                        'checkpoints_sha256': {'news_logistic': hashlib.sha256(checkpoint.read_bytes()).hexdigest()}}
            (root / 'manifest.json').write_text(json.dumps(manifest))
            result = predict(corpus_path, root / 'vectors.npz', root, root / 'prediction.json')
            self.assertEqual(result['items'][0]['available_news_count'], 0)
            self.assertEqual(result['items'][0]['up_probability_uncalibrated'], .5)
            checkpoint.write_bytes(checkpoint.read_bytes() + b'changed')
            with self.assertRaisesRegex(ValueError, 'Checkpoint does not match'):
                predict(corpus_path, root / 'vectors.npz', root, root / 'prediction.json')


if __name__ == '__main__':
    unittest.main()
