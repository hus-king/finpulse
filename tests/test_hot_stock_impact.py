import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from backend.auth import AuthStore
from backend.hot_stock_impact import NAMESPACE, VERSION, HotStockImpactService, retrieve_business_cards
from backend.hot_news_board import digest
from backend.hot_stock_memory import EPISODES, PROJECT_EPISODES, project_evidence, seed_project_memory, store_project_episode
from backend.research import ResearchService
from backend.research_store import ResearchStore

TOPIC = {'id': 'topic1', 'title': '国际原油价格大幅上涨', 'summary': '国际原油价格大幅上涨，原油生产者和航空运输企业面临不同的收入和成本变化。',
         'sources': [{'id': 'baidu', 'name': '百度热搜', 'url': 'https://www.baidu.com/s?wd=oil', 'rank': 1, 'heat': '1000', 'observed_at': '2026-10-26T12:00:00+08:00', 'observed_timestamp': 1792987200}]}
STOCKS = [
    {'code': '601857', 'name': '中国石油', 'exchange': 'SH', 'business': '原油勘探、生产和销售'},
    {'code': '601111', 'name': '中国国航', 'exchange': 'SH', 'business': '国内及国际航空旅客运输'},
]
TOPIC['screening'] = {'decision': 'keep', 'event': {'kind': 'disruption', 'actor': '国际',
    'action': '原油价格大幅变化', 'new_fact': TOPIC['title'], 'novelty': 'new_development',
    'importance': 'major_industry', 'attribution': 'reported'}}


def analysis(stock):
    return {'code': stock['code'], 'relation': 'indirect', 'direction': 'positive' if stock['code'] == '601857' else 'negative',
            'score': 6 if stock['code'] == '601857' else -6, 'relationship_evidence': [],
            'confidence': 'high', 'news_evidence': ['国际原油价格大幅上涨'], 'business_evidence': stock['business']['main_business'],
            'mechanism': '生产者收入与使用者成本的可能影响方向不同。', 'uncertainty': '仍需核对净敞口和价格传导。'}


class ImpactTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.directory.name) / 'test.db'); auth.initialize()
        self.store = ResearchStore(auth); self.store.initialize()
        async def complete(messages, **kwargs):
            stocks = json.loads(messages[1]['content'])['stocks']
            return {'content': json.dumps({'items': [analysis(stock) for stock in stocks]}, ensure_ascii=False), 'model': 'fixture'}
        self.completion = AsyncMock(side_effect=complete)
        self.research = ResearchService(self.store, self.completion)
        self.research.industry_news.business = AsyncMock(return_value={})
        self.research.industry_news.relationships = AsyncMock(return_value={})
        self.topic = deepcopy(TOPIC)
        self.board = SimpleNamespace(feed=lambda: {'items': [self.topic]})
        self.service = HotStockImpactService(self.research, self.board, clock=lambda: 1000)
        for stock in STOCKS:
            self.research.catalog.items[stock['code']] = {k: v for k, v in stock.items() if k != 'business'}
            self.store.put('company_business', stock['code'], {'data': {'main_business': stock['business'], 'source': '资料源', 'url': 'https://example.com/business'}, 'status': 'ok', 'fetched_at': 1000})
        self.store.put('watchlist', 'list', [s['code'] for s in STOCKS], 'alice')

    async def asyncTearDown(self):
        await self.research.close(); self.directory.cleanup()

    async def test_read_only_snapshot_and_no_price_inputs(self):
        result = self.service.snapshot('topic1', 'alice')
        self.assertEqual(result['counts']['pending'], 2)
        self.completion.assert_not_awaited(); self.research.industry_news.business.assert_not_awaited()
        material = json.loads(self.service.messages(self.topic, self.service.stocks('alice'))[1]['content'])
        self.assertEqual(set(material), {'news', 'sources', 'stocks', 'sector_hypotheses', 'attention'})
        self.assertNotIn('market', material['stocks'][0]); self.assertNotIn('historical_cases', material)
        self.assertTrue(material['stocks'][0]['business_cards'])

    async def test_project_memory_supplies_historical_paths_to_private_analysis(self):
        seed_project_memory(self.store)
        seed_project_memory(self.store)
        memory = [row['value'] for row in self.store.list(PROJECT_EPISODES)]
        self.assertEqual(len(memory), 2)
        self.assertEqual(len({row['project']['id'] for row in memory}), 1)
        self.assertEqual(len({row['event']['id'] for row in memory}), 2)

        project = '萨拉戈萨磷酸铁锂电池工厂'
        title = project + '建设延期'
        self.topic['title'] = title
        self.topic['summary'] = '原有施工计划向后顺延。'
        self.topic['screening']['event'].update(actor=project, action='建设延期', new_fact=title)
        self.topic['sources'][0]['published_at'] = '2026-10-26'
        self.research.catalog.items['300750'] = {'code': '300750', 'name': '宁德时代', 'exchange': 'SZ'}
        for owner in ('alice', 'bob'):
            self.store.put('watchlist', 'list', ['300750'], owner)

        evidence = project_evidence(self.topic, '300750', memory)
        self.assertEqual(evidence['path'][-1]['to'], '宁德时代')
        self.assertEqual(evidence['path'][-1]['published_at'], '2025-11-26')
        old_topic = {**self.topic, 'sources': [{'published_at': '2025-01-01'}]}
        self.assertEqual(project_evidence(old_topic, '300750', memory)['path'][-1]['published_at'], '2024-12-10')
        same_day = {**self.topic, 'sources': [{'published_at': '2024-12-10'}]}
        self.assertIsNone(project_evidence(same_day, '300750', memory))
        unnamed = {**self.topic, 'title': '萨拉戈萨某电池工厂建设延期'}
        self.assertIsNone(project_evidence(unnamed, '300750', memory))
        ambiguous = {**memory[0], 'project': {**memory[0]['project'], 'id': 'different-project'}}
        self.assertIsNone(project_evidence(self.topic, '300750', [*memory, ambiguous]))

        async def complete(messages, **kwargs):
            payload = json.loads(messages[1]['content'])
            stock = payload['stocks'][0]
            self.assertNotIn('_project_memory', stock)
            self.assertEqual(stock['project_path'], evidence['path'])
            card = evidence['cards'][-1]
            self.assertIn(card, stock['relationships'])
            scored = {**analysis(stock), 'news_evidence': [title],
                      'relationship_evidence': [{'id': card['id'], 'quote': card['text']}]}
            return {'content': json.dumps({'items': [scored]}, ensure_ascii=False), 'model': 'fixture'}

        self.completion.side_effect = complete
        result = await self.service.analyze('topic1', 'alice')
        row = result['items'][0]
        self.assertEqual(row['status'], 'ok', row)
        self.assertEqual(row['analysis']['relation'], 'indirect')
        self.assertEqual(row['analysis']['project_evidence'], evidence['path'])
        self.assertEqual(self.service.snapshot('topic1', 'bob')['items'][0]['status'], 'pending')
        await self.service.analyze('topic1', 'alice')
        self.assertEqual(self.completion.await_count, 1)
        episodes = self.store.list(EPISODES)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]['value']['sources'][0]['observed_at'], '2026-10-26T12:00:00+08:00')

    async def test_project_import_rejects_quotes_missing_from_source(self):
        seed_path = Path(__file__).resolve().parents[1] / 'backend/knowledge/hot_stock_projects.json'
        draft = json.loads(seed_path.read_text(encoding='utf-8'))[0]
        draft['participants'][0]['quote'] = '原文没有的宁德时代引文'
        with self.assertRaises(ValueError):
            store_project_episode(self.store, draft)
        self.assertEqual(self.store.list(PROJECT_EPISODES), [])

    async def test_private_reads_also_reject_unpublished_candidates(self):
        self.topic['screening']['decision'] = 'review'
        with self.assertRaises(HTTPException) as error: self.service.snapshot('topic1', 'alice')
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(self.service.snapshots(['topic1'], 'alice')['missing_ids'], ['topic1'])
        self.completion.assert_not_awaited()

    async def test_provider_body_is_used_without_claiming_independent_verification(self):
        self.topic['_content'] = self.topic['summary'] + '新闻服务额外正文。'
        article = self.service.article(self.topic)
        self.assertIn('额外正文', article['content'])
        self.assertEqual(article['text_source'], 'provider_news')
        self.assertEqual(article['date_status'], 'provider_timestamp')

    async def test_same_event_opposite_business_effects_and_low_confidence_for_summary(self):
        result = await self.service.analyze('topic1', 'alice')
        self.assertEqual([row['analysis']['direction'] for row in result['items']], ['positive', 'negative'])
        self.assertTrue(all(row['analysis']['confidence'] == 'low' for row in result['items']))
        self.assertEqual(result['counts']['related'], 2)

    async def test_business_retrieval_uses_exact_profile_passages(self):
        stock = self.service.stocks('alice')[0]
        cards = retrieve_business_cards(self.topic['title'], self.topic['summary'], stock)
        self.assertTrue(cards)
        for card in cards: self.assertIn(card['quote'], stock['business']['main_business'])
        self.assertEqual(retrieve_business_cards('原油', '', {**stock, 'business': {**stock['business'], 'main_business': ''}}), [])

    async def test_repeat_reuses_completed_results_and_failed_results_are_retriable(self):
        await self.service.analyze('topic1', 'alice'); await self.service.analyze('topic1', 'alice')
        self.assertEqual(self.completion.await_count, 1)

    async def test_owner_isolation(self):
        await self.service.analyze('topic1', 'alice')
        self.store.put('watchlist', 'list', [STOCKS[0]['code']], 'bob')
        self.assertEqual(self.service.snapshot('topic1', 'bob')['counts']['pending'], 1)
        await self.service.analyze('topic1', 'bob')
        self.assertEqual(self.completion.await_count, 2)

    async def test_batch_read_is_private_read_only_and_omits_full_business_profiles(self):
        await self.service.analyze('topic1', 'alice')
        self.store.put('watchlist', 'list', [STOCKS[0]['code']], 'bob')
        alice = self.service.snapshots(['topic1', 'expired'], 'alice')
        bob = self.service.snapshots(['topic1'], 'bob')
        self.assertEqual(alice['missing_ids'], ['expired'])
        self.assertEqual(alice['items'][0]['counts']['related'], 2)
        self.assertNotIn('business', alice['items'][0]['items'][0])
        self.assertEqual(bob['items'][0]['counts']['pending'], 1)
        self.assertEqual(self.completion.await_count, 1)
        self.store.put('watchlist', 'list', [], 'alice')
        self.assertEqual(self.service.snapshots(['topic1'], 'alice')['items'][0]['items'], [])

    async def test_sector_hypotheses_are_supplied_but_do_not_override_stock_judgment(self):
        self.topic['sector_impacts'] = [{'id': 'oil_gas', 'name': '油气开采', 'direction': 'positive',
            'reason': '价格可能影响收入', 'evidence': '国际原油价格大幅上涨', 'method': 'rules'}]
        result = await self.service.analyze('topic1', 'alice')
        self.assertEqual(result['sector_impacts'], self.topic['sector_impacts'])
        self.assertEqual(result['items'][1]['analysis']['direction'], 'negative')
        material = json.loads(self.completion.call_args.args[0][1]['content'])
        self.assertEqual(material['sector_hypotheses'][0]['id'], 'oil_gas')
        self.topic['sector_impacts'][0]['direction'] = 'unclear'
        self.assertEqual(self.service.snapshot('topic1', 'alice')['counts']['pending'], 2)

    async def test_rank_and_heat_changes_reuse_evidence_but_summary_changes_invalidate(self):
        await self.service.analyze('topic1', 'alice')
        self.topic['sources'][0].update(rank=2, heat='2000', observed_at='later')
        self.assertEqual(self.service.snapshot('topic1', 'alice')['counts']['completed'], 2)
        self.topic['summary'] += '新增加的事件信息。'
        self.assertEqual(self.service.snapshot('topic1', 'alice')['counts']['pending'], 2)

    async def test_model_and_profile_changes_invalidate_private_cache(self):
        stock = self.service.stocks('alice')[0]
        with patch('backend.hot_stock_impact.read_config', return_value={'model': 'a'}): first = self.service.key(self.topic, stock)
        with patch('backend.hot_stock_impact.read_config', return_value={'model': 'b'}): self.assertNotEqual(first, self.service.key(self.topic, stock))
        changed = deepcopy(stock); changed['business']['fetched_at'] += 1
        self.assertNotEqual(self.service.key(self.topic, stock), self.service.key(self.topic, changed))

    async def test_failed_validation_is_persisted_and_not_labeled_unrelated(self):
        original = self.completion.side_effect
        self.completion.side_effect = None; self.completion.return_value = {'content': '{invalid'}
        result = await self.service.analyze('topic1', 'alice')
        self.assertEqual(result['counts']['failed'], 2)
        self.assertTrue(all(row['analysis'] is None for row in result['items']))
        self.assertEqual(self.service.snapshot('topic1', 'alice')['counts']['failed'], 2)
        self.completion.side_effect = original
        self.assertEqual((await self.service.analyze('topic1', 'alice'))['counts']['completed'], 2)

    async def test_watchlist_changes_only_return_current_account_stocks(self):
        await self.service.analyze('topic1', 'alice')
        self.store.put('watchlist', 'list', [STOCKS[1]['code']], 'alice')
        result = self.service.snapshot('topic1', 'alice')
        self.assertEqual([row['code'] for row in result['items']], [STOCKS[1]['code']])

    async def test_empty_watchlist_and_expired_topic_reject_before_generation(self):
        self.store.put('watchlist', 'list', [], 'alice')
        with self.assertRaises(HTTPException) as empty: await self.service.analyze('topic1', 'alice')
        self.assertEqual(empty.exception.status_code, 422)
        with self.assertRaises(HTTPException) as unknown: await self.service.analyze('missing', 'alice')
        self.assertEqual(unknown.exception.status_code, 404); self.completion.assert_not_awaited()

    async def test_lease_deduplicates_and_is_released_on_errors(self):
        key = digest([VERSION, 'alice', 'topic1']); token = self.store.claim(key, 600)
        with self.assertRaises(HTTPException) as busy: await self.service.analyze('topic1', 'alice')
        self.assertEqual(busy.exception.status_code, 409); self.completion.assert_not_awaited()
        self.store.release(key, token)
        self.assertEqual((await self.service.analyze('topic1', 'alice'))['counts']['completed'], 2)

    async def test_request_capacity_is_bounded(self):
        await self.service.requests.acquire(); await self.service.requests.acquire()
        try:
            with self.assertRaises(HTTPException) as busy: await self.service.analyze('topic1', 'alice')
            self.assertEqual(busy.exception.status_code, 429)
        finally: self.service.requests.release(); self.service.requests.release()
        self.completion.assert_not_awaited()

    async def test_price_prediction_fields_are_not_accepted_in_impact_output(self):
        async def complete(messages, **kwargs):
            stocks = json.loads(messages[1]['content'])['stocks']
            return {'content': json.dumps({'items': [{**analysis(s), 'predicted_return': .1} for s in stocks]})}
        self.completion.side_effect = complete
        self.assertEqual((await self.service.analyze('topic1', 'alice'))['counts']['failed'], 2)

    async def test_request_capacity_recovers_even_if_lease_cleanup_fails(self):
        with patch.object(self.store, 'release', side_effect=RuntimeError('temporary storage failure')):
            with self.assertRaises(RuntimeError): await self.service.analyze('topic1', 'alice')
        await asyncio.wait_for(self.service.requests.acquire(), timeout=.2)
        await asyncio.wait_for(self.service.requests.acquire(), timeout=.2)
        self.service.requests.release(); self.service.requests.release()


if __name__ == '__main__': unittest.main()
