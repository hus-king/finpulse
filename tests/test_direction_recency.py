import copy
import importlib.util
import unittest

from backend.attention_direction_data import indexed_features, session_news_time, trading_clock
from backend.direction_news_filter import clean_article, header_timestamp
from backend.news_direction import build_samples, documents, timestamp
from scripts.embed_direction_news import text_chunks
from tests.test_news_direction import fixture


class NewsCleaningTests(unittest.TestCase):
    def article(self, title, text):
        corpus, _ = fixture()
        return {**corpus['news'][0], 'title': title, 'text': text, 'publication_date': '2026-10-12'}

    def test_pure_stock_quote_is_excluded_but_product_prices_are_not(self):
        article = self.article('贵州茅台午后涨停', '贵州茅台午后涨停，成交额达到十亿元，同板块多只个股跟涨。' * 5)
        self.assertTrue(clean_article(article)[0]['direction_excluded'])
        article = self.article('光伏组件价格翻倍', '当地光伏组件价格上涨，限电导致需求增加，经销商报价发生变化。' * 5)
        view, audit = clean_article(article)
        self.assertFalse(view['direction_excluded'])
        self.assertEqual(audit['category'], 'event')
        self.assertIn('组件价格上涨', view['text'])

    def test_mixed_article_keeps_event_and_cancellation_risk(self):
        article = self.article('贵州茅台股价大涨', '贵州茅台股价大涨，换手率达到百分之三。\n'
                               + '公司签订供货合同，总金额五亿元。' * 4
                               + '如果客户取消合同，股价可能承压，利润贡献存在不确定性。')
        view, audit = clean_article(article)
        self.assertEqual(audit['category'], 'mixed')
        self.assertNotIn('换手率', view['text'])
        self.assertIn('客户取消合同', view['text'])
        self.assertIn('供货合同', view['title'])
        self.assertTrue(audit['removed_market_sentences'])
        article = self.article('产品争议后股价跌停', '三台汽车刹车测试引发争议，公司股价跌停。' * 6)
        view, _ = clean_article(article)
        self.assertFalse(view['direction_excluded'])
        self.assertIn('刹车测试引发争议', view['text'])

    def test_share_issuance_is_event_and_paywall_is_not_full_body(self):
        article = self.article('公司涨停', '公司因员工行权及股份奖励计划新增发行普通股。' * 6)
        self.assertFalse(clean_article(article)[0]['direction_excluded'])
        article['text'] += '\n单篇付费18元可解锁全文'
        self.assertTrue(clean_article(article)[0]['direction_excluded'])

    def test_uncertain_material_is_quarantined_instead_of_assumed_event(self):
        article = self.article('行业观察', '这是一篇没有明确事件依据的行业背景介绍，主要介绍公司的历史和概况。' * 5)
        view, audit = clean_article(article)
        self.assertTrue(view['direction_excluded'])
        self.assertEqual(audit['reason'], 'needs_review')

    def test_stock_target_price_is_not_a_product_price_event(self):
        article = self.article('贵州茅台目标价上调', '机构上调贵州茅台股票目标价格，预计股票价格上涨。' * 6)
        view, audit = clean_article(article)
        self.assertTrue(view['direction_excluded'])
        self.assertEqual(audit['category'], 'market_only')

    def test_policy_legal_and_capital_events_are_not_lost_to_quote_words(self):
        cases = [('公司跌停后回应司法部指控', '美国司法部起诉并指控公司操纵集装箱价格，公司发布回应。'),
                 ('机器人板块上涨', '我国首批六项人形机器人国家标准已批准发布，明确实施时间。'),
                 ('控股子公司参设基金完成备案', '公司子公司出资设立股权投资基金，基金已完成备案。')]
        for title, text in cases:
            with self.subTest(title=title):
                self.assertFalse(clean_article(self.article(title, text * 6))[0]['direction_excluded'])

    def test_same_day_movement_report_is_stripped_from_event_sentence(self):
        # Time-trap guard: a same-day "该股涨/跌" report must not reach the feature,
        # even when it shares a sentence with the event.
        article = self.article('公司签订合同', '公司签订供货合同后股价大涨5%，总金额五亿元。' * 5)
        view, audit = clean_article(article)
        self.assertEqual(audit['category'], 'mixed')
        self.assertIn('供货合同', view['text'])
        self.assertNotIn('大涨', view['text'])

    def test_business_growth_figures_are_not_treated_as_movement(self):
        article = self.article('公司签订合同', '公司签订供货合同，营收同比大涨，总金额五亿元。' * 5)
        view, audit = clean_article(article)
        self.assertFalse(view['direction_excluded'])
        self.assertIn('营收', view['text'])
        self.assertIn('大涨', view['text'])

    def test_related_article_footer_cannot_supply_event_evidence(self):
        article = self.article('贵州茅台午后涨停', '贵州茅台股价上涨，成交额达到十亿元。' * 5
                               + '\n相关新闻\n公司签订重大供货合同并投产新项目。')
        view, audit = clean_article(article)
        self.assertTrue(view['direction_excluded'])
        self.assertEqual(audit['category'], 'market_only')
        self.assertNotIn('供货合同', view['text'])

    def test_header_time_does_not_use_body_event_time_or_metadata(self):
        self.assertEqual(header_timestamp('2026年10月12日 09:20\n正文', '2026-10-12'), '2026-10-12T09:20:59+08:00')
        self.assertEqual(header_timestamp('公司将在2026年10月12日 09:20召开会议。', '2026-10-12'), '2026-10-12')
        self.assertEqual(header_timestamp('2026年10月13日 09:20', '2026-10-12'), '2026-10-12')
        article = self.article('公司签订合同', '公司签订合同，介绍交付安排及取消条件。' * 8)
        article['publication_timestamp'] = '2026-10-12T00:00:00+08:00'
        self.assertEqual(clean_article(article)[0]['publication_timestamp'], '2026-10-12')


