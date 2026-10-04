import asyncio
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

from backend.app import app
from backend.auth import AuthStore
from backend.morning import MorningService
from backend.news_cleaning import SHANGHAI
from backend.recommendations import build_digest
from backend.research import ResearchService
from backend.research_store import ResearchStore


def article(key='1', age=0, score=2, **extras):
    return {'id': key, 'title': '公司披露年度业绩及回购公告 ' + key, 'url': 'https://example.com/news/' + key, 'time': (datetime.now(SHANGHAI).date() - timedelta(days=age)).isoformat(), 'score': score, 'tier': 'company', 'text_source': 'extracted_body', 'date_status': 'body_verified', 'analysis': {'summary': '真实材料摘要', 'uncertainty': '需核验原文'}, **extras}


def snapshot(rows=None):
    return {'news': rows or [article()], 'as_of': datetime.now(SHANGHAI).isoformat(), 'pipeline': {'warnings': [], 'counts': {'analyzed': 1}}}


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.temp.name) / 'test.db')
        self.auth.initialize()
        self.store = ResearchStore(self.auth)
        self.store.initialize()
        self.store.put('watchlist', 'list', ['600519', '300750'], 'one')

    def tearDown(self):
        self.temp.cleanup()

    def test_freshness_evidence_and_negative_news_have_equal_reading_value(self):
        self.store.put('dashboard', '600519', snapshot([article('1'), article('2', score=-2), article('3', age=5), article('4', text_source='search_fragments', date_status='metadata_only')]))
        rows = {row['id']: row for row in build_digest(self.store, 'one')['recommendations']}
        self.assertEqual(rows['1']['priority'], rows['2']['priority'])
        self.assertGreater(rows['1']['priority'], rows['3']['priority'])
        self.assertGreater(rows['1']['priority'], rows['4']['priority'])

    def test_future_expired_and_conflicting_dates_are_excluded(self):
        self.store.put('dashboard', '600519', snapshot([article('valid'), article('future', age=-1), article('expired', age=40), article('bad', date_status='conflict')]))
        self.assertEqual([row['id'] for row in build_digest(self.store, 'one')['recommendations']], ['valid'])

    def test_same_event_shared_across_stocks_is_merged_and_private_watchlist_used(self):
        self.store.put('dashboard', '600519', snapshot([article('100'), article('1')]))
        self.store.put('dashboard', '300750', snapshot([article('100'), article('2')]))
        digest = build_digest(self.store, 'one')
        shared = next(row for row in digest['recommendations'] if row['id'] == '100')
        self.assertEqual(len(shared['related_stocks']), 2)
        self.assertEqual(digest['ranking']['deduplicated'], 1)
        self.store.put('watchlist', 'list', [], 'other')
        self.assertFalse(build_digest(self.store, 'other')['recommendations'])

    def test_known_quote_profiles_and_news_indexes_do_not_enter_ranked_digest(self):
        urls = ['https://tw.stock.yahoo.com/quote/600519.SS', 'https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpManager/stockid/600519.phtml', 'https://www.futunn.com/stock/688981-SH/news', 'https://www.moomoo.com/hant/stock/688981-SH', 'https://data.eastmoney.com/notice/300750.html', 'https://data.eastmoney.com/zjlx/688981.html', 'https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllBulletin/stockid/300750.phtml']
        self.store.put('dashboard', '600519', snapshot([article(str(i), url=url) for i, url in enumerate(urls)] + [article('99')]))
        digest = build_digest(self.store, 'one')
        self.assertEqual([row['id'] for row in digest['recommendations']], ['99'])
        self.assertEqual(digest['ranking']['filtered_non_news'], len(urls))

    def test_personal_interest_boosts_priority_and_source_html_is_escaped(self):
        self.store.put('dashboard', '600519', snapshot([article('x', title='<script>年度业绩</script>')]))
        first = build_digest(self.store, 'one')
        self.store.put('subscription', 'settings', {'interests': ['业绩财报']}, 'one')
        next_digest = build_digest(self.store, 'one')
        self.assertGreater(next_digest['recommendations'][0]['priority'], first['recommendations'][0]['priority'])
        self.assertNotIn('<script>', next_digest['html'])

    def test_lease_acquisition_is_atomic_across_connections(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            tokens = list(pool.map(lambda _: self.store.claim('same'), range(8)))
        winning = [token for token in tokens if token]
        self.assertEqual(len(winning), 1)
        self.store.release('same', 'wrong-token')
        self.assertIsNone(self.store.claim('same'))
        self.store.release('same', winning[0])
        self.assertIsNotNone(self.store.claim('same'))


class MorningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.temp.name) / 'test.db')
        self.auth.initialize()
        self.store = ResearchStore(self.auth)
        self.store.initialize()
        self.research = ResearchService(self.store, AsyncMock())
        self.morning = MorningService(self.research)
        self.research.briefing = self.morning
        self.users = [self.auth.register(name, name, 'Morning-test-2026!')[0] for name in ('user_one', 'user_two')]
        for user in self.users:
            self.store.put('watchlist', 'list', ['600519', '300750'], user['id'])

    async def asyncTearDown(self):
        await self.morning.close()
        await self.research.close()
        self.temp.cleanup()

    async def test_union_collects_stock_once_daily_and_retains_private_histories(self):
        async def collect(code, *args):
            result = snapshot()
            self.store.put('dashboard', code, result)
            return result
        with patch.object(self.research, 'collect', new=AsyncMock(side_effect=collect)) as source, patch('backend.briefing.deliver', new_callable=AsyncMock) as send:
            result = await self.morning.run()
            self.assertEqual(result['users'], 2)
            self.assertEqual(source.await_count, 2)
            again = await self.morning.run()
            self.assertEqual(source.await_count, 2)
            await self.morning.run(manual=True)
            self.assertEqual(source.await_count, 2)
            send.assert_not_awaited()  # On-site generation requires no delivery credentials.
        date = result['date']
        for user in self.users:
            self.assertEqual(self.store.get('briefing', date, user['id'])['date'], date)

    async def test_cross_instance_daily_lease_prevents_duplicate_work(self):
        gate = asyncio.Event()
        entered = asyncio.Event()
        async def collect(*args):
            entered.set()
            await gate.wait()
            return snapshot()
        other = MorningService(self.research)
        with patch.object(self.research, 'collect', new=AsyncMock(side_effect=collect)) as source:
            first = asyncio.create_task(self.morning.run())
            await entered.wait()
            self.assertEqual((await other.run())['status'], 'busy')
            gate.set()
            await first
            self.assertEqual(source.await_count, 2)

    async def test_concurrent_manual_launches_share_one_daily_run(self):
        gate = asyncio.Event()
        async def run(**kwargs):
            await gate.wait()
        with patch.object(self.morning, 'run', new=AsyncMock(side_effect=run)) as daily:
            first, second = await asyncio.gather(self.morning.launch(), self.morning.launch())
            self.assertEqual(first['id'], second['id'])
            gate.set()
            await self.morning.task
            self.assertEqual(daily.await_count, 1)

    async def test_completed_channel_is_not_repeated_when_failed_channel_retries(self):
        owner = self.users[0]['id']
        self.store.put('subscription', 'settings', {'enabled': True, 'email': 'test@example.com', 'pushplus_token': 'private-token'}, owner)
        digest = self.morning.generate(owner)
        async def send(subscription, _):
            return {'email': 'sent'} if 'email' in subscription else {'wechat': 'failed' if send.calls == 0 else 'accepted'}
        send.calls = 0
        with patch('backend.briefing.deliver', new=AsyncMock(side_effect=send)) as provider:
            await self.morning.deliver_digest(owner, digest)
            send.calls = 1
            await self.morning.retry_deliveries()
            self.assertEqual(provider.await_count, 2)  # Wait at least fifteen minutes.
            with patch('backend.morning.time.time', return_value=time.time() + 901):
                await self.morning.retry_deliveries()
            await self.morning.retry_deliveries()
            self.assertEqual(provider.await_count, 3)
        record = self.store.get('delivery', digest['date'], owner)
        self.assertEqual(record['status'], {'email': 'sent', 'wechat': 'accepted'})
        self.assertEqual(record['channels']['email']['attempts'], 1)
        self.assertEqual(record['channels']['wechat']['attempts'], 2)

    async def test_unknown_send_is_not_retried_and_inactive_account_is_not_sent(self):
        owner = self.users[0]['id']
        self.store.put('subscription', 'settings', {'enabled': True, 'email': 'test@example.com'}, owner)
        digest = self.morning.generate(owner)
        with patch('backend.briefing.deliver', new=AsyncMock(return_value={'email': 'unknown'})) as send:
            await self.morning.deliver_digest(owner, digest)
            await self.morning.deliver_digest(owner, digest)
            await self.morning.retry_deliveries()
            self.assertEqual(send.await_count, 1)
            with self.auth.transaction() as conn:
                conn.execute('UPDATE users SET is_active=0 WHERE id=?', (owner,))
            await self.morning.deliver_digest(owner, digest, test=True)
            self.assertEqual(send.await_count, 1)

    async def test_retry_limit_and_scheduler_restart_catchup(self):
        owner = self.users[0]['id']
        self.store.put('subscription', 'settings', {'enabled': True, 'email': 'test@example.com'}, owner)
        digest = self.morning.generate(owner)
        with patch('backend.briefing.deliver', new=AsyncMock(return_value={'email': 'failed'})) as send:
            start = time.time()
            for offset in (0, 1, 901, 1802, 2703):
                with patch('backend.morning.time.time', return_value=start + offset):
                    await self.morning.deliver_digest(owner, digest)
            self.assertEqual(send.await_count, 3)
        self.store.put('scheduler', 'settings', {'enabled': True, 'morning_time': '00:00', 'every_day': True})
        with patch.object(self.morning, 'run', new=AsyncMock()) as run:
            await self.morning.tick()
            run.assert_awaited_once()

    async def test_each_channel_obeys_its_retry_time_even_on_manual_rerun(self):
        owner = self.users[0]['id']
        self.store.put('subscription', 'settings', {'enabled': True, 'email': 'test@example.com', 'pushplus_token': 'private-token'}, owner)
        digest = self.morning.generate(owner)
        with patch('backend.briefing.deliver', new=AsyncMock(side_effect=lambda subscription, _: {'email' if 'email' in subscription else 'wechat': 'failed'})) as send:
            await self.morning.deliver_digest(owner, digest)
            record = self.store.get('delivery', digest['date'], owner)
            record['channels']['email']['retry_after'] = time.time() - 1
            record['channels']['wechat']['retry_after'] = time.time() + 1800
            self.store.put('delivery', digest['date'], record, owner)
            await self.morning.retry_deliveries()
            self.assertEqual(send.await_count, 3)
            self.assertEqual(send.call_args.args[0], {'email': 'test@example.com'})
            await self.morning.deliver_digest(owner, digest)
            self.assertEqual(send.await_count, 3)

    async def test_schedule_changes_from_another_instance_are_reconciled(self):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        self.morning.scheduler = AsyncIOScheduler(timezone='Asia/Shanghai')
        self.morning.scheduler.start()
        await self.morning.reload_schedule()
        first_job = self.morning.scheduler.get_job('morning_digest')
        await self.morning.reload_schedule()
        self.assertIs(self.morning.scheduler.get_job('morning_digest'), first_job)
        self.store.put('scheduler', 'settings', {'enabled': True, 'morning_time': '09:45', 'every_day': False})
        await self.morning.reload_schedule()
        trigger = str(self.morning.scheduler.get_job('morning_digest').trigger)
        self.assertIn("hour='9'", trigger)
        self.assertIn("minute='45'", trigger)
        self.assertIn("day_of_week='mon-fri'", trigger)
        self.store.put('scheduler', 'settings', {'enabled': False})
        await self.morning.reload_schedule()
        self.assertIsNone(self.morning.scheduler.get_job('morning_digest'))


class ConcurrentApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_twenty_users_private_data_and_shared_provider_work(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = AuthStore(Path(directory) / 'test.db')
            auth.initialize()
            users = [auth.register(f'concurrent_{i:02}', f'用户{i}', 'Concurrent-test-2026!')[0] for i in range(20)]
            app.state.auth_store = auth
            with patch.dict('os.environ', {'FINPULSE_SCHEDULER_ENABLED': '0'}):
                async with app.router.lifespan_context(app):
                    transport = httpx.ASGITransport(app=app)
                    clients = [httpx.AsyncClient(transport=transport, base_url='http://localhost') for _ in users]
                    try:
                        logins = await asyncio.gather(*(client.post('/api/auth/login', headers={'Origin': 'http://localhost'}, json={'username': f'concurrent_{i:02}', 'password': 'Concurrent-test-2026!'}) for i, client in enumerate(clients)))
                        self.assertTrue(all(response.status_code == 200 for response in logins))
                        headers = [{'Origin': 'http://localhost', 'X-CSRF-Token': response.json()['csrf_token']} for response in logins]
                        preferences = await asyncio.gather(*(client.post('/api/watchlist', headers=headers[i], json={'codes': ['600519'] if i % 2 else ['300750']}) for i, client in enumerate(clients)))
                        self.assertTrue(all(response.status_code == 200 for response in preferences))
                        gate = asyncio.Event()
                        async def collect(code, *args):
                            await gate.wait()
                            value = snapshot()
                            app.state.research.store.put('dashboard', code, value)
                            return value
                        with patch.object(app.state.research, 'collect', new=AsyncMock(side_effect=collect)) as source:
                            replies = await asyncio.gather(*(client.post('/api/research/600519/refresh', headers=headers[i], json={}) for i, client in enumerate(clients)))
                            self.assertTrue(all(response.status_code == 202 for response in replies))
                            self.assertEqual(len({response.json()['id'] for response in replies}), 20)
                            foreign = await clients[0].get('/api/research/jobs/' + replies[1].json()['id'])
                            self.assertEqual(foreign.status_code, 404)
                            gate.set()
                            for _ in range(100):
                                if not app.state.research.active:
                                    break
                                await asyncio.sleep(.01)
                            self.assertFalse(app.state.research.active)
                            self.assertEqual(source.await_count, 1)
                        digests = await asyncio.gather(*(client.post('/api/briefing/generate', headers=headers[i], json={}) for i, client in enumerate(clients)))
                        self.assertTrue(all(response.status_code == 200 for response in digests))
                        for i, response in enumerate(digests):
                            self.assertEqual(response.json()['sections'][0]['code'], '600519' if i % 2 else '300750')
                        self.assertEqual((await clients[0].get('/api/admin/briefing/status')).status_code, 403)
                        secret = await clients[0].post('/api/subscription', headers=headers[0], json={'enabled': True, 'pushplus_token': 'never-show-this', 'interests': ['风险事件']})
                        self.assertEqual(secret.status_code, 200)
                        self.assertNotIn('never-show-this', (await clients[0].get('/api/subscription')).text)
                        self.assertEqual((await clients[1].get('/api/subscription')).json()['interests'], [])
                    finally:
                        for client in clients:
                            await client.aclose()
                del app.state.auth_store
