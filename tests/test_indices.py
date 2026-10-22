"""Index identity, source routing and isolation from paid company research."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from backend.app import app
from backend.instruments import INDICES, is_index, provider_symbol
from backend.live_quotes import fetch_tencent_quotes, fetch_sina_quotes, get_batch_quotes
from backend.stock_catalog import StockCatalog
from tests import test_research as fixtures


class IndexIdentityTests(unittest.TestCase):
    def test_catalog_preserves_stock_identity_and_searches_indices(self):
        catalog = StockCatalog(None)
        self.assertEqual(len(INDICES), 8)
        self.assertEqual(catalog.get('000001')['name'], '平安银行')
        self.assertEqual(catalog.get('sh000001')['name'], '上证指数')
        self.assertEqual(provider_symbol('000001'), 'sz000001')
        self.assertEqual(provider_symbol('sh000001'), 'sh000001')
        self.assertFalse(is_index('sh600519'))
        for query, expected in [('沪深300', 'sh000300'), ('KC50', 'sh000688'), ('399006', 'sz399006')]:
            self.assertIn(expected, [row['code'] for row in catalog.search(query)['items']])
        self.assertEqual(len(catalog.search(asset_type='index')['items']), 8)
        self.assertTrue(all(not is_index(row['code']) for row in catalog.search(asset_type='stock')['items']))
        catalog._index()
        self.assertEqual(catalog.get('sh000300')['asset_type'], 'index')
        with self.assertRaises(ValueError):
            provider_symbol('sh999999')

    def test_quote_channels_keep_both_000001_instruments_and_ignore_unsolicited_symbols(self):
        for channel in ('tencent', 'sina'):
            if channel == 'tencent':
                def line(symbol, name, price):
                    fields = ['0'] * 40
                    for i, value in {1: name, 2: symbol[-6:], 3: price, 4: '10', 5: '10', 6: '123', 30: '20261022150000', 33: price, 34: '10'}.items():
                        fields[i] = str(value)
                    return f'v_{symbol}="' + '~'.join(fields) + '";'
                payload = ''.join([line('sz000001', '平安银行', 12), line('sh000001', '上证指数', 3800), line('sh600519', '意外返回', 100)])
                fetch = fetch_tencent_quotes
            else:
                def line(symbol, name, price):
                    fields = ['0'] * 32
                    for i, value in {0: name, 1: '10', 2: '10', 3: price, 4: price, 5: '10', 8: '123', 30: '2026-10-22', 31: '15:00:00'}.items():
                        fields[i] = str(value)
                    return f'var hq_str_{symbol}="' + ','.join(fields) + '";\n'
                payload = ''.join([line('sz000001', '平安银行', 12), line('sh000001', '上证指数', 3800), line('sh600519', '意外返回', 100)])
                fetch = fetch_sina_quotes
            response = AsyncMock()
            response.content = payload.encode('gbk')
            response.raise_for_status = lambda: None
            with self.subTest(channel=channel), patch('httpx.AsyncClient.get', new=AsyncMock(return_value=response)):
                quotes = asyncio.run(fetch(['000001', 'sh000001']))
                self.assertEqual(set(quotes), {'000001', 'sh000001'})
                self.assertEqual(quotes['000001']['price'], 12)
                self.assertEqual(quotes['sh000001']['price'], 3800)
                self.assertEqual(quotes['sh000001']['volume_unit'], 'source_units')
                self.assertEqual(quotes['000001']['volume_unit'], 'shares')

    def test_batch_accepts_whitelisted_indices_only(self):
        with patch('backend.live_quotes.fetch_tencent_quotes', new=AsyncMock(return_value={'sh000300': {'price': 4000}})) as fetch:
            asyncio.run(get_batch_quotes(['sh000300', 'sh000300', 'sh999999', '../000001']))
            fetch.assert_awaited_once_with(['sh000300'])


class IndexWatchlistTests(unittest.TestCase):
    setUp = fixtures.ResearchTests.setUp
    tearDown = fixtures.ResearchTests.tearDown
    register = fixtures.ResearchTests.register

    def test_index_add_save_dashboard_remove_without_company_collection(self):
        headers = self.register()
        with patch.object(app.state.research, 'launch', new_callable=AsyncMock) as launch, patch('backend.providers.search_news', new_callable=AsyncMock) as news:
            result = self.client.post('/api/watchlist/add', headers=headers, json={'code': 'sh000001'})
            self.assertEqual(result.status_code, 202, result.text)
            self.assertIsNone(result.json()['job'])
            self.assertIn('sh000001', self.client.get('/api/watchlist').json()['codes'])
            self.assertEqual(self.client.get('/api/dashboard/sh000001').json()['stock']['asset_type'], 'index')
            self.assertEqual(self.client.get('/api/dashboard/000001').json()['stock']['name'], '平安银行')
            self.assertEqual(self.client.post('/api/watchlist', headers=headers, json={'codes': ['sh000001', '000001']}).status_code, 200)
            self.assertEqual(self.client.post('/api/watchlist', headers=headers, json={'codes': ['000001']}).status_code, 200)
            self.assertEqual(self.client.post('/api/watchlist/add', headers=headers, json={'code': 'sh999999'}).status_code, 422)
            launch.assert_not_awaited()
            news.assert_not_awaited()

    def test_manual_index_research_rejected_and_index_search_needs_no_stock_refresh(self):
        headers = self.register()
        with patch.object(app.state.research.catalog, 'ensure', new_callable=AsyncMock) as ensure, patch('backend.providers.search_news', new_callable=AsyncMock) as news:
            result = self.client.get('/api/stocks?q=科创&asset_type=index')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['items'][0]['code'], 'sh000688')
            ensure.assert_not_awaited()
            self.assertEqual(self.client.post('/api/research/sh000001/refresh', headers=headers, json={}).status_code, 422)
            news.assert_not_awaited()


class IndexSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_and_minute_adapters_route_index_and_stock_separately(self):
        from datetime import datetime
        from backend import providers
        from backend.minute_market import MinuteMarketService
        from backend.news_cleaning import SHANGHAI
        catalog = StockCatalog(None)
        raw = {'date': '2026-10-22', 'day': '2026-10-22 15:00:00', 'open': 10, 'close': 11, 'low': 9, 'high': 12, 'volume': 123}
        minute = MinuteMarketService(None, clock=lambda: datetime(2026, 10, 22, 16, tzinfo=SHANGHAI))
        with patch('backend.providers.akshare_worker', new=AsyncMock(return_value={'rows': [raw]})) as worker:
            index = await providers.daily_market(catalog.get('sh000001'), '2026-10-22')
            stock = await providers.daily_market(catalog.get('000001'), '2026-10-22')
            await minute._provider(catalog.get('sh000001'), 1)
            await minute._provider(catalog.get('000001'), 1)
            self.assertEqual([call.args[0] for call in worker.await_args_list], ['index_history', 'history', 'index_minute_1', 'minute_1'])
            self.assertEqual(index['volume_unit'], 'source_units')
            self.assertEqual(stock['volume_unit'], 'shares')
            self.assertEqual(index['candles'][0]['volume'], 123)
        await minute.close()


class IndexIsolationTests(unittest.TestCase):
    setUp = fixtures.ResearchTests.setUp
    tearDown = fixtures.ResearchTests.tearDown
    register = fixtures.ResearchTests.register

    def test_cached_index_bars_cannot_overwrite_same_numbered_stock(self):
        from backend.news_cleaning import SHANGHAI
        from datetime import datetime
        for code, price in [('sh000001', 3800), ('000001', 12)]:
            bars = [{'date': '2026-10-22', 'open': price, 'close': price, 'low': price, 'high': price, 'volume': 123}]
            quote = {'status': 'ok', 'as_of_date': '2026-10-22', 'price': price, 'change': 1, 'volume_unit': 'source_units' if is_index(code) else 'shares'}
            self.store.put('daily_market', code, {'candles': bars, 'quote': quote})
            self.store.put('minute_market', code + ':1', {'candles': [{**bars[0], 'date': '2026-10-22 15:00:00'}], 'fetched_at': datetime.now(SHANGHAI).isoformat()})
        for code, price in [('sh000001', 3800), ('000001', 12)]:
            result = self.client.get('/api/dashboard/' + code).json()
            self.assertEqual(result['candles'][0]['close'], price)
            self.assertEqual(result['stock']['price'], price)
            minute = self.client.get('/api/market/' + code + '/minutes').json()
            self.assertEqual(minute['candles'][0]['close'], price)
            self.assertEqual(minute['volume_unit'], 'source_units' if is_index(code) else 'shares')

    def test_scheduler_digest_and_paper_trade_exclude_indices(self):
        from backend.recommendations import build_digest
        from backend.paper_rules import PaperError
        self.register()
        owner = self.client.get('/api/auth/me').json()['user']['id']
        self.store.put('watchlist', 'list', ['sh000001', '000001'], owner)
        morning = app.state.research.briefing
        self.assertEqual(morning.all_watchlist_codes(), ['000001'])
        owners, targets = morning.targets({'lookback_days': 7})
        self.assertIn(owner, owners)
        self.assertEqual(set(targets), {'000001'})
        self.assertEqual([row['code'] for row in build_digest(self.store, owner)['sections']], ['000001'])
        with self.assertRaises(PaperError) as rejected:
            app.state.paper_trading._stock('sh000001')
        self.assertEqual(rejected.exception.status, 422)

    def test_batch_research_skips_index_but_runs_requested_equity(self):
        headers = self.register()
        with patch.object(app.state.research, 'launch', new=AsyncMock(return_value={'id': 'test'})) as launch:
            response = self.client.post('/api/watchlist/analyze-all', headers=headers, json={'codes': ['sh000001', '000001']})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(response.json()['count'], 1)
            self.assertEqual(launch.await_args.args[0], '000001')
