import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app import app
from backend.auth import AuthStore


class BatchResearchTests(unittest.TestCase):
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

    def login(self):
        resp = self.client.post('/api/auth/register', headers={'Origin': 'http://localhost'}, json={'username': 'researcher', 'nickname': 'Researcher', 'password': 'Password123!'})
        self.assertEqual(resp.status_code, 201)
        return resp.json()['user'], resp.json()['csrf_token']

    def test_unauthorized(self):
        resp = self.client.post('/api/watchlist/analyze-all', json={})
        self.assertEqual(resp.status_code, 401)

    def test_invalid_codes(self):
        _, csrf = self.login()
        resp = self.client.post('/api/watchlist/analyze-all', json={'codes': ['999999']}, headers={'Origin': 'http://localhost', 'X-CSRF-Token': csrf})
        self.assertEqual(resp.status_code, 422)

    @patch('backend.research.ResearchService.launch', new_callable=AsyncMock)
    def test_batch_analyze_success(self, mock_launch):
        mock_launch.side_effect = lambda code, user, **kw: {'id': f'job-{code}', 'code': code, 'status': 'queued'}
        _, csrf = self.login()
        resp = self.client.post(
            '/api/watchlist/analyze-all',
            json={'codes': ['600519', '300750']},
            headers={'Origin': 'http://localhost', 'X-CSRF-Token': csrf},
        )
        self.assertEqual(resp.status_code, 202)
        data = resp.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['count'], 2)
        self.assertEqual(len(data['jobs']), 2)
        self.assertEqual(data['jobs'][0]['code'], '600519')
        self.assertEqual(data['jobs'][1]['code'], '300750')
        self.assertEqual(mock_launch.call_count, 2)


if __name__ == '__main__':
    unittest.main()