class SessionDataTests(unittest.TestCase):
    def corpus(self):
        corpus, days = fixture()
        corpus['direction_policy'] = {'version': 'event-news-session-v2', 'signal_start': days[20]}
        return corpus, days

    def test_late_ingestion_is_bucketed_now_but_does_not_refresh_publication_age(self):
        corpus, days = self.corpus()
        rows, _ = build_samples(corpus)
        row = next(x for x in rows if x['date'] == days[28])
        self.assertEqual(row['news'][-1], ['old'])
        self.assertFalse(any(row['news'][:-1]))
        features, _ = indexed_features([row], corpus, {'versions': []}, ['old'])
        clock = features['news_time'][0][-1][0]
        self.assertEqual(len(clock), 8)
        self.assertGreater(clock[0], clock[2])
        self.assertGreater(clock[7], 3)
        replay, _ = build_samples(corpus, 'publication_replay')
        self.assertEqual(replay[0]['date'], days[25])

    def test_filtered_latest_version_does_not_resurrect_old_event(self):
        corpus, days = self.corpus()
        corpus['news'].append({**corpus['news'][0], 'id': 'excluded', 'direction_excluded': True,
                               'text': '', 'first_seen_at': days[30] + 'T10:00:00+08:00'})
        self.assertEqual(len(documents(corpus)[0]), 1)
        rows, _ = build_samples(corpus)
        self.assertEqual(next(x for x in rows if x['date'] == days[29])['news_count'], 1)
        self.assertEqual(next(x for x in rows if x['date'] == days[30])['news_count'], 0)

    def test_weekend_and_lunch_do_not_add_trading_exposure(self):
        calendar = ['2026-10-16', '2026-10-19', '2026-10-20']
        fri = trading_clock('2026-10-16T18:00:00+08:00', calendar)
        mon = trading_clock('2026-10-19T09:00:00+08:00', calendar)
        self.assertEqual(fri, mon)
        self.assertEqual(trading_clock('2026-10-19T11:30:00+08:00', calendar),
                         trading_clock('2026-10-19T13:00:00+08:00', calendar))
        article = {'published': timestamp('2026-10-19T09:00:00+08:00'),
                   'observed': timestamp('2026-10-19T09:05:00+08:00'),
                   'publication_timestamp': '2026-10-19T09:00:00+08:00'}
        values = session_news_time(article, timestamp('2026-10-19T15:00:00+08:00'), calendar, 'observed')
        self.assertEqual(values[3:7], [1., 0., 0., 0.])
        self.assertEqual(values[7], 1.)

    def test_unknown_time_and_after_close_wait_for_next_close(self):
        corpus, days = self.corpus()
        article = corpus['news'][0]
        article.update(publication_date=days[28], publication_timestamp=days[28], first_seen_at=days[28] + 'T10:00:00+08:00')
        rows, _ = build_samples(corpus)
        self.assertEqual(rows[0]['news_count'], 0)
        self.assertEqual(rows[1]['news_count'], 1)
        article.update(publication_timestamp=days[28] + 'T15:01:00+08:00')
        rows, _ = build_samples(corpus)
        self.assertEqual(rows[0]['news_count'], 0)
        self.assertEqual(rows[1]['news_count'], 1)

    def test_date_only_availability_bound_does_not_invent_after_hours_session(self):
        calendar = ['2026-10-16', '2026-10-19', '2026-10-20']
        article = {'published': timestamp('2026-10-16T23:59:59+08:00'),
                   'observed': timestamp('2026-10-16T23:59:59+08:00'), 'publication_timestamp': '2026-10-16'}
        values = session_news_time(article, timestamp('2026-10-19T15:00:00+08:00'), calendar, 'observed')
        self.assertEqual(values[1], 1.)
        self.assertEqual(values[3:7], [0., 0., 0., 0.])
        self.assertEqual(values[7], 2.)

    def test_required_adjustment_reference_is_not_silently_ignored(self):
        corpus, _ = self.corpus()
        corpus['direction_policy']['require_adjustment_reference'] = True
        rows, audit = build_samples(corpus)
        self.assertFalse(rows)
        self.assertGreater(audit['skipped']['missing_adjustment_reference'], 0)


