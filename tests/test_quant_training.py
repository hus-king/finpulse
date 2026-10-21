import unittest
from scripts.quant.train import split_samples, metrics, make_samples
from backend.quant.features import corporate_action
from test_quant_model import history

class QuantTrainingTests(unittest.TestCase):
    def test_split_by_common_dates_and_purge_labels(self):
        rows = [{'date':f'2025-{i//28+1:02}-{i%28+1:02}', 'label_end':f'2025-{(i+6)//28+1:02}-{(i+6)%28+1:02}', 'code':c} for i in range(100) for c in ['000001','600000']]
        train,val,test = split_samples(rows)
        self.assertTrue(set(x['date'] for x in train).isdisjoint(x['date'] for x in val))
        self.assertLess(max(x['label_end'] for x in train),min(x['date'] for x in val))
        self.assertLess(max(x['label_end'] for x in val),min(x['date'] for x in test))

    def test_label_begins_after_signal_not_same_close(self):
        bars = history(110)
        samples = make_samples({'000001': {'raw':bars,'adjusted':bars}})
        row=samples[0]; i=next(i for i,b in enumerate(bars) if b['date']==row['date'])
        self.assertAlmostEqual(row['y'],bars[i+6]['open']/bars[i+1]['open']-1)
        self.assertEqual(row['label_end'],bars[i+6]['date'])

    def test_corporate_action_and_invalid_alignment(self):
        raw=history(); adjusted=[dict(b) for b in raw]; adjusted[-1]['close']*=1.05
        self.assertTrue(corporate_action(raw,adjusted))
        with self.assertRaises(ValueError): corporate_action(raw,adjusted[:-1])

    def test_metrics_include_drawdown_and_actual_return(self):
        result=metrics([100,110,99,105],2,3)
        self.assertAlmostEqual(result['total_return'],.05)
        self.assertAlmostEqual(result['max_drawdown'],.1)
        self.assertEqual(result['trades'],3)

    def test_backtest_deducts_commission_slippage_and_never_uses_same_day_close(self):
        from scripts.quant.train import backtest
        bars=history(105)
        for b in bars: b.update(open=10,close=10,high=10,low=10,volume=10000)
        class Fixed:
            def decide(self,histories,signal):return {'target_weights':{'000001':.2}}
        result=backtest({'000001':{'raw':bars,'adjusted':bars}},Fixed(),[b['date'] for b in bars[70:80]])
        self.assertLess(result['total_return'],0)
        self.assertGreater(result['total_return'],-.01)
        changed=[dict(b) for b in bars];changed[70]['close']=1000
        # Signal close cannot be the fill price; next open remains 10.
        alternate=backtest({'000001':{'raw':changed,'adjusted':changed}},Fixed(),[bars[70]['date']])
        self.assertEqual(alternate['equity_curve'],result['equity_curve'][:2])
