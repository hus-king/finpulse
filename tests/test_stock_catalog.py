import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.app import app
from backend.auth import AuthStore
from backend.providers import ProviderError
from backend.news_cleaning import clean_report
from backend.research_store import ResearchStore
from backend.stock_catalog import StockCatalog
from tests import test_research as fixtures

REPLY, ORIGIN, news_sample = fixtures.REPLY, fixtures.ORIGIN, fixtures.news_sample


class StockCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.directory.name) / 'test.db')
        self.auth.initialize()
        self.store = ResearchStore(self.auth)
        self.store.initialize()
        self.catalog = StockCatalog(self.store)

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_search_real_listing_names_codes_initials_and_restore_cache(self):
        async def source(kind, *args, **kwargs):
            return {'rows': [{'code': '600036', 'name': '招商银行'}] if kind == 'catalog_sh' else [{'code': '000858', 'name': '五粮液'}] if kind == 'catalog_sz' else [{'code': '688041', 'name': '海光信息'}]}
        with patch('backend.providers.akshare_worker', new=AsyncMock(side_effect=source)) as fetch:
            await asyncio.gather(self.catalog.ensure(), self.catalog.ensure())
            self.assertEqual(fetch.await_count, 3)
            self.assertEqual(self.catalog.search('ZSYH')['items'][0]['code'], '600036')
            self.assertEqual(self.catalog.search('五粮液')['items'][0]['code'], '000858')
            self.assertEqual(self.catalog.search('688041')['items'][0]['name'], '海光信息')
        restored = StockCatalog(self.store)
        self.assertEqual(restored.get('600036')['name'], '招商银行')
        self.assertIsNone(restored.get('600036')['price'])
        self.assertIsNone(restored.get('999999'))

    async def test_failed_board_keeps_cache_and_does_not_invent_stocks(self):
        record = {'items': StockCatalog.normalize([{'code': '600036', 'name': '招商银行'}], 'SH'), 'fetched_at': 0, 'as_of': '2026-09-30', 'source': 'exchange'}
        self.store.put('stock_catalog', 'sh', record)
        catalog = StockCatalog(self.store)
        with patch('backend.providers.akshare_worker', new=AsyncMock(side_effect=ProviderError('Source unavailable'))):
            await catalog.ensure()
        self.assertEqual(catalog.get('600036')['name'], '招商银行')
        self.assertEqual(len(catalog.search()['warnings']), 3)
        self.assertEqual(catalog.search('未知不存在')['items'], [])


