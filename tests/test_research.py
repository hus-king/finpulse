import asyncio
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app import app
from backend.auth import AuthStore
from backend.briefing import build_briefing, deliver, start_scheduler
from backend.catalog import stock_by_code
from backend.news_cleaning import SHANGHAI
from backend.prompts import Analysis, parse_json
from backend.providers import ProviderError
from backend.research import ResearchService, forward_returns
from backend.research_store import ResearchStore

ORIGIN = {'Origin': 'http://localhost'}
ANALYSIS = {'sentiment_score': 1, 'summary': '公司披露经营进展', 'causal_chain': ['公司披露新项目', '可能增加业务收入', '实际市场反应有待观察'], 'uncertainty': '仅基于来源材料；不构成价格预测'}
REPLY = {'content': json.dumps(ANALYSIS, ensure_ascii=False), 'model': 'test', 'elapsed_ms': 1, 'usage': {}, 'request_id': 'test', 'source': 'test'}


def news_sample():
    day = datetime.now(SHANGHAI).date().isoformat()
    return {'results': [{'title': '贵州茅台披露海外市场业务进展', 'content': f'{day}\n贵州茅台介绍海外市场业务进展，相关计划仍需持续观察实际执行情况和最终收入贡献，不应据此判断未来股价。', 'published_date': day, 'url': 'https://example.com/news/1'}]}


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.directory.name) / 'test.db')
        app.state.auth_store = self.auth
        self.context = TestClient(app, base_url='http://localhost')
        self.client = self.context.__enter__()
        self.store = app.state.research.store

    def tearDown(self):
        self.context.__exit__(None, None, None)
        del app.state.auth_store
        self.directory.cleanup()

    def register(self, name='researcher'):
        response = self.client.post('/api/auth/register', headers=ORIGIN, json={'username': name, 'nickname': name, 'password': 'Research-test-2026!'})
        self.assertEqual(response.status_code, 201)
        return {**ORIGIN, 'X-CSRF-Token': response.json()['csrf_token']}

    def wait_job(self, job_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            response = self.client.get('/api/research/jobs/' + job_id)
            self.assertEqual(response.status_code, 200)
            job = response.json()
            # The live result becomes terminal before its final DB write and
            # queue cleanup; wait for durable completion before follow-up work.
            if job['status'] not in ('queued', 'running') and job_id not in app.state.research.live_jobs:
                return job
            time.sleep(.02)
        self.fail('Job did not complete')

    def test_blank_dashboard_does_not_invent_prices_or_news(self):
        data = self.client.get('/api/dashboard/600519').json()
        self.assertIsNone(data['stock']['price'])
        self.assertEqual(data['candles'], [])
        self.assertEqual(data['news'], [])
        self.assertIsNone(data['sentiment']['bull'])

    def test_watchlists_are_persistent_and_account_scoped(self):
        first = self.register()
        self.assertEqual(self.client.post('/api/watchlist', headers=first, json={'codes': ['002594']}).status_code, 200)
        self.register('otheruser')
        self.assertNotEqual(self.client.get('/api/watchlist').json()['codes'], ['002594'])
        response = self.client.post('/api/auth/login', headers=ORIGIN, json={'username': 'researcher', 'password': 'Research-test-2026!'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/watchlist').json()['codes'], ['002594'])
        self.assertEqual(self.client.post('/api/watchlist', headers={**ORIGIN, 'X-CSRF-Token': response.json()['csrf_token']}, json={'codes': ['unknown']}).status_code, 422)

    def test_research_requires_login_and_csrf_before_provider_calls(self):
        with patch('backend.providers.search_news', new_callable=AsyncMock) as search:
            self.assertEqual(self.client.post('/api/research/600519/refresh', headers=ORIGIN, json={}).status_code, 401)
            self.register()
            self.assertEqual(self.client.post('/api/research/600519/refresh', headers=ORIGIN, json={}).status_code, 403)
            search.assert_not_awaited()

    def test_live_flow_persists_analysis_audit_and_reuses_cache(self):
        headers = self.register()
        market = {'status': 'ok', 'price': 101, 'change': 1, 'as_of_date': '2026-09-30', 'is_realtime': False, 'source': 'test', 'candles': [{'date': '2026-09-30', 'open': 100, 'close': 101, 'low': 99, 'high': 102, 'volume': 10000}]}
        with patch('backend.providers.search_news', new=AsyncMock(return_value=news_sample())), patch('backend.providers.akshare_news', new=AsyncMock(return_value={'results': []})), patch('backend.providers.daily_market', new=AsyncMock(return_value=market)), patch('backend.providers.tavily', new=AsyncMock(return_value={'results': []})), patch('backend.app.completion', new=AsyncMock(return_value=REPLY)) as model:
            response = self.client.post('/api/research/600519/refresh', headers=headers, json={})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(self.wait_job(response.json()['id'])['status'], 'completed')
            data = self.client.get('/api/dashboard/600519').json()
            self.assertEqual(data['news'][0]['score'], 1)
            self.assertEqual(data['news'][0]['url'], 'https://example.com/news/1')
            self.assertEqual(self.client.get('/api/research/600519/audit').json()['summary']['input'], 1)
            again = self.client.post('/api/research/600519/refresh', headers=headers, json={})
            self.wait_job(again.json()['id'])
            self.assertEqual(model.await_count, 1)
            item = data['news'][0]
            self.assertEqual(self.client.post(f'/api/news/600519/{item["id"]}/analyze', headers=headers, json={}).status_code, 200)
            self.assertEqual(model.await_count, 1)
        self.assertEqual(ResearchStore(self.auth).get('dashboard', '600519')['news'][0]['score'], 1)

    def test_market_news_and_individual_analyses_are_visible_before_job_finishes(self):
        headers = self.register()
        search_gate = threading.Event()
        titles = ['贵州茅台宣布海外市场渠道建设计划', '贵州茅台公告新任董事人事任命', '贵州茅台发布产品质量召回风险提示']
        model_gates = {title: threading.Event() for title in titles}
        day = datetime.now(SHANGHAI).date().isoformat()
        market = {'status': 'ok', 'price': 101, 'change': 1, 'as_of_date': day, 'is_realtime': False, 'candles': [{'date': day, 'open': 100, 'close': 101, 'low': 99, 'high': 102, 'volume': 10000}]}
        sample = {'results': [{'title': title, 'content': f'{day}\n{title}。公司已披露相关事项，具体实施安排及业务影响仍需结合后续公告核验。', 'published_date': day, 'url': f'https://example.com/news/{index}'} for index, title in enumerate(titles)]}
        started = []

        async def slow_search(*args):
            await asyncio.to_thread(search_gate.wait, 10)
            return sample

        async def slow_model(messages, max_tokens):
            title = json.loads(messages[1]['content'])['title']
            started.append(title)
            await asyncio.to_thread(model_gates[title].wait, 10)
            return REPLY

        def wait_for(read, predicate):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                value = read()
                if predicate(value):
                    return value
                time.sleep(.02)
            self.fail('Expected staged result was not visible')

        with patch('backend.providers.search_news', side_effect=slow_search), patch('backend.providers.akshare_news', new=AsyncMock(return_value={'results': []})), patch('backend.providers.daily_market', new=AsyncMock(return_value=market)), patch('backend.providers.tavily', new=AsyncMock(return_value={'results': []})), patch('backend.app.completion', side_effect=slow_model):
            try:
                job = self.client.post('/api/research/600519/refresh', headers=headers, json={}).json()
                read_dashboard = lambda: self.client.get('/api/dashboard/600519').json()
                first = wait_for(read_dashboard, lambda value: len(value['candles']) == 1)
                self.assertEqual(first['pipeline']['stages']['news'], 'pending')
                self.assertEqual(first['news'], [])
                running = self.client.get('/api/research/jobs/' + job['id']).json()
                self.assertEqual(running['status'], 'running')
                self.assertTrue(running['data_revision'])
                other = TestClient(app, base_url='http://localhost')
                try:
                    other.post('/api/auth/register', headers=ORIGIN, json={'username': 'other_live_viewer', 'nickname': 'Other', 'password': 'Research-test-2026!'})
                    self.assertEqual(other.get('/api/research/jobs/' + job['id']).status_code, 404)
                finally:
                    other.close()
                search_gate.set()
                cleaned = wait_for(read_dashboard, lambda value: value['pipeline']['stages']['news'] == 'ready')
                self.assertEqual(len(cleaned['news']), 3)
                self.assertTrue(all(article['score'] is None for article in cleaned['news']))
                self.assertNotEqual(first['revision'], cleaned['revision'])
                self.assertEqual(self.client.get('/api/research/600519/audit').json()['summary']['retained'], 3)
                wait_for(lambda: list(started), lambda value: len(value) == 3)
                # All three requests have started before any model is allowed
                # to respond. One response must appear without the other two.
                model_gates[titles[0]].set()
                partial = wait_for(read_dashboard, lambda value: value['pipeline']['counts'].get('analyzed') == 1)
                self.assertEqual(sum(article['analysis_status'] == 'running' for article in partial['news']), 2)
                self.assertEqual(partial['candles'], market['candles'])
                self.assertEqual(self.client.get('/api/research/jobs/' + job['id']).json()['status'], 'running')
            finally:
                search_gate.set()
                for gate in model_gates.values():
                    gate.set()
            self.assertEqual(self.wait_job(job['id'])['status'], 'completed')
            final = read_dashboard()
            self.assertEqual(final['pipeline']['counts']['analyzed'], 3)
            self.assertTrue(all(article['analysis_status'] == 'completed' for article in final['news']))

    def test_failed_sources_keep_saved_data_with_visible_stale_status(self):
        headers = self.register()
        self.store.put('dashboard', '600519', {'quote': {'price': 100, 'as_of_date': '2026-09-30'}, 'candles': [{'date': '2026-09-30', 'close': 100}], 'news': []})
        with patch('backend.providers.search_news', new=AsyncMock(side_effect=ProviderError('Tavily unavailable'))), patch('backend.providers.akshare_news', new=AsyncMock(side_effect=ProviderError('AkShare unavailable'))), patch('backend.providers.daily_market', new=AsyncMock(side_effect=ProviderError('Market unavailable'))), patch('backend.app.completion', new_callable=AsyncMock) as model:
            response = self.client.post('/api/research/600519/refresh', headers=headers, json={})
            self.assertEqual(self.wait_job(response.json()['id'])['status'], 'partial')
            data = self.client.get('/api/dashboard/600519').json()
            self.assertEqual(data['quote']['status'], 'stale')
            self.assertEqual(data['stock']['price'], 100)
            self.assertTrue(data['pipeline']['warnings'])
            model.assert_not_awaited()

    def test_jobs_are_private_to_the_requesting_account(self):
        headers = self.register()
        with patch.object(app.state.research, 'collect', new=AsyncMock(return_value={'pipeline': {'warnings': [], 'counts': {}}})):
            job = self.client.post('/api/research/600519/refresh', headers=headers, json={}).json()
            self.wait_job(job['id'])
        self.register('otheruser')
        self.assertEqual(self.client.get('/api/research/jobs/' + job['id']).status_code, 404)

    def test_subscription_secret_not_returned_and_preview_never_sends(self):
        headers = self.register()
        with patch('backend.briefing.deliver', new_callable=AsyncMock) as send:
            response = self.client.post('/api/subscription', headers=headers, json={'enabled': True, 'email': 'test@example.com', 'pushplus_token': 'private-push-token'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('private-push-token', response.text)
            self.assertNotIn('private-push-token', self.client.get('/api/subscription').text)
            self.assertEqual(self.client.get('/api/briefing/preview').status_code, 200)
            send.assert_not_awaited()
        self.assertEqual(self.client.post('/api/subscription', headers=headers, json={'enabled': True, 'email': 'bad\nmail'}).status_code, 422)

    def test_empty_news_and_invalid_model_json_never_become_neutral_analyses(self):
        headers = self.register()
        with patch('backend.providers.search_news', new=AsyncMock(return_value=news_sample())), patch('backend.providers.akshare_news', new=AsyncMock(return_value={'results': []})), patch('backend.providers.daily_market', new=AsyncMock(side_effect=ProviderError('Unavailable'))), patch('backend.providers.tavily', new=AsyncMock(return_value={'results': []})), patch('backend.app.completion', new=AsyncMock(return_value={**REPLY, 'content': '{"sentiment_score":3}'})) as model:
            job = self.client.post('/api/research/600519/refresh', headers=headers, json={}).json()
            self.assertEqual(self.wait_job(job['id'])['status'], 'partial')
            article = self.client.get('/api/dashboard/600519').json()['news'][0]
            self.assertIsNone(article['score'])
            self.assertEqual(article['analysis_status'], 'failed')
            self.assertEqual(model.await_count, 2)

    def test_scheduler_is_opt_in(self):
        with patch('backend.briefing.read_config', return_value={}):
            self.assertIsNone(start_scheduler(app.state.research))

    def test_community_only_refresh_is_private_and_preserves_news_and_candles(self):
        self.assertEqual(self.client.post('/api/research/600519/community', headers=ORIGIN, json={}).status_code, 401)
        headers = self.register()
        self.assertEqual(self.client.post('/api/research/600519/community', headers=ORIGIN, json={}).status_code, 403)
        saved = self.client.get('/api/dashboard/600519').json()
        saved['candles'] = [{'date': '2026-09-30', 'close': 100}]
        saved['news'] = [{'id': 'saved-news', 'score': 1, 'analysis': ANALYSIS}]
        self.store.put('dashboard', '600519', saved)
        sample = [{'id': str(index), 'title': '看好公司', 'content': '继续关注', 'url': f'https://guba.eastmoney.com/news,600519,{index + 1}.html', 'date': '2026-10-05'} for index in range(5)]
        classified = {'content': json.dumps({'items': [{'id': str(index), 'stance': 'bull' if index < 3 else 'neutral'} for index in range(5)], 'keywords': ['关注']})}
        with patch('backend.research.collect_posts', new=AsyncMock(return_value={'posts': sample, 'source': 'eastmoney_direct', 'diagnostics': {}, 'warnings': []})), patch('backend.app.completion', new=AsyncMock(return_value=classified)), patch('backend.providers.search_news', new_callable=AsyncMock) as news, patch('backend.providers.daily_market', new_callable=AsyncMock) as market:
            response = self.client.post('/api/research/600519/community', headers=headers, json={})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(self.wait_job(response.json()['id'])['status'], 'completed')
            news.assert_not_awaited()
            market.assert_not_awaited()
        result = self.client.get('/api/dashboard/600519').json()
        self.assertEqual(result['candles'], saved['candles'])
        self.assertEqual(result['news'], saved['news'])
        self.assertEqual(result['sentiment']['sample_count'], 5)
        self.assertEqual(result['sentiment']['bull'], 60)
        self.assertEqual(sum(result['sentiment'][key] for key in ('bull', 'bear', 'neutral')), 100)

    def test_digest_escapes_source_text_and_preserves_original_link(self):
        owner = 'test-owner'
        self.store.put('watchlist', 'list', ['600519'], owner)
        self.store.put('dashboard', '600519', {'news': [{'id': 'n', 'title': '<script>evil</script>', 'url': 'https://example.com/n', 'time': '2026-09-30', 'score': 0}], 'as_of': '2026-09-30'})
        # Keep the fixture inside the lookback window regardless of the run date.
        with patch('backend.recommendations.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 6, 10, tzinfo=SHANGHAI)
            digest = build_briefing(self.store, owner)
        self.assertNotIn('<script>', digest['html'])
        self.assertIn('&lt;script&gt;evil&lt;/script&gt;', digest['html'])
        self.assertIn('https://example.com/n', digest['html'])

    def test_forward_returns_use_next_trading_close_without_lookahead(self):
        candles = [{'date': f'2026-09-{day:02}', 'close': value} for day, value in [(21, 100), (22, 101), (23, 102), (24, 104), (25, 110), (28, 120), (29, 130)]]
        report = forward_returns([{'id': 'n', 'title': 'News', 'time': '2026-09-21', 'score': 1}], candles)
        row = report['items'][0]
        self.assertEqual(row['base_date'], '2026-09-22')
        self.assertEqual(row['return_3d'], round((110 / 101 - 1) * 100, 2))
        self.assertEqual(row['return_5d'], round((130 / 101 - 1) * 100, 2))

    def test_json_schema_rejects_float_and_boolean_scores(self):
        for score in (1.5, True, 3):
            with self.assertRaises(ValueError):
                parse_json(json.dumps({**ANALYSIS, 'sentiment_score': score}), Analysis)


class StagedCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.directory.name) / 'staged.db')
        auth.initialize()
        self.store = ResearchStore(auth)
        self.store.initialize()
        self.service = ResearchService(self.store, AsyncMock(return_value=REPLY))
        self.market = {'status': 'ok', 'price': 101, 'change': 1, 'is_realtime': False, 'candles': [{'date': '2026-09-30', 'open': 100, 'close': 101, 'low': 99, 'high': 102, 'volume': 10000}]}

    async def asyncTearDown(self):
        await self.service.close()
        self.directory.cleanup()

    async def test_slow_market_does_not_block_news_or_drop_earlier_analysis(self):
        gate, analyzed = asyncio.Event(), asyncio.Event()
        async def slow_market(*args):
            await gate.wait()
            return self.market
        def progress(stage):
            if stage == 'AI 新闻研判已完成 1/1':
                analyzed.set()
        with patch('backend.providers.search_news', new=AsyncMock(return_value=news_sample())), patch('backend.providers.akshare_news', new=AsyncMock(return_value={'results': []})), patch('backend.providers.tavily', new=AsyncMock(return_value={'results': []})), patch('backend.providers.daily_market', side_effect=slow_market):
            task = asyncio.create_task(self.service.collect('600519', 30, 3, False, progress))
            try:
                await asyncio.wait_for(analyzed.wait(), 3)
                early = self.store.get('dashboard', '600519')
                self.assertEqual(early['candles'], [])
                self.assertEqual(early['pipeline']['stages']['market'], 'pending')
                self.assertEqual(early['news'][0]['score'], 1)
                self.assertFalse(task.done())
                gate.set()
                final = await asyncio.wait_for(task, 3)
                self.assertEqual(final['candles'], self.market['candles'])
                self.assertEqual(final['news'][0]['score'], 1)
                self.assertNotEqual(early['revision'], final['revision'])
                self.service.completion.assert_awaited_once()
            finally:
                gate.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_cancellation_keeps_visible_results_and_stops_model_workers(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        async def blocked_model(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        self.service.completion = blocked_model
        with patch('backend.providers.search_news', new=AsyncMock(return_value=news_sample())), patch('backend.providers.akshare_news', new=AsyncMock(return_value={'results': []})), patch('backend.providers.tavily', new=AsyncMock(return_value={'results': []})), patch('backend.providers.daily_market', new=AsyncMock(return_value=self.market)):
            task = asyncio.create_task(self.service.collect('600519', 30, 3, False))
            await asyncio.wait_for(started.wait(), 3)
            # Independent daily caching adds async persistence; cancel after
            # the market stage has actually become visible to the user.
            deadline = asyncio.get_running_loop().time() + 5
            while not (self.store.get('dashboard', '600519') or {}).get('candles'):
                if asyncio.get_running_loop().time() >= deadline:
                    self.fail('Daily market stage did not become visible')
                await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(stopped.is_set())
            saved = self.store.get('dashboard', '600519')
            self.assertEqual(saved['candles'], self.market['candles'])
            self.assertEqual(len(saved['news']), 1)
            self.assertEqual(saved['pipeline']['job_status'], 'failed')
            self.assertEqual(saved['news'][0]['analysis_status'], 'failed')
            self.assertIsNone(saved['news'][0]['score'])


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_mail_and_wechat_adapters_report_results_without_live_sends(self):
        response = unittest.mock.Mock(is_success=True)
        response.json.return_value = {'code': 200}
        with patch('backend.briefing.read_config', return_value={'notifications': {'smtp': {'host': 'smtp.example.com', 'username': 'test', 'password': 'test', 'from_email': 'test@example.com'}}}), patch('backend.briefing.smtp_send') as smtp, patch('httpx.AsyncClient.post', new=AsyncMock(return_value=response)):
            result = await deliver({'email': 'test@example.com', 'pushplus_token': 'test-token'}, {'html': '<p>Digest</p>'})
            self.assertEqual(result, {'email': 'sent', 'wechat': 'accepted'})
            smtp.assert_called_once()

    async def test_missing_community_dates_do_not_generate_ratios(self):
        service = ResearchService(None, AsyncMock())
        with patch('backend.community.direct_posts', new=AsyncMock(return_value=([], {'retained': 0}))), patch('backend.providers.tavily', new=AsyncMock(return_value={'results': [{'title': '贵州茅台讨论', 'url': 'https://guba.eastmoney.com/news,600519,123.html', 'content': '讨论'}]})):
            result = await service.community(stock_by_code('600519'), '2026-09-01', '2026-10-03')
            self.assertEqual(result['status'], 'empty')
            self.assertIsNone(result['bull'])
            service.completion.assert_not_awaited()
