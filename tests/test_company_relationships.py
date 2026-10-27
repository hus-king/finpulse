import unittest
from unittest.mock import AsyncMock, patch
from backend.providers import company_relationships, ProviderError
from backend.company_knowledge import curated_relationships


class RelationshipTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_stock_identified_exchange_disclosures_become_quotable_cards(self):
        rows = [
            {'url': 'https://static.cninfo.com.cn/report.pdf', 'title': '某上市公司年报', 'content': '600418 江淮汽车与合作伙伴共同建设品牌，提供整车生产制造。'},
            {'url': 'https://cninfo.com.cn.evil.test/fake', 'content': '600418 江淮汽车旗下品牌。'},
            {'url': 'https://www.sse.com.cn/unrelated.pdf', 'content': '另一家企业生产制造某品牌产品。'},
        ]
        with patch('backend.company_knowledge.curated_relationships', return_value=[]), patch('backend.providers.tavily', new=AsyncMock(return_value={'results': rows})) as search:
            result = await company_relationships({'name': '江淮汽车', 'code': '600418'})
        self.assertEqual(len(result['cards']), 1)
        self.assertIn('生产制造', result['cards'][0]['text'])
        self.assertEqual(len(result['cards'][0]['id']), 64)
        self.assertEqual(search.call_args.args[1]['include_domains'], ['cninfo.com.cn', 'sse.com.cn', 'szse.cn'])

    async def test_no_usable_company_evidence_does_not_invent_brand_links(self):
        with patch('backend.company_knowledge.curated_relationships', return_value=[]), patch('backend.providers.tavily', new=AsyncMock(return_value={'results': []})):
            with self.assertRaises(ProviderError): await company_relationships({'name': '江淮汽车', 'code': '600418'})

    async def test_reviewed_relationship_cards_have_expiry_and_never_predefine_scores(self):
        rows = curated_relationships({'code': '600418'}, today='2026-10-27')
        self.assertIn('尊界', rows[0]['text'])
        self.assertNotIn('score', rows[0])
        self.assertEqual(curated_relationships({'code': '600418'}, today='2028-01-01'), [])
