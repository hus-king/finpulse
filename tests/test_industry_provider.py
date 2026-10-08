import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend.providers import ProviderError, stock_profile

STOCK = {'code': '600031', 'name': '三一重工'}


class IndustryProviderTests(unittest.IsolatedAsyncioTestCase):
    async def get_profile(self, row):
        async def handler(request):
            self.assertEqual(request.url.host, 'datacenter.eastmoney.com')
            self.assertEqual(request.url.params['filter'], '(SECUCODE="600031.SH")')
            self.assertNotIn('authorization', request.headers)
            return httpx.Response(200, json={'success': True, 'result': {'data': [row]}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch('backend.providers.akshare_worker', new=AsyncMock(side_effect=ProviderError('ConnectionError'))), patch('backend.providers.httpx.AsyncClient', return_value=client):
            return await stock_profile(STOCK)

    async def test_f10_fallback_uses_verified_company_industry(self):
        result = await self.get_profile({'SECUCODE': '600031.SH', 'SECURITY_CODE': '600031', 'SECURITY_NAME_ABBR': '三一重工', 'BOARD_NAME_2LEVEL': '工程机械'})
        self.assertEqual(result['code'], '600031')
        self.assertEqual(result['industry'], '工程机械')
        self.assertIn('F10', result['source'])

    async def test_wrong_stock_exchange_missing_or_placeholder_industry_are_rejected(self):
        valid = {'SECUCODE': '600031.SH', 'SECURITY_CODE': '600031', 'BOARD_NAME_2LEVEL': '工程机械'}
        with self.assertRaises(ProviderError):
            await self.get_profile('malformed row')
        for change in ({'SECURITY_CODE': '601857'}, {'SECUCODE': '600031.SZ'}, {'BOARD_NAME_2LEVEL': None}, {'BOARD_NAME_2LEVEL': 'A 股'}):
            with self.subTest(change=change), self.assertRaises(ProviderError):
                await self.get_profile({**valid, **change})

    async def test_valid_akshare_profile_does_not_call_fallback(self):
        rows = [{'item': key, 'value': value} for key, value in [('股票代码', '600031'), ('股票简称', '三一重工'), ('行业', '工程机械')]]
        with patch('backend.providers.akshare_worker', new=AsyncMock(return_value={'rows': rows})), patch('backend.providers.httpx.AsyncClient') as fallback:
            result = await stock_profile(STOCK)
            self.assertEqual(result['industry'], '工程机械')
            fallback.assert_not_called()
