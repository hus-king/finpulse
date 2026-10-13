import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app import app
from backend.auth import AuthStore
from backend.morning import NEWS_SCHEDULE_TIMES, MorningService


class NewsSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.directory.name) / 'test.db')
        app.state.auth_store = self.auth
        self.context = TestClient(app, base_url='http://localhost')
        self.client = self.context.__enter__()
        self.store = app.state.research.store
        self.research = app.state.research
        self.morning = MorningService(self.research)

    def tearDown(self):
        self.context.__exit__(None, None, None)
        del app.state.auth_store
        self.directory.cleanup()

    def register(self, username):
        resp = self.client.post('/api/auth/register', headers={'Origin': 'http://localhost'}, json={'username': username, 'nickname': username, 'password': 'Password123!'})
        return resp.json()['user']

    def test_schedule_times(self):
        self.assertEqual(NEWS_SCHEDULE_TIMES, ['08:30', '12:00', '20:00'])

    def test_configure_news_cron_registers_jobs(self):
        async def run_test():
            with patch.object(MorningService, 'instance_enabled', new_callable=unittest.mock.PropertyMock(return_value=True)):
                sched = self.morning.start_scheduler()
                self.assertIsNotNone(sched)
                job_ids = [j.id for j in sched.get_jobs()]
                self.assertIn('morning_digest', job_ids)
                self.assertIn('news_research_12_00', job_ids)
                self.assertIn('news_research_20_00', job_ids)
                sched.shutdown(wait=False)

        asyncio.run(run_test())

    @patch('backend.research.ResearchService.refresh_shared', new_callable=AsyncMock)
    def test_run_news_batch(self, mock_refresh):
        mock_refresh.return_value = {'status': 'ok'}
        user = self.register('user_news')
        self.store.put('watchlist', 'list', ['600519', '000001'], user['id'])

        async def run_test():
            with patch('asyncio.sleep', new_callable=AsyncMock):
                return await self.morning.run_news_batch(slot='12:00')

        record = asyncio.run(run_test())

        self.assertIn('600519', record.get('completed', []))
        self.assertIn('000001', record.get('completed', []))
        self.assertEqual(record.get('total'), 2)
        self.assertEqual(record.get('slot'), '12:00')
        self.assertEqual(mock_refresh.await_count, 2)
        saved = self.store.get('news_scheduler', 'latest')
        self.assertIsNotNone(saved)
        self.assertEqual(saved['times'], NEWS_SCHEDULE_TIMES)
        self.assertEqual(saved['slot'], '12:00')

    @patch('backend.research.ResearchService.refresh_shared', new_callable=AsyncMock)
    def test_lease_prevents_duplicate_runs(self, mock_refresh):
        token = self.store.claim('news-batch-global', 600)
        self.assertIsNotNone(token)

        async def run_test():
            return await self.morning.run_news_batch(slot='12:00')

        result = asyncio.run(run_test())
        self.assertEqual(result.get('status'), 'busy')
        mock_refresh.assert_not_awaited()
        self.store.release('news-batch-global', token)

    def test_admin_news_trigger_endpoint_requires_auth(self):
        resp = self.client.post('/api/admin/news/trigger', headers={'Origin': 'http://localhost'})
        self.assertEqual(resp.status_code, 401)


if __name__ == '__main__':
    unittest.main()
