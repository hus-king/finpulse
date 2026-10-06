import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend import community
from backend.catalog import stock_by_code
from backend.research import ResearchService

STOCK = stock_by_code('600519')
START, END = '2026-10-01', '2026-10-06'


def row(identifier=123, **values):
    return {'post_id': identifier, 'post_title': '继续持有', 'stockbar_code': '600519', 'post_type': 0, 'post_publish_time': '2026-10-05 10:00:00', 'post_last_time': '2026-10-06 12:00:00', **values}


def listing(rows):
    return '<script>var article_list=' + json.dumps({'bar_code': '600519', 're': rows}) + ';</script>'


def detail(identifier=123, **values):
    data = {'post_id': identifier, 'post_title': '继续持有', 'post_guba': {'stockbar_code': '600519'}, 'post_type': 0, 'post_state': 0, 'post_publish_time': '2026-10-05 10:00:00', 'post_content': '<p>我选择继续持有，等待公司业绩兑现。</p>', 'post_click_count': 100, 'post_comment_count': 2, **values}
    return '<script>var post_article=' + json.dumps(data) + ';</script>'


def posts(count=5):
    return [community.detail_post(detail(index), row(index), '600519', START, END) for index in range(1, count + 1)]


class CommunityParsingTests(unittest.TestCase):
    def test_only_dated_same_board_user_posts_are_candidates(self):
        rows = [row(1), row(2, post_type=1), row(3, stockbar_code='300750'), row(4, post_publish_time='2026-09-01'), row(5, post_publish_time=None), row(1), row('../private')]
        selected, diagnostics = community.candidates(listing(rows), '600519', START, END)
        self.assertEqual([item['post_id'] for item in selected], [1])
        self.assertEqual(diagnostics['filtered']['not_user_post'], 1)
        self.assertEqual(diagnostics['filtered']['wrong_board'], 1)
        self.assertEqual(diagnostics['filtered']['missing_or_outside_date'], 2)
        self.assertEqual(diagnostics['filtered']['invalid_id'], 1)
        self.assertEqual(diagnostics['filtered']['duplicate'], 1)

    def test_detail_has_verified_publication_date_body_and_no_author_identifiers(self):
        post = community.detail_post(detail(post_user={'user_id': 'private-irrelevant'}, post_content='<p>看好长期价值。</p><script>steal()</script>'), row(), '600519', START, END)
        self.assertEqual(post['date'], '2026-10-05')
        self.assertEqual(post['content'], '看好长期价值。')
        self.assertEqual(post['views'], 100)
        self.assertEqual(post['weight'], 1)
        self.assertNotIn('user_id', post)
        self.assertNotIn('steal', post['content'])

    def test_wrong_detail_board_id_date_and_removed_post_are_rejected(self):
        for page in (detail(456), detail(post_guba={'stockbar_code': '300750'}), detail(post_publish_time='2026-09-01'), detail(post_publish_time='2026-10-04'), detail(post_type=1), detail(post_state=1), detail(post_content='')):
            with self.assertRaises(ValueError):
                community.detail_post(page, row(), '600519', START, END)

    def test_no_javascript_evaluation_or_wrong_board_fallback(self):
        with self.assertRaises(ValueError):
            community.embedded_json('var post_article = stealCredentials();', 'post_article')
        with self.assertRaises(ValueError):
            community.candidates(listing([row()]), '300750', START, END)


class CommunityCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_collection_is_bounded_and_does_not_use_tavily(self):
        requested = []
        active, peak = 0, 0
        async def handler(request):
            nonlocal active, peak
            requested.append(request)
            if request.url.path.startswith('/list,'):
                return httpx.Response(200, text=listing([row(index) for index in range(1, 25)]))
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.01)
            active -= 1
            identifier = int(request.url.path.rsplit(',', 1)[1].split('.')[0])
            return httpx.Response(200, text=detail(identifier))
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        with patch('backend.community.httpx.AsyncClient', return_value=client), patch('backend.providers.tavily', new_callable=AsyncMock) as tavily:
            result = await community.collect_posts(STOCK, START, END)
        self.assertEqual(len(result['posts']), 12)
        self.assertEqual(len(requested), 13)
        self.assertLessEqual(peak, 3)
        self.assertTrue(all(request.method == 'GET' and request.url.host == 'guba.eastmoney.com' for request in requested))
        self.assertTrue(all('authorization' not in request.headers for request in requested))
        tavily.assert_not_awaited()

    async def test_insufficient_direct_samples_use_fallback_without_duplicate_posts(self):
        sample = posts(1)
        with patch('backend.community.direct_posts', new=AsyncMock(return_value=(sample, {'retained': 1}))), patch('backend.community.search_posts', new=AsyncMock(return_value=(sample + posts(5)[1:], {'retained': 5}))):
            result = await community.collect_posts(STOCK, START, END)
        self.assertEqual(len(result['posts']), 5)
        self.assertEqual(len({post['url'] for post in result['posts']}), 5)
        self.assertEqual(result['source'], 'mixed')

    async def test_missing_dates_and_list_pages_never_become_opinion_samples(self):
        result = {'results': [{'url': 'https://guba.eastmoney.com/list,600519.html', 'title': '贵州茅台', 'published_date': '2026-10-05', 'content': '贵州茅台讨论'}, {'url': 'https://guba.eastmoney.com/news,600519,123.html', 'title': '贵州茅台', 'content': '贵州茅台讨论'}]}
        with patch('backend.providers.tavily', new=AsyncMock(return_value=result)):
            selected, diagnostics = await community.search_posts(STOCK, START, END)
        self.assertEqual(selected, [])
        self.assertEqual(diagnostics['filtered']['not_individual_post'], 1)
        self.assertEqual(diagnostics['filtered']['missing_or_outside_date'], 1)

    async def test_small_samples_and_source_failures_never_generate_ratios(self):
        model = AsyncMock()
        service = ResearchService(None, model)
        result = {'posts': posts(2), 'source': 'eastmoney_direct', 'diagnostics': {'direct': {'retained': 2}, 'tavily': {'retained': 0}}, 'warnings': []}
        with patch('backend.research.collect_posts', new=AsyncMock(return_value=result)):
            small = await service.community(STOCK, START, END)
        self.assertEqual(small['status'], 'insufficient')
        self.assertIsNone(small['bull'])
        self.assertEqual(len(small['posts']), 2)
        model.assert_not_awaited()
        result.update(posts=[], diagnostics={'direct': {'status': 'error'}, 'tavily': {'status': 'error'}})
        with patch('backend.research.collect_posts', new=AsyncMock(return_value=result)):
            failed = await service.community(STOCK, START, END)
        self.assertEqual(failed['status'], 'error')
        self.assertIsNone(failed['bull'])

    async def test_duplicate_model_ids_fail_without_fabricating_percentages(self):
        sample = posts()
        model = AsyncMock(return_value={'content': json.dumps({'items': [{'id': sample[0]['id'], 'stance': 'bull'}] * 5, 'keywords': []})})
        service = ResearchService(None, model)
        with patch('backend.research.collect_posts', new=AsyncMock(return_value={'posts': sample, 'source': 'eastmoney_direct', 'diagnostics': {}, 'warnings': []})):
            result = await service.community(STOCK, START, END)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(len(result['posts']), 5)
        self.assertIsNone(result['bull'])
