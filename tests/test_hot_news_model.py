import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from backend.app import app, ModelConfig, _completion, completion, hot_news_completion


class ModelRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_thinking_switch_is_scoped_to_supported_hot_news_calls(self):
        cases = [('https://api.ppio.com/openai/v1', 'deepseek/deepseek-v4.1-flash', True, True),
                 ('https://api.ppio.com/openai/v1', 'deepseek/deepseek-v4.1-flash', False, False),
                 ('https://other.example/v1', 'deepseek/deepseek-v4.1-flash', True, False),
                 ('https://api.ppio.com/openai/v1', 'other-model', True, False)]
        for endpoint, model, disable, expected in cases:
            async def handler(request):
                data = json.loads(request.content)
                self.assertEqual('thinking' in data, expected)
                if expected: self.assertEqual(data['thinking'], {'type': 'disabled'})
                self.assertEqual(data['max_tokens'], 8000)
                return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"groups":[]}'}}]})
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            config = ModelConfig(base_url=endpoint, model=model, api_key='fixture-token')
            with patch('backend.app.load_config', return_value=config), patch('backend.app.httpx.AsyncClient', return_value=client):
                await _completion([{'role': 'user', 'content': 'fixture'}], 8000, disable_thinking=disable)

    async def test_hot_wrapper_requests_switch_and_default_call_is_unchanged(self):
        with patch('backend.app.completion', new=AsyncMock(return_value={'content': 'fixture'})) as model:
            await hot_news_completion([], max_tokens=8000)
            model.assert_awaited_once_with([], 8000, disable_thinking=True)
        app.state.model_capacity = asyncio.Semaphore(1)
        with patch('backend.app._completion', new=AsyncMock(return_value={'content': 'fixture'})) as model:
            await completion([], 1100)
            model.assert_awaited_once_with([], 1100)

    async def test_capacity_is_released_when_hot_call_fails(self):
        app.state.model_capacity = asyncio.Semaphore(1)
        with patch('backend.app._completion', new=AsyncMock(side_effect=ValueError('fixture'))):
            with self.assertRaises(ValueError): await completion([], disable_thinking=True)
        await asyncio.wait_for(app.state.model_capacity.acquire(), timeout=.1)
        app.state.model_capacity.release()
