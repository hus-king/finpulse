import unittest
from unittest.mock import AsyncMock, patch
import httpx
from backend import providers

STOCK={'code':'600028','name':'中国石化','exchange':'SH'}

class BusinessProfileTests(unittest.IsolatedAsyncioTestCase):
    async def profile(self, record):
        async def handler(request):
            return httpx.Response(200,json={'success':True,'result':{'data':[record]}})
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch('backend.providers.httpx.AsyncClient',return_value=client):
            self.assertTrue(hasattr(providers,'business_profile'), 'Business facts provider missing')
            return await providers.business_profile(STOCK)

    async def test_main_business_has_source_and_does_not_invent_revenue_shares(self):
        result=await self.profile({'SECUCODE':'600028.SH','SECURITY_CODE':'600028','MAIN_BUSINESS':'石油炼制与石化产品生产、销售'})
        self.assertIn('石油炼制',result['main_business'])
        self.assertIsNone(result['revenue_segments'])
        self.assertIn('eastmoney.com',result['url'])

    async def test_wrong_identity_or_missing_business_is_rejected(self):
        for record in ({'SECUCODE':'600028.SH','SECURITY_CODE':'601857','MAIN_BUSINESS':'石油炼制'},{'SECUCODE':'600028.SH','SECURITY_CODE':'600028','MAIN_BUSINESS':None}):
            with self.subTest(record=record), self.assertRaises(providers.ProviderError):
                await self.profile(record)

    async def test_multi_topic_search_preserves_good_results_and_records_partial_failure(self):
        async def search(endpoint,payload):
            if '风险' in payload['query']:
                raise providers.ProviderError('source unavailable')
            return {'results':[{'url':'https://example.com/news','title':'公司公告订单'}]}
        with patch('backend.providers.tavily',new=AsyncMock(side_effect=search)):
            result=await providers.search_news(STOCK,'2026-10-01','2026-10-08')
        self.assertEqual(len(result['results']),1)
        self.assertEqual(len(result.get('topics',[])),4)
        self.assertTrue(result.get('warnings'))