class ChunkCoverageTests(unittest.TestCase):
    def test_long_body_is_fully_covered_with_repeated_context(self):
        class Tokenizer:
            def encode(self, text, **kwargs):
                return [ord(x) for x in text]
            def num_special_tokens_to_add(self, **kwargs):
                return 0
            def build_inputs_with_special_tokens(self, ids):
                return ids
        article = {'code': '600001', 'company': '测试', 'title': '合同', 'text': ''.join(chr(20000 + i) for i in range(500))}
        chunks = text_chunks(Tokenizer(), article, 128)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(ids) <= 128 for ids, _ in chunks))
        covered = {token for ids, _ in chunks for token in ids}
        self.assertTrue(set(map(ord, article['text'])) <= covered)
        self.assertEqual(sum(weight for _, weight in chunks), len(article['text']))


class FreshMarketTests(unittest.TestCase):
    def test_fresh_snapshot_replaces_mixed_units_and_missing_reference_fails(self):
        import json
        from pathlib import Path
        import tempfile
        from scripts.prepare_direction_corpus import prepare
        corpus, days = fixture()
        corpus['news'][0]['code'] = '000002'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.json'
            source.write_text(json.dumps(corpus))
            market = root / 'market'
            market.mkdir()
            bar = {'date': days[25], 'open': 10., 'close': 10., 'high': 11., 'low': 9., 'volume': 123.}
            saved = {'bars': [bar], 'captured_at': days[-1] + 'T18:00:00+08:00', 'source': 'AkShare / Tencent'}
            (market / '000002-raw.json').write_text(json.dumps(saved))
            with self.assertRaises(FileNotFoundError):
                prepare(source, root / 'missing', days[20], market)
            (market / '000002-adjusted.json').write_text(json.dumps(saved))
            prepare(source, root / 'ready', days[20], market)
            ready = json.loads((root / 'ready/corpus.json').read_text())
            self.assertEqual(set(ready['markets']), {'000002'})
            self.assertEqual(ready['markets']['000002']['raw'][0]['volume'], 12300.)
            self.assertEqual(len(ready['markets']['000002']['raw']), 1)


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('sklearn'), 'Optional training dependencies')
class RecencyModelTests(unittest.TestCase):
    def test_prepared_corpus_trains_saves_and_predicts_end_to_end(self):
        import contextlib
        import hashlib
        import io
        import json
        from pathlib import Path
        import tempfile
        import numpy as np
        from scripts.train_attention_direction import predict, train
        corpus, days = fixture()
        corpus['exported_at'] = days[38] + 'T18:00:00+08:00'
        corpus['direction_policy'] = {'version': 'event-news-session-v2', 'signal_start': days[20]}
        for i, bar in enumerate(corpus['markets']['600519']['raw']):
            price = 100. + (i % 6) * 2
            bar.update(open=price, close=price, high=price + 1, low=price - 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'corpus.json'
            source.write_text(json.dumps(corpus))
            meta = {'weights_sha256': 'test', 'dimension': 8, 'max_length': 1024, 'pooling': 'test', 'input_format': 'test',
                    'corpus_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
            news_path = root / 'news.npz'
            np.savez(news_path, ids=np.array(['old']), vectors=np.ones((1, 8), dtype=np.float32), metadata=np.array(json.dumps(meta)))
            company = root / 'company'
            company.mkdir()
            bundle = {'cards': [{'id': 'fact'}], 'versions': [{'id': 'v1', 'code': '600519', 'cards': ['fact'],
                                                            'known_at': days[20] + 'T10:00:00+08:00'}]}
            bundle_path = company / 'company-cards.json'
            bundle_path.write_text(json.dumps(bundle))
            fact_meta = {**meta, 'cards_sha256': hashlib.sha256(bundle_path.read_bytes()).hexdigest()}
            np.savez(company / 'company-embeddings.npz', ids=np.array(['fact']), vectors=np.ones((1, 8), dtype=np.float32),
                     metadata=np.array(json.dumps(fact_meta)))
            with contextlib.redirect_stdout(io.StringIO()):
                report = train(source, news_path, company, root / 'run', recency=True)
            for protocol in ('observed', 'publication_replay'):
                result = report['protocol_results'][protocol]
                self.assertEqual(result['selected_model'], 'recency_3d')
                self.assertEqual(len(result['reload_verification']), 6)
                self.assertTrue(all(x['max_absolute_difference'] == 0 for x in result['reload_verification'].values()))
            forecast = predict(source, news_path, company, root / 'run/observed', root / 'prediction.json', kind=None)
            self.assertEqual(forecast['model'], 'recency_3d')
            self.assertEqual(forecast['status'], 'research_only')
            self.assertEqual(len(forecast['items']), 1)
            self.assertTrue(np.isfinite(forecast['items'][0]['up_probability_uncalibrated']))

    def test_checkpoint_roundtrip_preserves_options_and_rejects_corruption(self):
        import hashlib
        from pathlib import Path
        import tempfile
        import torch
        from backend.attention_direction import AttentionDirectionModel
        from scripts.train_attention_direction import load_saved_model
        model = AttentionDirectionModel(torch.ones(2, 8), torch.zeros(10), torch.ones(10), torch.zeros(6), torch.ones(6),
                                        news_time_width=8, recency_half_life=3., use_company=False)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'recency_3d.pt'
            torch.save(model.state_dict(), path)
            manifest = {'width': 32, 'checkpoints_sha256': {'recency_3d': hashlib.sha256(path.read_bytes()).hexdigest()},
                        'model_options': {'recency_3d': {'news_time_width': 8, 'recency_half_life': 3., 'use_company': False}}}
            restored = load_saved_model(root, manifest, 'recency_3d')
            self.assertEqual(restored.recency_half_life, 3.)
            self.assertFalse(restored.use_company)
            for name, value in model.state_dict().items():
                self.assertTrue(torch.equal(value, restored.state_dict()[name]))
            path.write_bytes(path.read_bytes() + b'corrupted')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                load_saved_model(root, manifest, 'recency_3d')

    def test_decay_survives_daily_normalization_and_temporal_prior_is_recent(self):
        import torch
        from backend.attention_direction import AttentionDirectionModel
        torch.set_num_threads(1)
        model = AttentionDirectionModel(torch.randn(3, 8), torch.zeros(10), torch.ones(10), torch.zeros(6), torch.ones(6),
                                        news_time_width=8, recency_half_life=1.)
        for p in model.parameters():
            p.data.zero_()
        data = {'news_ids': torch.ones((1, 10, 1), dtype=torch.long), 'news_time': torch.zeros((1, 10, 1, 8)),
                'fact_ids': torch.zeros((1, 1), dtype=torch.long), 'day_market': torch.zeros((1, 10, 6)),
                'day_time': torch.zeros((1, 10, 4)), 'market': torch.zeros((1, 10))}
        data['news_time'][0, :, 0, 7] = torch.arange(9, -1, -1)
        data['day_time'][0, :, 0] = torch.arange(9, -1, -1) / 9
        model.eval()
        out, weights = model(**data, explain=True)
        self.assertTrue(torch.isfinite(out).all())
        self.assertAlmostEqual(float(weights['news_decay'][0, -2, 0]), .5)
        self.assertAlmostEqual(float(weights['news_decay'][0, -1, 0]), 1.)
        self.assertAlmostEqual(float(weights['news_effective_weight'][0, -1, 0] /
                                     weights['news_effective_weight'][0, -2, 0]), 2.)
        self.assertAlmostEqual(float(weights['time'][0, -1] / weights['time'][0, -2]), 2., places=5)

    def test_new_model_test_data_cannot_affect_training(self):
        import torch
        from scripts.train_attention_direction import fit_attention
        torch.set_num_threads(1)
        features = {'news_ids': torch.ones((2, 10, 1), dtype=torch.long), 'news_time': torch.zeros((2, 10, 1, 8)),
                    'fact_ids': torch.zeros((2, 1), dtype=torch.long), 'day_market': torch.zeros((2, 10, 6)),
                    'day_time': torch.zeros((2, 10, 4)), 'market': torch.zeros((2, 10))}
        rows = {'train': [{'label': 0}, {'label': 1}], 'test': [{'label': 0}, {'label': 1}]}
        data = {'train': features, 'test': copy.deepcopy(features)}
        changed = copy.deepcopy(data)
        changed['test']['market'].fill_(99999)
        vectors = torch.ones(2, 8)
        options = {'news_time_width': 8, 'recency_half_life': 3.}
        first, _ = fit_attention('company_attention_gru', rows, data, vectors, fixed_epochs=2, model_options=options)
        second, _ = fit_attention('company_attention_gru', rows, changed, vectors, fixed_epochs=2, model_options=options)
        for name, value in first.state_dict().items():
            self.assertTrue(torch.equal(value, second.state_dict()[name]), name)


if __name__ == '__main__':
    unittest.main()
