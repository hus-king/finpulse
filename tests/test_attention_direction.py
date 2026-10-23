import copy
from datetime import date, timedelta
import importlib.util
import unittest

from backend.attention_direction_data import available_cards, company_cards, daily_market_sequence


class CompanyFeatureTests(unittest.TestCase):
    def test_future_fact_version_never_replaces_known_business(self):
        versions = [
            {'id': 'old', 'code': '600001', 'main_business': '主营业务为集装箱制造、物流服务',
             'source': 'public filing', 'url': 'https://example.com/old', 'known_at': '2026-10-01T10:00:00+08:00'},
            {'id': 'future', 'code': '600001', 'main_business': '主营业务为软件开发、数据服务',
             'source': 'public filing', 'url': 'https://example.com/new', 'known_at': '2026-10-15T16:00:00+08:00'},
        ]
        bundle = company_cards({'versions': versions})
        self.assertEqual(available_cards(bundle, '600001', '2026-09-30T15:00:00+08:00'), [])
        known = available_cards(bundle, '600001', '2026-10-15T15:00:00+08:00')
        self.assertEqual(known, next(v['cards'] for v in bundle['versions'] if v['id'] == 'old'))
        self.assertNotEqual(known, available_cards(bundle, '600001', '2026-10-16T15:00:00+08:00'))
        self.assertEqual(available_cards(bundle, '600002', '2026-10-16T15:00:00+08:00'), [])

    def test_company_fragments_are_verbatim_and_source_required(self):
        text = '集装箱制造业务、物流服务业务；装备制造业务'
        bundle = company_cards({'versions': [{'id': 'a', 'code': '600001', 'main_business': text,
                               'known_at': '2026-10-01T10:00:00+08:00', 'source': 'filing', 'url': 'https://example.com'}]})
        self.assertGreater(len(bundle['cards']), 1)
        self.assertTrue(all(card['text'] in text for card in bundle['cards']))
        self.assertFalse(company_cards({'versions': [{'id': 'x', 'code': '600001', 'main_business': text}]})['cards'])

    def test_daily_features_do_not_read_prices_after_their_daily_close(self):
        days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(22)]
        bars = {day: {'open': 10 + i, 'close': 10.5 + i, 'high': 11 + i, 'low': 9 + i, 'volume': 100 + i}
                for i, day in enumerate(days)}
        first = daily_market_sequence(bars, days[:21])
        changed = copy.deepcopy(bars)
        changed[days[21]]['close'] = 1000000
        self.assertEqual(first, daily_market_sequence(changed, days[:21]))
        changed[days[20]]['close'] += 10
        self.assertEqual(first[:-1], daily_market_sequence(changed, days[:21])[:-1])


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('sklearn'), 'Optional training dependencies')
class AttentionModelTests(unittest.TestCase):
    def setUp(self):
        import torch
        from backend.attention_direction import AttentionDirectionModel
        torch.set_num_threads(1)
        torch.manual_seed(7)
        self.torch = torch
        self.model = AttentionDirectionModel(torch.randn(8, 8), torch.zeros(10), torch.ones(10), torch.zeros(6), torch.ones(6))
        self.data = {'news_ids': torch.zeros((2, 10, 3), dtype=torch.long), 'news_time': torch.zeros((2, 10, 3, 2)),
                     'fact_ids': torch.zeros((2, 3), dtype=torch.long), 'day_market': torch.zeros((2, 10, 6)),
                     'day_time': torch.zeros((2, 10, 4)), 'market': torch.zeros((2, 10))}

    def test_no_news_no_company_is_finite_and_trains(self):
        torch = self.torch
        out, attention = self.model(**self.data, explain=True)
        self.assertTrue(torch.isfinite(out).all())
        out.sum().backward()
        self.assertTrue(all(torch.isfinite(p.grad).all() for p in self.model.parameters() if p.grad is not None))
        self.assertTrue(torch.equal(attention['news'][..., -1], torch.ones((2, 10))))
        self.assertTrue(torch.equal(attention['company'][..., -1], torch.ones((2, 10, 3))))

    def test_daily_article_order_is_invariant(self):
        torch = self.torch
        self.model.eval()
        self.data['news_ids'][:, :, :2] = torch.tensor([1, 2])
        self.data['news_time'] = torch.randn(2, 10, 3, 2)
        self.data['fact_ids'][:, 0] = 3
        first = self.model(**self.data)
        changed = {k: v.clone() for k, v in self.data.items()}
        changed['news_ids'] = changed['news_ids'][:, :, [1, 0, 2]]
        changed['news_time'] = changed['news_time'][:, :, [1, 0, 2]]
        self.assertTrue(torch.allclose(first, self.model(**changed), atol=1e-6))

    def test_company_ablation_cannot_read_fact_contents(self):
        torch = self.torch
        self.model.use_company = False
        self.model.eval()
        self.data['news_ids'][:, :, 0] = 1
        first = self.model(**self.data)
        changed = {k: v.clone() for k, v in self.data.items()}
        changed['fact_ids'][:, 0] = 7
        self.assertTrue(torch.equal(first, self.model(**changed)))

    def test_padding_contents_do_not_change_outputs(self):
        torch = self.torch
        self.model.eval()
        self.data['news_ids'][:, :, 0] = 1
        self.data['fact_ids'][:, 0] = 2
        first = self.model(**self.data)
        with torch.no_grad():
            self.model.vectors[0].fill_(100000)
        self.assertTrue(torch.equal(first, self.model(**self.data)))

    def test_test_data_cannot_change_fit_or_train_normalization(self):
        torch = self.torch
        from scripts.train_attention_direction import fit_attention
        samples = {'train': [{'label': 0}, {'label': 1}], 'test': [{'label': 0}, {'label': 1}]}
        data = {'train': {k: v.clone() for k, v in self.data.items()}, 'test': {k: v.clone() for k, v in self.data.items()}}
        data['train']['market'][1] = 2
        data['train']['day_market'][1] = 2
        changed_samples, changed_data = copy.deepcopy(samples), copy.deepcopy(data)
        changed_samples['test'][0]['label'] = 1
        changed_data['test']['market'].fill_(100000)
        vectors = self.model.vectors.detach().clone()
        first, _ = fit_attention('company_attention_gru', samples, data, vectors, fixed_epochs=3)
        second, _ = fit_attention('company_attention_gru', changed_samples, changed_data, vectors, fixed_epochs=3)
        for key, value in first.state_dict().items():
            self.assertTrue(torch.equal(value, second.state_dict()[key]), key)
        self.assertTrue(torch.equal(first.market_mean, torch.ones(10)))
        self.assertTrue(torch.equal(first.day_mean, torch.ones(6)))

    def test_saved_state_roundtrip_preserves_predictions(self):
        import io
        from backend.attention_direction import AttentionDirectionModel
        torch = self.torch
        self.model.eval()
        first = self.model(**self.data)
        stream = io.BytesIO()
        torch.save(self.model.state_dict(), stream)
        stream.seek(0)
        state = torch.load(stream, map_location='cpu', weights_only=True)
        restored = AttentionDirectionModel(state['vectors'], state['market_mean'], state['market_scale'], state['day_mean'], state['day_scale'])
        restored.load_state_dict(state)
        restored.eval()
        self.assertTrue(torch.equal(first, restored(**self.data)))


if __name__ == '__main__':
    unittest.main()
