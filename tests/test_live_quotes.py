import unittest
from unittest.mock import patch, AsyncMock
from backend.live_quotes import code_to_symbol, get_batch_quotes, fetch_tencent_quotes, fetch_sina_quotes


class LiveQuotesTests(unittest.TestCase):
    def test_code_to_symbol(self):
        self.assertEqual(code_to_symbol('600519'), 'sh600519')
        self.assertEqual(code_to_symbol('688836'), 'sh688836')
        self.assertEqual(code_to_symbol('000425'), 'sz000425')
        self.assertEqual(code_to_symbol('300750'), 'sz300750')

    @patch('httpx.AsyncClient.get')
    def test_fetch_tencent_quotes_success(self, mock_get):
        mock_resp = AsyncMock()
        sample_text = (
            'v_sh600519="1~贵州茅台~600519~1288.00~1263.00~1262.95~31556~19046~12510~'
            '1287.00~6~1286.90~1~1286.89~1~1286.60~1~1286.50~2~1288.00~10~1288.01~1~'
            '1288.06~4~1288.10~1~1288.16~2~~20261012161454~25.00~1.98~1288.00~1261.00~'
            '1288.00/31556/4042857559~31556~404286~0.25~19.77~~1288.00~1261.00~2.14";'
        )
        mock_resp.content = sample_text.encode('gbk')
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        import asyncio
        quotes = asyncio.run(fetch_tencent_quotes(['600519']))
        self.assertIn('600519', quotes)
        q = quotes['600519']
        self.assertEqual(q['code'], '600519')
        self.assertEqual(q['name'], '贵州茅台')
        self.assertEqual(q['price'], 1288.00)
        self.assertEqual(q['change'], 1.98)
        self.assertEqual(q['previous_close'], 1263.00)
        self.assertEqual(q['open'], 1262.95)
        self.assertEqual(q['high'], 1288.00)
        self.assertEqual(q['low'], 1261.00)
        self.assertEqual(q['volume'], 3155600)
        self.assertEqual(q['source'], 'tencent')
        self.assertEqual(q['as_of'], '2026-10-12 16:14:54')

    @patch('httpx.AsyncClient.get')
    def test_fetch_sina_quotes_success(self, mock_get):
        mock_resp = AsyncMock()
        sample_text = (
            'var hq_str_sz000425="徐工机械,7.16,7.15,7.37,7.38,7.15,7.36,7.37,139937500,'
            '1024216356,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2026-10-12,16:14:15,00";\n'
        )
        mock_resp.content = sample_text.encode('gbk')
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        import asyncio
        quotes = asyncio.run(fetch_sina_quotes(['000425']))
        self.assertIn('000425', quotes)
        q = quotes['000425']
        self.assertEqual(q['code'], '000425')
        self.assertEqual(q['name'], '徐工机械')
        self.assertEqual(q['price'], 7.37)
        self.assertEqual(q['change'], 3.08)
        self.assertEqual(q['previous_close'], 7.15)
        self.assertEqual(q['source'], 'sina')
        self.assertEqual(q['as_of'], '2026-10-12 16:14:15')

    def test_empty_codes(self):
        import asyncio
        result = asyncio.run(get_batch_quotes([]))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['items'], {})


if __name__ == '__main__':
    unittest.main()
