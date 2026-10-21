import copy
import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from backend.quant.features import features, FEATURE_NAMES
from backend.quant.strategy import QuantStrategy


def history(count=100):
    return [{'date': (date(2025, 1, 1)+timedelta(days=i)).isoformat(), 'open': 10+i*.01,
             'close': 10+i*.01, 'high': 10.1+i*.01, 'low': 9.9+i*.01, 'volume': 10000+i} for i in range(count)]


class QuantModelTests(unittest.TestCase):
    def test_features_use_only_past_and_reject_bad_bars(self):
        bars = history()
        before = copy.deepcopy(bars)
        vector = features(bars)
        self.assertEqual(len(vector), 12)
        self.assertEqual(len(FEATURE_NAMES), 12)
        self.assertEqual(bars, before)
        self.assertAlmostEqual(vector[0], bars[-1]['close']/bars[-2]['close']-1)
        for bad in [history(60), history()+[dict(bars[-1], volume=float('nan'))], list(reversed(bars))]:
            with self.assertRaises(ValueError): features(bad)

    def test_json_model_checksum_dimensions_and_prediction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = {'schema': 1, 'feature_version': 'ohlcv-v1', 'feature_names': FEATURE_NAMES,
                     'mean': [0]*12, 'scale': [1]*12, 'layers': [{'weight': [[0]*12], 'bias': [.02]}],
                     'universe': ['000001','600000'], 'trained_through': '2025-01-01', 'threshold': .005,
                     'version': 'test-v1'}
            raw = json.dumps(model).encode()
            (root/'model.json').write_bytes(raw)
            (root/'report.json').write_text(json.dumps({'model_sha256': hashlib.sha256(raw).hexdigest()}))
            strategy = QuantStrategy(root)
            self.assertAlmostEqual(strategy.predict(features(history())), .02)
            result = strategy.decide({'000001': history(), '600000': history()}, history()[-1]['date'])
            self.assertEqual(result['target_weights'], {'000001': .2, '600000': .2})
            self.assertLessEqual(sum(result['target_weights'].values()), .8)
            (root/'model.json').write_text('{}')
            with self.assertRaises(ValueError): QuantStrategy(root)

    def test_strategy_requires_all_symbols_at_same_signal_date(self):
        strategy = QuantStrategy.__new__(QuantStrategy)
        strategy.model = {'universe':['000001','600000'], 'version':'test', 'threshold':.001}
        strategy.predict = lambda _: .01
        with self.assertRaises(ValueError): strategy.decide({'000001': history(), '600000': history(99)}, history()[-1]['date'])
