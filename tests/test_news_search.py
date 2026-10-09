import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta
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

    async def test_thirty_day_cache_serves_seven_days_without_search_or_clock_reset(self):
        self.fetch.return_value = {'results': [row('outside', '2026-09-20'),
            row('boundary', '2026-10-02'), row('recent', '2026-10-08', raw_content='full body')]}
        broad = await self.search(start='2026-09-09', days=30)
        original = self.store.get('news_search', search_key(self.identity, 30))
        self.now += 300
        narrow = await self.search()
        self.assertEqual([r['title'] for r in narrow['results']], ['boundary', 'recent'])
        self.assertEqual(narrow['results'][-1]['raw_content'], 'full body')
        self.assertEqual(narrow['search_cache']['mode'], 'reused')
        self.assertEqual(narrow['search_cache']['reused_from_days'], 30)
        self.assertEqual(narrow['search_cache']['last_search_at'], broad['search_cache']['last_search_at'])
        self.assertEqual(self.store.get('news_search', search_key(self.identity, 30)), original)
        self.service = NewsSearchService(self.store, clock=lambda: self.now)
        self.assertEqual((await self.search())['results'], narrow['results'])
        self.assertEqual(self.fetch.await_count, 1)

    async def test_ninety_to_thirty_to_seven_does_not_extend_original_hour(self):
        await self.search(start='2026-07-11', days=90)
        self.now += 1200
        await self.search(start='2026-09-09', days=30)
        self.now += 1200
        await self.search()
        self.assertEqual(self.fetch.await_count, 1)
        self.now += 1200
        result = await self.search()
        self.assertEqual(self.fetch.await_count, 2)
        self.assertEqual(result['search_cache']['mode'], 'incremental')
        self.assertEqual(result['search_cache']['interval'][0], '2026-10-09T10:00:00+08:00')

    async def test_stale_wider_cache_is_a_base_for_incremental_search(self):
        self.fetch.return_value = {'results': [row('outside', '2026-09-20'), row('old', '2026-10-08')]}
        await self.search(start='2026-09-09', days=30)
        self.now += 4000
        self.fetch.return_value = {'results': [row('new', '2026-10-09T10:30:00+08:00')]}
        result = await self.search()
        self.assertEqual(self.fetch.await_args.args, ('2026-10-09', '2026-10-09'))
        self.assertEqual(result['search_cache']['mode'], 'incremental')
        self.assertEqual(result['search_cache']['interval'][0], '2026-10-09T10:00:00+08:00')
        self.assertEqual({r['title'] for r in result['results']}, {'old', 'new'})

    async def test_seven_days_cannot_supply_missing_thirty_day_history(self):
        await self.search()
        result = await self.search(start='2026-09-09', days=30)
        self.assertEqual(result['search_cache']['mode'], 'full')
        self.assertEqual(self.fetch.await_args.args, ('2026-09-09', '2026-10-09'))
        self.assertEqual(self.fetch.await_count, 2)

    async def test_newest_covering_cache_can_replace_an_older_narrow_cache(self):
        self.fetch.return_value = {'results': [row('only-seven', '2026-10-08')]}
        await self.search()
        self.now += 1800
        self.fetch.return_value = {'results': [row('new-thirty', '2026-10-09')]}
        await self.search(start='2026-09-09', days=30)
        self.now += 1801
        result = await self.search()
        self.assertEqual({r['title'] for r in result['results']}, {'only-seven', 'new-thirty'})
        self.assertEqual(result['search_cache']['reused_from_days'], 30)
        self.assertEqual(self.fetch.await_count, 2)

    async def test_failed_or_incomplete_wider_window_does_not_hide_missing_coverage(self):
        self.fetch.return_value = {'results': [row('partial', '2026-10-08')], 'warnings': ['topic failed']}
        await self.search(start='2026-09-09', days=30)
        self.fetch.return_value = {'results': []}
        self.assertEqual((await self.search())['search_cache']['mode'], 'full')
        self.assertEqual(self.fetch.await_count, 2)
        # A mislabeled long cache whose actual start is too late is not covering.
        other = {**self.identity, 'code': '000002'}
        await self.search(start='2026-10-05', days=30, identity=other)
        result = await self.search(identity=other)
        self.assertEqual(result['search_cache']['mode'], 'full')
        self.assertEqual(self.fetch.await_count, 4)

    async def test_cross_window_cache_stays_isolated_by_stock_query_and_endpoint(self):
        await self.search(start='2026-09-09', days=30)
        for identity in ({**self.identity, 'code': '000001'},
                         {**self.identity, 'source': 'industry'},
                         {**self.identity, 'query': 'different topic'}):
            self.assertEqual((await self.search(identity=identity))['search_cache']['mode'], 'full')
        with patch('backend.providers.read_config', return_value={'tavily_base_url': 'https://other.example'}):
            self.assertEqual((await self.search())['search_cache']['mode'], 'full')
        self.assertEqual(self.fetch.await_count, 5)

    async def test_cross_midnight_wider_cache_uses_original_cursor_and_current_window(self):
        self.now = datetime(2026, 10, 9, 23, 50, tzinfo=SHANGHAI).timestamp()
        self.fetch.return_value = {'results': [row('expired', '2026-10-02'), row('keep', '2026-10-08')]}
        first = await self.search(start='2026-09-09', days=30)
        self.now += 1200
        result = await self.search(start='2026-10-03', end='2026-10-10')
        self.assertEqual([r['title'] for r in result['results']], ['keep'])
        self.assertEqual(result['search_cache']['window'], ['2026-10-03', '2026-10-10'])
        self.assertEqual(result['search_cache']['last_search_at'], first['search_cache']['last_search_at'])
        self.assertEqual(self.fetch.await_count, 1)

    async def test_arbitrary_fourteen_day_window_can_cover_seven_days(self):
        await self.search(start='2026-09-25', days=14)
        self.assertEqual((await self.search())['search_cache']['reused_from_days'], 14)
        self.assertEqual(self.fetch.await_count, 1)

    async def test_disabling_hour_shortcut_keeps_cross_window_incremental_reuse(self):
        await self.search(start='2026-09-09', days=30)
        self.now += 30
        self.service.reuse_enabled = False
        result = await self.search()
        self.assertEqual(result['search_cache']['mode'], 'incremental')
        self.assertEqual(self.fetch.await_args.args, ('2026-10-09', '2026-10-09'))
        self.assertEqual(result['search_cache']['interval'][0], '2026-10-09T10:00:00+08:00')

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

    async def test_search_supplied_body_is_preserved_without_paid_extraction(self):
        url = 'https://example.com/from-search'
        materials = {url: row('from-search', '2026-10-09', raw_content='full original body')}
        with patch('backend.providers.tavily', new=AsyncMock()) as api:
            await self.service.remember_extracts(materials, {url: 'full original body'})
            self.now += 7200
            self.assertEqual(await self.service.load_extracts(materials), {url: 'full original body'})
            result = await self.service.extract([url], materials, cache_only=True)
            self.assertEqual(result['results'], [{'url': url, 'raw_content': 'full original body'}])
            api.assert_not_awaited()


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

    async def test_thirty_then_seven_reuses_search_extracted_bodies_and_judgments(self):
        now = datetime.now(SHANGHAI)
        day = now.date().isoformat()
        old_day = (now.date() - timedelta(days=20)).isoformat()
        title = '贵州茅台公布经营进展'
        content = title + '，公司介绍海外业务渠道建设计划，实际经营影响需要持续核验公告。'
        article = row('recent', day, title=title, content=content)
        old = row('old', old_day, title='贵州茅台发布产品公告', content='贵州茅台发布产品公告，公司披露相关事项，需要进一步核验实际经营影响。')
        with patch('backend.providers.search_news', new=AsyncMock(return_value={'results': [old, article]})) as company, \
             patch('backend.providers.akshare_news', new=AsyncMock(return_value={'results': []})) as akshare, \
             patch('backend.providers.search_industry_news', new=AsyncMock(return_value={'results': []})) as industry, \
             patch('backend.providers.tavily', new=AsyncMock(return_value={
                 'results': [{'url': article['url'], 'raw_content': day + '\n' + content}]})) as extract, \
             patch('backend.providers.daily_market', new=AsyncMock(return_value={'candles': [], 'price': None})):
            broad = await self.service.collect('600519', 30, 2, False)
            original = next(n for n in broad['news'] if n['url'] == article['url'])
            calls = (company.await_count, akshare.await_count, industry.await_count, extract.await_count, self.model.await_count)
            # Cache hits can finish before profile reads. The progressive
            # company-only phase must not perform new paid extraction either.
            profile = self.service.industry_news.profile
            async def delayed_profile(stock):
                await asyncio.sleep(.15)
                return await profile(stock)
            with patch.object(self.service.industry_news, 'profile', side_effect=delayed_profile):
                narrow = await self.service.collect('600519', 7, 2, False)
            self.assertEqual(len(narrow['news']), 1)
            self.assertEqual(narrow['news'][0]['analysis'], original['analysis'])
            self.assertEqual(narrow['news'][0]['analyzed_at'], original['analyzed_at'])
            self.assertEqual(narrow['news'][0]['text_source'], original['text_source'])
            self.assertEqual(narrow['pipeline']['counts']['analysis_reused'], 1)
            self.assertTrue(all(s['mode'] == 'reused' and s['reused_from_days'] == 30
                                for s in narrow['pipeline']['search_cache']))
            self.assertEqual(calls, (company.await_count, akshare.await_count, industry.await_count,
                                     extract.await_count, self.model.await_count))

    async def test_switching_range_does_not_change_saved_snippet_into_a_different_model_input(self):
        day = datetime.now(SHANGHAI).date().isoformat()
        article = row('stable', day, title='贵州茅台公布经营进展',
                      content='贵州茅台公布经营进展，公司介绍海外业务渠道建设计划，实际经营影响需要持续核验公告。')
        with patch('backend.providers.search_news', new=AsyncMock(return_value={'results': [article]})), \
             patch('backend.providers.akshare_news', new=AsyncMock(return_value={'results': []})), \
             patch('backend.providers.tavily', new=AsyncMock(return_value={'results': []})), \
             patch('backend.providers.daily_market', new=AsyncMock(return_value={'candles': [], 'price': None})):
            broad = await self.service.collect('600519', 30, 1, False)
            original = broad['news'][0]
            self.assertEqual(original['text_source'], 'search_fragments')
            # A body is now available, but there has been no new upstream news
            # search. The narrower cached view must preserve the saved material.
            await self.service.news_search.remember_extracts({article['url']: article},
                {article['url']: day + '\n' + article['content'] + '补充正文描述了渠道变化。'})
            narrow = await self.service.collect('600519', 7, 1, False)
            for field in ('content', 'time', 'text_source', 'date_status', 'analysis', 'analyzed_at'):
                self.assertEqual(narrow['news'][0][field], original[field])
            self.assertEqual(self.model.await_count, 1)
            audit = self.store.get('collection', narrow['pipeline']['collection_id'])
            self.assertTrue(audit['cleaning']['items'][0]['text_stats']['reused_cleaning'])
