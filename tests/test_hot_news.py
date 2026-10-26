import asyncio
import json
import asyncio
import threading
import time
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import providers
from backend.app import app
from backend.auth import AuthStore
from backend.hot_news import (FEED_KEY, HotNewsService, clean_candidates, digest,
                              validate_evidence)
from backend.news_cleaning import SHANGHAI
from backend.research import ResearchService
from backend.research_store import ResearchStore

NOW = datetime(2026, 10, 26, 12, tzinfo=SHANGHAI)
BODY = '2026-10-26 09:00\n国际原油价格上涨，原油生产企业销售价格可能提高，航空运输企业面临燃油成本上升压力。'
RAW = {'url': 'https://example.com/news/oil', 'title': '国际原油价格上涨引发产业链关注',
       'published_date': '2026-10-26', 'raw_content': BODY}
ARTICLE = clean_candidates([RAW], NOW)[0]
STOCKS = [
    {'code': '601857', 'name': '中国石油', 'exchange': 'SH', 'business': {
        'main_business': '原油勘探、生产和销售', 'status': 'ok'}},
    {'code': '601111', 'name': '中国国航', 'exchange': 'SH', 'business': {
        'main_business': '国内及国际航空旅客运输', 'status': 'ok'}},
]


def impact(code='601857', direction='positive'):
    return {'code': code, 'relation': 'indirect', 'direction': direction, 'confidence': 'medium',
            'news_evidence': ['原油生产企业销售价格可能提高' if code == '601857' else '航空运输企业面临燃油成本上升压力'],
            'business_evidence': '原油勘探、生产和销售' if code == '601857' else '国内及国际航空旅客运输',
            'mechanism': '按企业在产业链中的业务角色判断价格对收入或成本的可能影响。',
            'uncertainty': '未取得净敞口及价格传导数据，仍需核对实际利润影响。'}


class CandidateTests(unittest.TestCase):
    def test_keeps_traceable_recent_news_without_company_name(self):
        result = clean_candidates([RAW], NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['date_status'], 'body_verified')
        self.assertEqual(result[0]['text_source'], 'fulltext')

    def test_rejects_bad_url_missing_text_date_conflict_old_future_and_missing_date(self):
        for overrides in ({'url': 'javascript:alert(1)'}, {'url': 'https://u:p@example.com/a'},
                          {'raw_content': '短文本'}, {'published_date': '2026-10-25'},
                          {'raw_content': BODY.replace('2026-10-26', '2026-10-20'), 'published_date': '2026-10-20'},
                          {'raw_content': BODY.replace('2026-10-26', '2026-10-27'), 'published_date': '2026-10-27'},
                          {'raw_content': BODY.split('\n')[1], 'published_date': None},
                          {'raw_content': {'invalid': True}, 'published_date': 123}):
            with self.subTest(overrides=overrides):
                self.assertEqual(clean_candidates([{**RAW, **overrides}], NOW), [])

    def test_snippets_are_explicit_and_exact_duplicates_merged(self):
        snippet = {**RAW, 'raw_content': None, 'content': BODY.split('\n')[1]}
        self.assertEqual(clean_candidates([snippet], NOW)[0]['text_source'], 'snippet')
        self.assertEqual(len(clean_candidates([RAW, {**RAW, 'url': RAW['url'] + '?utm_source=x'},
                                                {**RAW, 'url': 'https://another.example.com/news'}], NOW)), 1)

    def test_material_revision_changes_identity(self):
        revised = clean_candidates([{**RAW, 'raw_content': BODY + '相关部门继续观察价格变化。'}], NOW)[0]
        self.assertNotEqual(revised['id'], ARTICLE['id'])

    def test_article_limit(self):
        rows = [{**RAW, 'title': f'第{i}条国际原油价格变化新闻', 'url': f'https://example.com/news/{i}'} for i in range(30)]
        self.assertEqual(len(clean_candidates(rows, NOW)), 20)


