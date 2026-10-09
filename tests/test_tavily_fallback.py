import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend.providers import ProviderError, tavily, tavily_keys


class TavilyFallbackTests(unittest.IsolatedAsyncioTestCase):
    def test_tavily_keys_parsing(self):
        self.assertEqual(tavily_keys({}), [])
        self.assertEqual(tavily_keys({'tavily_api_key': 'key-1'}), ['key-1'])
        self.assertEqual(
            tavily_keys({'tavily_api_key': 'key-1', 'tavily_backup_api_key': 'key-2'}),
            ['key-1', 'key-2']
        )
        self.assertEqual(
            tavily_keys({'tavily_api_key': 'key-1, key-2', 'tavily_backup_api_keys': ['key-3', 'key-1']}),
            ['key-1', 'key-2', 'key-3']
        )

    async def test_primary_key_success_does_not_call_backup(self):
        auth_headers = []

        async def mock_post(url, headers=None, json=None):
            auth_headers.append(headers.get('Authorization'))
            return httpx.Response(200, json={'results': [{'title': 'ok'}]}, request=httpx.Request('POST', url))

        with patch('backend.providers.read_config', return_value={
            'tavily_api_key': 'primary-key',
            'tavily_backup_api_key': 'backup-key',
            'tavily_base_url': 'https://api.tavily.com'
        }):
            with patch('backend.providers.httpx.AsyncClient.post', side_effect=mock_post):
                res = await tavily('search', {'query': 'test'})
                self.assertEqual(res['results'][0]['title'], 'ok')
                self.assertEqual(auth_headers, ['Bearer primary-key'])

    async def test_primary_key_fails_falls_back_to_backup_key(self):
        auth_headers = []

        async def mock_post(url, headers=None, json=None):
            auth = headers.get('Authorization')
            auth_headers.append(auth)
            if 'primary-key' in auth:
                # Simulate rate-limit or quota error on primary key
                return httpx.Response(429, text='Rate limited', request=httpx.Request('POST', url))
            return httpx.Response(200, json={'results': [{'title': 'backup_ok'}]}, request=httpx.Request('POST', url))

        with patch('backend.providers.read_config', return_value={
            'tavily_api_key': 'primary-key',
            'tavily_backup_api_key': 'backup-key',
            'tavily_base_url': 'https://api.tavily.com'
        }):
            with patch('backend.providers.httpx.AsyncClient.post', side_effect=mock_post):
                res = await tavily('search', {'query': 'test'})
                self.assertEqual(res['results'][0]['title'], 'backup_ok')
                self.assertEqual(auth_headers, ['Bearer primary-key', 'Bearer backup-key'])

    async def test_all_keys_fail_raises_provider_error(self):
        async def mock_post(url, headers=None, json=None):
            return httpx.Response(401, text='Unauthorized', request=httpx.Request('POST', url))

        with patch('backend.providers.read_config', return_value={
            'tavily_api_key': 'key-1',
            'tavily_backup_api_key': 'key-2',
            'tavily_base_url': 'https://api.tavily.com'
        }):
            with patch('backend.providers.httpx.AsyncClient.post', side_effect=mock_post):
                with self.assertRaises(ProviderError):
                    await tavily('search', {'query': 'test'})


if __name__ == '__main__':
    unittest.main()