class AddStockTests(unittest.TestCase):
    # Reuse setup helpers without running inherited tests a second time.
    setUp = fixtures.ResearchTests.setUp
    tearDown = fixtures.ResearchTests.tearDown
    register = fixtures.ResearchTests.register
    wait_job = fixtures.ResearchTests.wait_job
    def seed(self, codes=None):
        catalog = app.state.research.catalog
        rows = [{'code': code, 'name': '招商银行' if code == '600036' else '测试公司' + code} for code in (codes or ['600036'])]
        record = {'items': StockCatalog.normalize(rows, 'SH'), 'fetched_at': 9999999999, 'as_of': '2026-10-03', 'source': 'test listing'}
        self.store.put('stock_catalog', 'sh', record)
        catalog.boards['sh'] = record
        catalog._index()

    def test_add_new_stock_automatically_collects_and_analysis_uses_new_entity(self):
        self.seed()
        headers = self.register()
        news = news_sample()
        for item in news['results']:
            item.update(title=item['title'].replace('贵州茅台', '招商银行'), content=item['content'].replace('贵州茅台', '招商银行'))
        market = {'status': 'ok', 'price': 40, 'change': 1, 'is_realtime': False, 'candles': [{'date': '2026-09-30', 'open': 39, 'close': 40, 'low': 38, 'high': 41, 'volume': 1000}]}
        with patch('backend.providers.search_news', new=AsyncMock(return_value=news)) as search, patch('backend.providers.akshare_news', new=AsyncMock(return_value={'results': []})), patch('backend.providers.daily_market', new=AsyncMock(return_value=market)), patch('backend.providers.tavily', new=AsyncMock(return_value={'results': []})), patch('backend.app.completion', new=AsyncMock(return_value=REPLY)) as model:
            reply = self.client.post('/api/watchlist/add', headers=headers, json={'code': '600036'})
            self.assertEqual(reply.status_code, 202, reply.text)
            self.assertTrue(reply.json()['added'])
            job = self.wait_job(reply.json()['job']['id'])
            self.assertEqual(job['status'], 'completed')
            snapshot = self.client.get('/api/dashboard/600036').json()
            self.assertEqual(snapshot['stock']['name'], '招商银行')
            self.assertEqual(snapshot['news'][0]['score'], 1)
            self.assertEqual(snapshot['candles'][0]['close'], 40)
            self.assertIn('600036', self.client.get('/api/watchlist').json()['codes'])
            self.assertEqual(self.client.get('/api/briefing/preview').json()['sections'][-1]['name'], '招商银行')
            duplicate = self.client.post('/api/watchlist/add', headers=headers, json={'code': '600036'}).json()
            self.assertFalse(duplicate['added'])
            self.assertIsNone(duplicate['job'])
            self.assertEqual(search.await_count, 1)
            self.assertEqual(model.await_count, 1)

    def test_login_and_csrf_checked_before_catalog_or_model(self):
        with patch.object(app.state.research.catalog, 'ensure', new_callable=AsyncMock) as listing:
            self.assertEqual(self.client.post('/api/watchlist/add', headers=ORIGIN, json={'code': '600036'}).status_code, 401)
            self.register()
            self.assertEqual(self.client.post('/api/watchlist/add', headers=ORIGIN, json={'code': '600036'}).status_code, 403)
            listing.assert_not_awaited()

    def test_quote_and_profile_pages_are_filtered_but_sina_announcements_remain(self):
        urls = ['https://vip.stock.finance.sina.com.cn/quotes_service/view/cn_price_history.php?symbol=sh600036', 'https://www.cnyes.com/astock/quote/600036', 'https://hk.investing.com/equities/merchants-bank', 'https://www.qcc.com/firm/company.html', 'https://vip.stock.finance.sina.com.cn/corp/go.php/vFD_ProfitStatement/stockid/600036/ctrl/part/displaytype/4.phtml', 'https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=600036&id=12616873']
        sample = news_sample()['results'][0]
        rows = [{**sample, 'title': '招商银行发布业务进展公告', 'content': sample['content'].replace('贵州茅台', '招商银行'), 'url': url} for url in urls]
        day = sample['published_date']
        report = clean_report({'date_range': [day, day], 'searches': [{'stock': '招商银行', 'response': {'results': rows}}]}, entities={'招商银行': {'code': '600036', 'aliases': ['招商银行', '600036']}})
        self.assertEqual(report['summary']['retained'], 1)
        self.assertEqual(report['items'][0]['url'], urls[-1])
        self.assertEqual(report['audit'][3]['reason_codes'], ['company_profile'])

    def test_invalid_stock_and_full_watchlist_do_not_launch_work(self):
        headers = self.register()
        self.seed([str(600100 + index) for index in range(21)])
        with patch.object(app.state.research.catalog, 'ensure', new_callable=AsyncMock), patch.object(app.state.research, 'launch') as launch:
            unknown = self.client.post('/api/watchlist/add', headers=headers, json={'code': '999999'})
            self.assertEqual(unknown.status_code, 422)
            owner = self.client.get('/api/auth/me').json()['user']['id']
            codes = [str(600100 + index) for index in range(20)]
            self.store.put('watchlist', 'list', codes, owner)
            full = self.client.post('/api/watchlist/add', headers=headers, json={'code': '600120'})
            self.assertEqual(full.status_code, 422)
            self.assertEqual(self.client.get('/api/watchlist').json()['codes'], codes)
            launch.assert_not_called()

    def test_watchlist_write_failure_cancels_work_before_provider_calls(self):
        self.seed()
        headers = self.register()
        original = self.store.put
        def fail_preference(namespace, *args, **kwargs):
            if namespace == 'watchlist':
                raise RuntimeError('Simulated write failure')
            return original(namespace, *args, **kwargs)
        with patch.object(self.store, 'put', side_effect=fail_preference), patch('backend.providers.search_news', new_callable=AsyncMock) as source:
            with self.assertRaises(RuntimeError):
                self.client.post('/api/watchlist/add', headers=headers, json={'code': '600036'})
            source.assert_not_awaited()
        self.assertNotIn('600036', self.client.get('/api/watchlist').json()['codes'])
        self.assertFalse(app.state.research.active)


if __name__ == '__main__':
    unittest.main()