class EvidenceTests(unittest.TestCase):
    def validate(self, rows, article=None, stocks=None):
        return validate_evidence(json.dumps({'items': rows}, ensure_ascii=False), article or ARTICLE, stocks or STOCKS[:1])

    def test_same_event_can_have_opposite_effects_for_business_roles(self):
        result = self.validate([impact(), impact('601111', 'negative')], stocks=STOCKS)
        self.assertEqual([row['direction'] for row in result], ['positive', 'negative'])

    def test_rejects_fabricated_news_and_business_quotes(self):
        for field, value in [('news_evidence', ['原油价格已上涨百分之五十']), ('business_evidence', '航空燃油套期保值')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate([{**impact(), field: value}])

    def test_rejects_missing_duplicate_or_added_stock(self):
        for rows in ([], [impact(), impact()], [{**impact(), 'code': '600519'}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.validate(rows)

    def test_indirect_relation_requires_actual_business_quote(self):
        for business in ('', '公司主营业务'):
            with self.subTest(business=business), self.assertRaises(ValueError):
                self.validate([{**impact(), 'business_evidence': business}])

    def test_direct_relation_requires_company_identity_in_evidence(self):
        with self.assertRaises(ValueError):
            self.validate([{**impact(), 'relation': 'direct', 'business_evidence': ''}])
        article = {**ARTICLE, 'content': '中国石油发布原油生产经营公告，说明近期经营情况。'}
        result = self.validate([{**impact(), 'relation': 'direct', 'business_evidence': '',
                                 'news_evidence': ['中国石油发布原油生产经营公告']}], article=article)
        self.assertEqual(result[0]['relation'], 'direct')

    def test_unrelated_or_uncertain_cannot_have_direction(self):
        for relation in ('none', 'uncertain'):
            row = {**impact(), 'relation': relation, 'news_evidence': [], 'business_evidence': ''}
            with self.subTest(relation=relation), self.assertRaises(ValueError):
                self.validate([row])
            self.assertEqual(self.validate([{**row, 'direction': 'unclear'}])[0]['confidence'], 'low')

    def test_mixed_and_unclear_are_allowed_for_established_relation(self):
        for direction in ('mixed', 'unclear'):
            self.assertEqual(self.validate([{**impact(), 'direction': direction}])[0]['direction'], direction)

    def test_weak_material_or_stale_business_downgrades_confidence(self):
        for overrides in ({'text_source': 'snippet'}, {'date_status': 'metadata_only'}, {'fragmented': True}):
            self.assertEqual(self.validate([{**impact(), 'confidence': 'high'}], article={**ARTICLE, **overrides})[0]['confidence'], 'low')
        stocks = deepcopy(STOCKS[:1]); stocks[0]['business']['status'] = 'stale'
        self.assertEqual(self.validate([impact()], stocks=stocks)[0]['confidence'], 'low')


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.directory.name) / 'test.db'); auth.initialize()
        self.store = ResearchStore(auth); self.store.initialize()
        self.completion = AsyncMock(return_value={'content': json.dumps({'items': [impact()]}), 'model': 'fixture-model'})
        self.research = ResearchService(self.store, self.completion)
        self.service = HotNewsService(self.research, clock=lambda: NOW.timestamp())
        for stock in STOCKS:
            self.research.catalog.items[stock['code']] = {**stock, 'industry': ''}
            self.store.put('company_business', stock['code'], {'data': {**stock['business'], 'source': 'fixture',
                           'url': 'https://example.com/business'}, 'status': 'ok', 'fetched_at': NOW.timestamp()})
        self.store.put('watchlist', 'list', ['601857'], 'alice')
        self.store.put('hot_news_feed', FEED_KEY, {'items': [ARTICLE], 'fetched_at': NOW.timestamp(), 'collected_at': NOW.isoformat()})
        self.research.industry_news.business = AsyncMock(return_value={})

    async def asyncTearDown(self):
        await self.research.close()
        self.directory.cleanup()

    async def test_get_is_read_only_and_does_not_call_providers(self):
        with patch('backend.hot_news.providers.tavily', new=AsyncMock()) as search:
            self.service.feed()
            self.service.snapshot(ARTICLE, self.service.stocks('alice'), 'alice')
        search.assert_not_awaited(); self.completion.assert_not_awaited()
        self.research.industry_news.business.assert_not_awaited()

    async def test_account_results_are_isolated_and_repeat_analysis_reuses_cache(self):
        await self.service.analyze(ARTICLE['id'], 'alice')
        self.assertEqual((await self.service.analyze(ARTICLE['id'], 'alice'))['items'][0]['status'], 'ok')
        self.assertEqual(self.completion.await_count, 1)
        self.store.put('watchlist', 'list', ['601857'], 'bob')
        snapshot = self.service.snapshot(ARTICLE, self.service.stocks('bob'), 'bob')
        self.assertEqual(snapshot['items'][0]['status'], 'pending')
        await self.service.analyze(ARTICLE['id'], 'bob')
        self.assertEqual(self.completion.await_count, 2)

    async def test_changed_business_model_and_news_invalidate_cache(self):
        stock = self.service.stocks('alice')[0]
        with patch('backend.hot_news.providers.read_config', return_value={'model': 'm1'}):
            original = self.service.key(ARTICLE, stock)
            changed = deepcopy(stock); changed['business']['main_business'] += '与天然气销售'
            self.assertNotEqual(original, self.service.key(ARTICLE, changed))
            self.assertNotEqual(original, self.service.key({**ARTICLE, 'content': BODY + '补充报道'}, stock))
        with patch('backend.hot_news.providers.read_config', return_value={'model': 'm2'}):
            self.assertNotEqual(original, self.service.key(ARTICLE, stock))

    async def test_model_validation_failure_is_failed_and_retriable(self):
        self.completion.return_value = {'content': '{invalid'}
        result = await self.service.analyze(ARTICLE['id'], 'alice')
        self.assertEqual(result['items'][0]['status'], 'failed')
        self.assertIsNone(result['items'][0]['analysis'])
        self.completion.return_value = {'content': json.dumps({'items': [impact()]})}
        self.assertEqual((await self.service.analyze(ARTICLE['id'], 'alice'))['items'][0]['status'], 'ok')

    async def test_unknown_news_and_empty_watchlist_rejected_before_model_call(self):
        with self.assertRaises(HTTPException) as unknown:
            await self.service.analyze('unknown', 'alice')
        self.assertEqual(unknown.exception.status_code, 404)
        self.store.put('watchlist', 'list', [], 'alice')
        with self.assertRaises(HTTPException) as empty:
            await self.service.analyze(ARTICLE['id'], 'alice')
        self.assertEqual(empty.exception.status_code, 422)
        self.completion.assert_not_awaited()

    async def test_analysis_lease_prevents_duplicate_calls(self):
        self.store.claim(digest(['hot-watchlist-v1', 'alice', ARTICLE['id']]), 600)
        # Store leases use wall time; claim remains valid during this test.
        with self.assertRaises(HTTPException) as busy:
            await self.service.analyze(ARTICLE['id'], 'alice')
        self.assertEqual(busy.exception.status_code, 409)
        self.completion.assert_not_awaited()

    async def test_refresh_reuses_fresh_feed_and_retains_stale_on_provider_failure(self):
        with patch('backend.hot_news.providers.tavily', new=AsyncMock()) as search:
            await self.service.refresh()
            search.assert_not_awaited()
        self.service.clock = lambda: NOW.timestamp() + 901
        with patch('backend.hot_news.providers.tavily', new=AsyncMock(side_effect=providers.ProviderError('fixture-failure'))):
            result = await self.service.refresh()
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['items'][0]['id'], ARTICLE['id'])
        self.assertEqual(result['fetched_at'], NOW.isoformat())

    async def test_old_news_disappears_from_current_window(self):
        self.service.clock = lambda: (NOW + timedelta(days=3)).timestamp()
        self.assertEqual(self.service.feed()['items'], [])

    async def test_refresh_fetches_and_normalizes_once_after_ttl(self):
        self.service.clock = lambda: NOW.timestamp() + 901
        with patch('backend.hot_news.providers.tavily', new=AsyncMock(return_value={'results': [RAW]})) as search:
            result = await self.service.refresh()
            await self.service.refresh()
        self.assertEqual(search.await_count, 1)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['items']), 1)

    async def test_batch_partial_failure_does_not_discard_other_results(self):
        codes = ['601857', '601111', '600519', '300750', '688981', '000001']
        self.store.put('watchlist', 'list', codes, 'alice')

        async def complete(messages, **kwargs):
            group = json.loads(messages[1]['content'])['stocks']
            if len(group) == 1:
                return {'content': '{invalid'}
            rows = [{**impact(stock['code']), 'relation': 'uncertain', 'direction': 'unclear',
                     'news_evidence': [], 'business_evidence': ''} for stock in group]
            return {'content': json.dumps({'items': rows})}

        self.completion.side_effect = complete
        result = await self.service.analyze(ARTICLE['id'], 'alice')
        self.assertEqual(len(result['items']), 6)
        self.assertEqual(sum(row['status'] == 'ok' for row in result['items']), 5)
        self.assertEqual(sum(row['status'] == 'failed' for row in result['items']), 1)
        self.assertEqual(self.completion.await_count, 2)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        app.state.auth_store = AuthStore(Path(self.directory.name) / 'auth.db')
        self.context = TestClient(app, base_url='http://localhost')
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        del app.state.auth_store
        self.directory.cleanup()

    def login(self):
        resp = self.client.post('/api/auth/register', headers={'Origin': 'http://localhost'},
            json={'username': 'hotnewsuser', 'nickname': 'Reader', 'password': 'Password123!'})
        self.assertEqual(resp.status_code, 201)
        return resp.json()

    def test_refresh_and_future_analysis_require_login(self):
        for method, path in [('post', '/refresh'), ('get', '/x/analysis'), ('post', '/x/analyze'), ('get', '/analysis-batch?ids=x')]:
            self.assertEqual(getattr(self.client, method)('/api/hot-news' + path).status_code, 401)

    def test_mutations_require_csrf_and_same_origin(self):
        session = self.login()
        for path in ('/refresh', '/x/analyze'):
            self.assertEqual(self.client.post('/api/hot-news' + path, json={}).status_code, 403)
            response = self.client.post('/api/hot-news' + path, json={}, headers={
                'Origin': 'https://untrusted.example', 'X-CSRF-Token': session['csrf_token']})
            self.assertEqual(response.status_code, 403)

    def test_get_feed_never_searches_and_is_not_cacheable(self):
        self.login()
        with patch('backend.hot_news.providers.tavily', new=AsyncMock()) as search:
            response = self.client.get('/api/hot-news')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        search.assert_not_awaited()

    def test_stock_impact_uses_authenticated_owner_instead_of_payload(self):
        session = self.login()
        with patch.object(app.state.hot_stock_impact, 'analyze', new=AsyncMock(return_value={'items': []})) as analyze:
            response = self.client.post('/api/hot-news/example/analyze', json={'owner': 'someone-else'},
                headers={'Origin': 'http://localhost', 'X-CSRF-Token': session['csrf_token']})
        self.assertEqual(response.status_code, 200)
        analyze.assert_awaited_once_with('example', session['user']['id'])

    def test_batch_read_uses_authenticated_owner_and_validates_size_and_ids(self):
        session = self.login()
        with patch.object(app.state.hot_stock_impact, 'snapshots', return_value={'items': [], 'missing_ids': []}) as read:
            response = self.client.get('/api/hot-news/analysis-batch', params={'ids': 'a,b,a', 'owner': 'someone-else'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            read.assert_called_once_with(['a', 'b'], session['user']['id'])
            for ids in ('', '../x', '中', 'x' * 65, ','.join(str(i) for i in range(101))):
                with self.subTest(ids=ids):
                    self.assertEqual(self.client.get('/api/hot-news/analysis-batch', params={'ids': ids}).status_code, 422)
            self.assertEqual(read.call_count, 1)

    def test_refresh_accepts_slow_job_and_polling_only_publishes_kept_news(self):
        from tests.test_hot_news_board import board_row
        session = self.login(); gate = threading.Event()
        async def collect(source):
            while not gate.is_set(): await asyncio.sleep(.01)
            return [board_row(), board_row('超长预售45天原来是在等退货回来', 2)]
        headers = {'Origin': 'http://localhost', 'X-CSRF-Token': session['csrf_token']}
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock(side_effect=collect)), patch('backend.hot_news_board.read_config', return_value={}):
            started = time.monotonic()
            try:
                response = self.client.post('/api/hot-news/refresh', json={}, headers=headers)
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json()['refresh']['status'], 'running')
                self.assertEqual(self.client.post('/api/hot-news/refresh', json={}, headers=headers).status_code, 202)
            finally: gate.set()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                result = self.client.get('/api/hot-news?decision=exclude').json()
                if result['refresh']['status'] == 'completed': break
                time.sleep(.02)
        self.assertEqual(result['refresh']['status'], 'completed')
        self.assertEqual([r['title'] for r in result['items']], ['美国宣布加征进口关税'])
        self.assertEqual(result['counts'], {'keep': 1})

    def test_rate_limit_blocks_before_paid_work(self):
        session = self.login()
        with patch.object(app.state.hot_news, 'request_refresh', new=AsyncMock(return_value={'items': [], 'counts': {}, 'sources': []})) as refresh:
            for index in range(9):
                response = self.client.post('/api/hot-news/refresh', json={},
                    headers={'Origin': 'http://localhost', 'X-CSRF-Token': session['csrf_token']})
                self.assertEqual(response.status_code, 200 if index < 8 else 429)
        self.assertEqual(refresh.await_count, 8)


if __name__ == '__main__':
    unittest.main()
