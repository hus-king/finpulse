import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.auth import AuthStore
from backend.news_cleaning import SHANGHAI
from backend.news_search import NewsSearchService, search_key
from backend.providers import ProviderError
from backend.research import ResearchService
from backend.research_store import ResearchStore
from tests.test_evidence import BASE
from tests.test_research import mock_industry_sources


def row(name, day, **kwargs):
    return {'url': 'https://example.com/' + name, 'title': name, 'published_date': day, **kwargs}


class RollingSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.temp.name) / 'search.db'); self.auth.initialize()
        self.store = ResearchStore(self.auth); self.store.initialize()
        self.now = datetime(2026, 10, 9, 10, tzinfo=SHANGHAI).timestamp()
        self.service = NewsSearchService(self.store, clock=lambda: self.now)
        self.identity = {'source': 'company', 'code': '000159', 'name': '国际实业'}
        self.fetch = AsyncMock(return_value={'results': [row('old', '2026-10-08')]})
        config = patch('backend.providers.read_config', return_value={})
        config.start(); self.addCleanup(config.stop)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def search(self, start='2026-10-02', end='2026-10-09', days=7, identity=None):
        return await self.service.search(identity or self.identity, days, start, end, self.fetch)

    async def test_hour_reuse_is_durable_and_does_not_extend_cursor(self):
        first = await self.search()
        cursor = first['search_cache']['last_search_at']
        self.now += 3599
        self.service = NewsSearchService(self.store, clock=lambda: self.now)
        again = await self.search()
        self.assertEqual(again['results'], first['results'])
        self.assertEqual(again['search_cache']['mode'], 'reused')
        self.assertEqual(again['search_cache']['last_search_at'], cursor)
        self.fetch.assert_awaited_once_with('2026-10-02', '2026-10-09')

    async def test_expiry_fetches_gap_merges_deduplicates_and_filters_exact_times(self):
        await self.search()
        self.now += 3600
        self.fetch.return_value = {'results': [row('old', '2026-10-08'),
            row('before', '2026-10-09T09:59:00+08:00'), row('new', '2026-10-09T10:30:00+08:00'),
            row('boundary', '2026-10-09'), row('future', '2026-10-09T12:00:00+08:00')]}
        result = await self.search()
        self.assertEqual(self.fetch.await_args.args, ('2026-10-09', '2026-10-09'))
        self.assertEqual({r['title'] for r in result['results']}, {'old', 'new', 'boundary'})
        self.assertEqual(result['search_cache']['interval'],
                         ['2026-10-09T10:00:00+08:00', '2026-10-09T11:00:00+08:00'])

    async def test_rolling_window_prunes_old_even_during_midnight_cache_hit(self):
        self.now = datetime(2026, 10, 9, 23, 50, tzinfo=SHANGHAI).timestamp()
        self.fetch.return_value = {'results': [row('expired', '2026-10-02'), row('keep', '2026-10-08')]}
        await self.search()
        self.now += 1200
        result = await self.search(start='2026-10-03', end='2026-10-10')
        self.assertEqual([r['title'] for r in result['results']], ['keep'])
        self.assertEqual(self.fetch.await_count, 1)

    async def test_empty_gap_keeps_previous_material(self):
        await self.search(); self.now += 4000
        self.fetch.return_value = {'results': []}
        self.assertEqual([r['title'] for r in (await self.search())['results']], ['old'])

    async def test_failure_does_not_advance_cursor_and_retries_uncovered_interval(self):
        first = await self.search(); self.now += 4000
        self.fetch.side_effect = ProviderError('offline')
        failed = await self.search()
        self.assertEqual(failed['search_cache']['mode'], 'stale')
        self.assertEqual(failed['search_cache']['last_search_at'], first['search_cache']['last_search_at'])
        self.fetch.side_effect = None
        result = await self.search()
        self.assertEqual(result['search_cache']['interval'][0], first['search_cache']['last_search_at'])
        self.assertEqual(self.fetch.await_count, 3)

    async def test_partial_topics_retry_without_losing_successful_material(self):
        await self.search(); self.now += 4000
        self.fetch.return_value = {'results': [row('partial', '2026-10-09')], 'warnings': ['one topic failed']}
        partial = await self.search()
        self.fetch.return_value = {'results': []}
        result = await self.search()
        self.assertEqual(result['search_cache']['interval'][0], '2026-10-09T10:00:00+08:00')
        self.assertEqual({r['title'] for r in result['results']}, {'old', 'partial'})
        self.assertEqual(partial['search_cache']['last_search_at'], '2026-10-09T10:00:00+08:00')

    async def test_config_days_source_and_stock_are_isolated_but_keys_are_not(self):
        await self.search()
        await self.search(days=30)
        await self.search(identity={**self.identity, 'code': '000001'})
        await self.search(identity={**self.identity, 'source': 'industry'})
        with patch('backend.providers.read_config', return_value={'tavily_api_key': 'unused-private'}):
            self.assertEqual((await self.search())['search_cache']['mode'], 'reused')
        with patch('backend.providers.read_config', return_value={'tavily_base_url': 'https://other.example'}):
            await self.search()
        self.assertEqual(self.fetch.await_count, 5)

    async def test_optional_shortcut_can_be_disabled_without_disabling_incremental_merge(self):
        await self.search(); self.now += 30
        self.service.reuse_enabled = False
        self.fetch.return_value = {'results': [row('new', '2026-10-09')]}
        result = await self.search()
        self.assertEqual(result['search_cache']['mode'], 'incremental')
        self.assertEqual({r['title'] for r in result['results']}, {'old', 'new'})

    async def test_two_service_instances_share_database_lease_and_one_search(self):
        async def slow(*args):
            await asyncio.sleep(.1)
            return {'results': [row('one', '2026-10-09')]}
        self.fetch.side_effect = slow
        other = NewsSearchService(self.store, clock=lambda: self.now)
        results = await asyncio.gather(self.search(),
            other.search(self.identity, 7, '2026-10-02', '2026-10-09', self.fetch))
        self.assertEqual(self.fetch.await_count, 1)
        self.assertEqual(sorted(r['search_cache']['mode'] for r in results), ['full', 'reused'])

    async def test_cursor_is_request_start_not_finish(self):
        request_start = self.now
        async def slow(*args):
            self.now += 30
            return {'results': []}
        self.fetch.side_effect = slow
        await self.search()
        self.assertEqual(self.store.get('news_search', search_key(self.identity, 7))['cursor'], request_start)

    async def test_extract_body_and_known_failure_are_reused_until_hour_expiry(self):
        urls = ['https://example.com/good', 'https://example.com/fail']
        with patch('backend.providers.tavily', new=AsyncMock(return_value={
            'results': [{'url': urls[0], 'raw_content': 'body'}], 'failed_results': [{'url': urls[1]}]})) as api:
            first = await self.service.extract(urls)
            again = await self.service.extract(urls)
            self.assertEqual(first, again)
            self.assertEqual(api.await_count, 1)
            self.now += 3600
            await self.service.extract(urls)
            self.assertEqual(api.await_count, 2)

    async def test_old_body_survives_incremental_budget_changes_but_changed_material_invalidates(self):
        url = 'https://example.com/body'
        materials = {url: row('body', '2026-10-09', content='original snippet')}
        with patch('backend.providers.tavily', new=AsyncMock(return_value={
            'results': [{'url': url, 'raw_content': 'verified full body'}]})) as api:
            await self.service.extract([url], materials)
            self.now += 7200
            self.assertEqual(await self.service.load_extracts(materials), {url: 'verified full body'})
            await self.service.extract([url], materials)
            self.assertEqual(api.await_count, 1)
            materials[url]['content'] = 'new facts'
            self.assertEqual(await self.service.load_extracts(materials), {})
            await self.service.extract([url], materials)
            self.assertEqual(api.await_count, 2)


class RollingResearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        mock_industry_sources(self)
        self.temp = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.temp.name) / 'integration.db'); auth.initialize()
        self.store = ResearchStore(auth); self.store.initialize()
        self.model = AsyncMock(return_value={'content': json.dumps(BASE), 'model': 'test'})
        self.service = ResearchService(self.store, self.model)

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def test_repeated_collection_reuses_all_news_sources_and_scores_but_hides_until_final(self):
        day = datetime.now(SHANGHAI).date().isoformat()
        article = row('one', day, title='贵州茅台公布经营进展', content='贵州茅台公布经营进展，公司介绍海外业务渠道建设计划，实际经营影响需要持续核验公告。')
        snapshots = []
        def progress(stage):
            snapshots.append(self.store.get('dashboard', '600519'))
        with patch('backend.providers.search_news', new=AsyncMock(return_value={'results': [article]})) as company, \
             patch('backend.providers.akshare_news', new=AsyncMock(return_value={'results': []})) as akshare, \
             patch('backend.providers.search_industry_news', new=AsyncMock(return_value={'results': []})) as industry, \
             patch('backend.providers.tavily', new=AsyncMock(return_value={'results': []})) as extract, \
             patch('backend.providers.daily_market', new=AsyncMock(return_value={'candles': [], 'price': None})):
            first = await self.service.collect('600519', 7, 1, False, progress)
            calls = (company.await_count, akshare.await_count, industry.await_count, extract.await_count)
            snapshots.clear()
            again = await self.service.collect('600519', 7, 1, False, progress)
            self.assertEqual(calls, (company.await_count, akshare.await_count, industry.await_count, extract.await_count))
            self.assertEqual(again['news'][0]['score'], first['news'][0]['score'])
            self.assertEqual(self.model.await_count, 1)
            self.assertTrue(all(s['pipeline']['score_display'] == 'updating' for s in snapshots[:-1]))
            self.assertEqual(snapshots[-1]['pipeline']['score_display'], 'ready')
            self.assertEqual(again['pipeline']['counts']['analysis_reused'], 1)
            self.assertTrue(all(s['mode'] == 'reused' for s in again['pipeline']['search_cache']))
