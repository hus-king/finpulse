import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app import app
from backend.auth import AuthStore
from backend.morning import COMMUNITY_SCHEDULE_TIMES, MorningService


class CommunitySchedulerTests(unittest.TestCase):
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
        self.assertEqual(COMMUNITY_SCHEDULE_TIMES, ['08:45', '12:00', '15:15', '20:30'])

    def test_all_watchlist_codes(self):
        user = self.register('user1')
        self.store.put('watchlist', 'list', ['600519', '300750'], user['id'])
        codes = self.morning.all_watchlist_codes()
        self.assertIn('600519', codes)
        self.assertIn('300750', codes)

    @patch('backend.research.ResearchService.collect_community', new_callable=AsyncMock)
    def test_run_community_batch(self, mock_collect):
        mock_collect.return_value = {'status': 'ok'}
        user = self.register('user2')
        self.store.put('watchlist', 'list', ['600519'], user['id'])

        async def run_test():
            with patch('asyncio.sleep', new_callable=AsyncMock):
                return await self.morning.run_community_batch()

        record = asyncio.run(run_test())

        self.assertIn('600519', record.get('completed', []))
        self.assertEqual(record.get('total'), 1)
        mock_collect.assert_awaited_once_with('600519', days=30)
        saved = self.store.get('community_scheduler', 'latest')
        self.assertIsNotNone(saved)
        self.assertEqual(saved['times'], COMMUNITY_SCHEDULE_TIMES)


if __name__ == '__main__':
    unittest.main()
