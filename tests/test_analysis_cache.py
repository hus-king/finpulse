import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.analysis_cache import analysis_context, analysis_cache_key, legacy_cache_key
from backend.auth import AuthStore
from backend.news_cleaning import SHANGHAI
from backend.prompts import PROMPT_VERSION
from backend.research import ResearchService
from backend.research_store import ResearchStore
from tests.test_evidence import BASE
from tests.test_research import mock_industry_sources


class AnalysisReuseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        mock_industry_sources(self)
        config = patch('backend.providers.read_config', return_value={'model': 'requested-test'})
        config.start(); self.addCleanup(config.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.temp.name) / 'cache.db'); self.auth.initialize()
        self.store = ResearchStore(self.auth); self.store.initialize()
        self.model = AsyncMock(return_value={'content': json.dumps(BASE), 'model': 'test'})
        self.service = ResearchService(self.store, self.model)
        self.stock = self.service.catalog.get('600519')
        day = datetime.now(SHANGHAI).date().isoformat()
        titles = ['贵州茅台公告年度利润增长', '贵州茅台披露海外渠道建设计划', '贵州茅台发布产品召回风险提示']
        self.rows = [{'title': title, 'content': f'{day}\n{title}。公司披露相关事项，具体安排和经营影响仍需要持续核验后续公告。',
                      'published_date': day, 'url': f'https://example.com/events/{i}'} for i, title in enumerate(titles)]

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def collect(self, rows=None, maximum=3, progress=lambda stage: None):
        # Force upstream fixtures here: this suite isolates judgment persistence,
        # while rolling search reuse/merging has its own integration tests.
        async def fresh(identity, days, start, end, fetcher):
            return await fetcher(start, end)
        with patch.object(self.service.news_search, 'search', side_effect=fresh), \
             patch('backend.providers.search_news', new=AsyncMock(return_value={'results': self.rows if rows is None else rows})), \
             patch('backend.providers.akshare_news', new=AsyncMock(return_value={'results': []})), \
             patch('backend.providers.tavily', new=AsyncMock(return_value={'results': []})), \
             patch('backend.providers.daily_market', new=AsyncMock(return_value={'candles': [], 'price': None})):
            return await self.service.collect('600519', 30, maximum, False, progress)

    async def test_recollect_restores_all_scores_outside_budget_and_spends_budget_on_new_news(self):
        first = await self.collect()
        self.assertEqual(len(first['news']), 3)
        self.assertEqual(self.model.await_count, 3)
        original_times = {row['url']: row['analyzed_at'] for row in first['news']}
        extra = {**self.rows[0], 'url': 'https://example.com/events/new', 'title': '贵州茅台公布重大并购方案',
                 'content': self.rows[0]['published_date'] + '\n贵州茅台公布重大并购方案，公司披露收购资产与交易安排，最终执行结果需核验。'}
        second = await self.collect([extra, *reversed(self.rows)], maximum=1)
        self.assertEqual(second['pipeline']['counts']['analysis_reused'], 3)
        self.assertEqual(second['pipeline']['counts']['analysis_total'], 1)
        self.assertEqual(self.model.await_count, 4)
        self.assertTrue(all(row['score'] == BASE['sentiment_score'] for row in second['news']))
        for row in second['news']:
            if row['url'] in original_times:
                self.assertTrue(row['cached'])
                self.assertEqual(row['analyzed_at'], original_times[row['url']])
        self.model.side_effect = AssertionError('Cached news must not call LLM')
        third = await self.collect([extra, *self.rows], maximum=1)
        self.assertEqual(third['pipeline']['counts']['analysis_reused'], 4)
        self.assertEqual(third['pipeline']['counts']['analysis_total'], 0)
        self.assertEqual(self.model.await_count, 4)

    async def test_results_survive_disappearance_and_service_restart_with_zero_model_budget(self):
        first = await self.collect()
        await self.collect([], maximum=0)
        self.assertEqual(self.store.get('dashboard', '600519')['news'], [])
        await self.service.close()
        self.model = AsyncMock(side_effect=AssertionError('No new model call'))
        self.store = ResearchStore(AuthStore(Path(self.temp.name) / 'cache.db'))
        self.service = ResearchService(self.store, self.model)
        result = await self.collect(maximum=0)
        self.assertEqual(result['pipeline']['counts']['analysis_reused'], 3)
        self.assertEqual({row['url']: row['analysis'] for row in result['news']},
                         {row['url']: row['analysis'] for row in first['news']})
        self.model.assert_not_awaited()
        records = self.store.list('analysis')
        self.assertEqual(len(records), 3)
        for record in records:
            self.assertTrue(record['value']['document']['content'])
            self.assertEqual(record['value']['stock_code'], '600519')
            self.assertEqual(record['value']['score'], BASE['sentiment_score'])

    async def test_refresh_timestamp_and_fetch_status_do_not_invalidate_but_business_facts_do(self):
        first = await self.collect()
        old_business = deepcopy(first['business_profile'])
        same_business = {**old_business, 'fetched_at': '2026-10-09T13:00:00+08:00', 'status': 'stale'}
        with patch.object(self.service.industry_news, 'business', new=AsyncMock(return_value=same_business)):
            second = await self.collect(maximum=0)
        self.assertEqual(second['pipeline']['counts']['analysis_reused'], 3)
        changed_business = {**same_business, 'main_business': '完全不同的金融业务'}
        with patch.object(self.service.industry_news, 'business', new=AsyncMock(return_value=changed_business)):
            third = await self.collect(maximum=0)
        self.assertEqual(third['pipeline']['counts']['analysis_reused'], 0)
        self.assertTrue(all(row['score'] is None for row in third['news']))
        self.assertEqual(self.model.await_count, 3)

    async def test_identifiable_legacy_results_migrate_before_profile_refresh(self):
        first = await self.collect()
        with self.auth.transaction() as conn:
            conn.execute("DELETE FROM research_records WHERE namespace='analysis'")
        for item in first['news']:
            context = analysis_context(self.stock, item, first['industry_profile'], first['business_profile'])
            old_key = legacy_cache_key(self.stock, item, context, 'requested-test')
            record = {field: item[field] for field in ('analysis', 'model', 'analyzed_at')}
            record['prompt_version'] = PROMPT_VERSION
            self.store.put('analysis', old_key, record)
            item['analysis_context_key'] = old_key
            # A previous broken collection may already have reset the visible
            # score; the independent legacy record must still be recovered.
            item.update(analysis=None, score=None, analysis_status='pending')
        self.store.put('dashboard', '600519', first)
        refreshed = {**first['business_profile'], 'fetched_at': '2026-10-09T15:00:00+08:00'}
        with patch.object(self.service.industry_news, 'business', new=AsyncMock(return_value=refreshed)):
            second = await self.collect(maximum=0)
        self.assertEqual(second['pipeline']['counts']['analysis_reused'], 3)
        self.assertEqual(self.model.await_count, 3)
        self.assertEqual(sum(row['value'].get('cache_version') == 2 for row in self.store.list('analysis')), 3)

    async def test_changed_evidence_model_prompt_and_stock_never_reuse_incompatible_scores(self):
        first = await self.collect()
        item = first['news'][0]
        profile, business = first['industry_profile'], first['business_profile']
        def restored(row, stock=None, context_profile=None, model='requested-test'):
            return self.service.analysis_cache.restore(stock or self.stock, [deepcopy(row)],
                                                        context_profile or profile, business, model)
        self.assertEqual(restored(item), 1)
        changes = [{'content': item['content'] + '新增重大事实'}, {'time': '2026-10-01'},
                   {'text_source': 'akshare'}, {'date_status': 'body_verified'},
                   {'url': 'https://example.com/different'}, {'news_scope': 'industry'}]
        for change in changes:
            with self.subTest(change=change):
                self.assertEqual(restored({**item, **change}), 0)
        self.assertEqual(restored(item, stock={**self.stock, 'code': '000001'}), 0)
        self.assertEqual(restored(item, model='other-model'), 0)
        self.assertEqual(restored(item, context_profile={**profile, 'version': 'new-version'}), 0)
        with patch('backend.analysis_cache.PROMPT_VERSION', 'new-prompt'):
            self.assertEqual(restored(item), 0)

    async def test_invalid_saved_score_is_not_treated_as_completed_judgment(self):
        first = await self.collect()
        item = first['news'][0]
        context = analysis_context(self.stock, item, first['industry_profile'], first['business_profile'])
        key = analysis_cache_key(self.stock, item, context, 'requested-test')
        record = self.store.get('analysis', key)
        record['analysis']['sentiment_score'] = 0
        self.store.put('analysis', key, record)
        changed = {**item, 'analysis': None, 'score': None, 'analysis_status': 'pending'}
        self.assertEqual(self.service.analysis_cache.restore(self.stock, [changed], first['industry_profile'],
                                                            first['business_profile'], 'requested-test'), 0)

    async def test_progressive_company_snapshot_preserves_scores_while_industry_is_pending(self):
        first = await self.collect()
        gate, visible = asyncio.Event(), asyncio.Event()
        async def delayed_profile(stock):
            await gate.wait()
            return first['industry_profile']
        def progress(stage):
            if stage == '公司新闻已就绪，行业新闻继续检索':
                visible.set()
        with patch.object(self.service.industry_news, 'profile', side_effect=delayed_profile):
            task = asyncio.create_task(self.collect(maximum=0, progress=progress))
            try:
                await asyncio.wait_for(visible.wait(), 5)
                early = self.store.get('dashboard', '600519')
                self.assertTrue(all(row['score'] == BASE['sentiment_score'] and row['cached'] for row in early['news']))
                self.assertFalse(task.done())
                gate.set()
                result = await task
                self.assertEqual(result['pipeline']['counts']['analysis_reused'], 3)
                self.assertEqual(self.model.await_count, 3)
            finally:
                gate.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_failed_model_output_is_not_cached_and_retry_can_succeed(self):
        self.model.side_effect = RuntimeError('offline')
        first = await self.collect(maximum=1)
        self.assertEqual(first['pipeline']['counts']['analyzed'], 0)
        self.assertEqual(self.store.list('analysis'), [])
        self.model.side_effect = None
        second = await self.collect(maximum=1)
        self.assertEqual(second['pipeline']['counts']['analyzed'], 1)
        self.assertEqual(second['pipeline']['counts']['analysis_reused'], 0)

    async def test_batch_database_lookup_respects_namespace_and_owner(self):
        for owner in ('', 'reader'):
            for index in range(105):
                self.store.put('lookup', str(index), {'owner': owner, 'index': index}, owner)
        self.store.put('other', '0', {'different': True})
        keys = [str(index) for index in range(105)] + ['missing', '0']
        found = self.store.get_many('lookup', keys)
        self.assertEqual(len(found), 105)
        self.assertTrue(all(row['owner'] == '' for row in found.values()))
        self.assertEqual(self.store.get_many('lookup', keys, 'reader')['0']['owner'], 'reader')
        self.assertEqual(self.store.get_many('lookup', []), {})
