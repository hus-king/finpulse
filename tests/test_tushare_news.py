import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import httpx
from backend.auth import AuthStore
from backend.research_store import ResearchStore
from backend.providers import ProviderError
from backend.tushare_news import fetch_news, parse_news, configured_sources
from backend.hot_news_board import HotNewsBoardService, FEED_KEY, VERSION, merge_topics, public_board, rule_screen
from backend.news_cleaning import SHANGHAI

NOW = datetime(2026, 10, 26, 23, tzinfo=SHANGHAI)
CFG = {'tushare_news': {'base_url': 'http://provider.example:8030', 'token': 'fixture-private-token'}}

def payload(rows=None):
    return {'code': 0, 'data': {'fields': ['datetime', 'title', 'content', 'channels'], 'items': rows if rows is not None else [
        ['2026-10-26 22:30:00', '美国宣布加征进口关税', '美国宣布对指定进口商品加征关税，报道披露了政策调整。', '国际']]}}


class NewsProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_rest_protocol_uses_configured_endpoint_and_keeps_token_out_of_rows(self):
        async def handler(request):
            sent = json.loads(request.content)
            self.assertEqual(str(request.url), 'http://provider.example:8030/')
            self.assertEqual(sent['token'], 'fixture-private-token')
            self.assertEqual(sent['api_name'], 'news'); self.assertEqual(sent['params']['src'], 'cls')
            self.assertEqual(sent['params']['limit'], 100)
            return httpx.Response(200, json=payload())
        with patch('backend.tushare_news.read_config', return_value=CFG), patch('backend.tushare_news.httpx.AsyncClient', return_value=httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            rows = await fetch_news('tushare_cls', NOW)
        self.assertEqual(rows[0]['rank'], None); self.assertEqual(rows[0]['heat'], None)
        self.assertEqual(rows[0]['kind'], 'news'); self.assertIn('2026-10-26T22:30', rows[0]['published_at'])
        self.assertNotIn('fixture-private-token', json.dumps(rows))

    async def test_major_news_requests_body_and_permission_errors_are_redacted(self):
        async def handler(request):
            sent = json.loads(request.content)
            self.assertEqual(sent['api_name'], 'major_news'); self.assertIn('content', sent['fields'])
            self.assertNotIn('src', sent['params'])
            return httpx.Response(200, json={'code': 2002, 'msg': 'denied fixture-private-token'})
        with patch('backend.tushare_news.read_config', return_value=CFG), patch('backend.tushare_news.httpx.AsyncClient', return_value=httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            with self.assertRaises(ProviderError) as error: await fetch_news('tushare_major', NOW)
        self.assertNotIn('fixture-private-token', str(error.exception)); self.assertIn('订阅', str(error.exception))

    async def test_invalid_url_or_missing_config_never_sends_credentials(self):
        for cfg in ({}, {'tushare_news': {'token': 's', 'base_url': 'http://user:pass@host/?token=s'}}):
            with patch('backend.tushare_news.read_config', return_value=cfg), patch('backend.tushare_news.httpx.AsyncClient') as client:
                with self.assertRaises(ProviderError): await fetch_news('tushare_sina', NOW)
                client.assert_not_called()
        with patch('backend.tushare_news.read_config', return_value={}): self.assertEqual(configured_sources(), {})

    async def test_news_feed_has_no_fake_heat_and_public_payload_excludes_full_paid_body(self):
        rows = parse_news(payload(), 'tushare_cls', NOW)
        merged = merge_topics({'tushare_cls': {'items': rows, 'collected_at': NOW.isoformat()}})
        merged[0]['screening'] = rule_screen(merged[0]); merged[0]['sources'][0]['observed_timestamp'] = NOW.timestamp()
        with tempfile.TemporaryDirectory() as directory:
            auth = AuthStore(Path(directory) / 'test.db'); auth.initialize()
            store = ResearchStore(auth); store.initialize()
            store.put('hot_news_board', FEED_KEY, {'version': VERSION, 'topics': merged, 'sources': {'tushare_cls': {'fetched_at': NOW.timestamp()}}, 'refreshed_at': NOW.timestamp()})
            service = HotNewsBoardService(store, None, clock=NOW.timestamp)
            result = public_board(service.feed())
        self.assertEqual(result['items'][0]['rank_score'], 0)
        self.assertIsNone(result['items'][0]['sources'][0]['rank'])
        self.assertNotIn('_content', result['items'][0])
        self.assertEqual(merged[0]['_content'], rows[0]['content'])


class NewsParsingTests(unittest.TestCase):
    def test_identical_long_reports_merge_sources_and_keep_readable_headline(self):
        body = '深蓝航天宣布重要火箭关键制造节点完成，相关结构件完成制造下线。' * 5
        first = parse_news(payload([['2026-10-26 22:30:00', body, body, '']]), 'tushare_cls', NOW)
        second = parse_news(payload([['2026-10-26 22:30:00', '深蓝航天火箭关键结构件下线', body, '']]), 'tushare_sina', NOW)
        merged = merge_topics({source: {'items': rows, 'collected_at': NOW.isoformat()}
                               for source, rows in [('tushare_cls', first), ('tushare_sina', second)]})
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['title'], second[0]['title'])
        self.assertEqual({source['id'] for source in merged[0]['sources']}, {'tushare_cls', 'tushare_sina'})
        self.assertEqual(merged[0]['_content'], first[0]['content'])
        # Short generic summaries cannot prove two distinct developments are identical.
        first[0]['summary'] = second[0]['summary'] = '相关消息正在核实。'
        first[0]['content'] = second[0]['content'] = ''
        self.assertEqual(len(merge_topics({source: {'items': rows, 'collected_at': NOW.isoformat()}
                                          for source, rows in [('tushare_cls', first), ('tushare_sina', second)]})), 2)

    def test_expired_future_invalid_dates_and_missing_content_are_not_published(self):
        rows = [['2026-10-26 22:30:00', '美国宣布加征进口关税', '美国宣布对进口商品加征关税。', ''],
                ['2025-10-26 22:30:00', '旧闻', '旧内容', ''], ['2026-10-29 22:30:00', '未来', '未来内容', ''],
                ['bad-date', '错日期', '内容', ''], ['2026-10-26 22:30:00', '无正文', '', '']]
        self.assertEqual(len(parse_news(payload(rows), 'tushare_sina', NOW)), 1)
        utc = payload([['2026-10-26T14:30:00+00:00', '美国宣布加征进口关税', '美国宣布对进口商品加征关税。', '']])
        self.assertEqual(parse_news(utc, 'tushare_sina', NOW)[0]['published_at'], '2026-10-26T22:30:00+08:00')

    def test_missing_schema_fails_explicitly_and_candidates_are_bounded(self):
        with self.assertRaises(ProviderError): parse_news({'code': 0, 'data': {'fields': [], 'items': []}}, 'tushare_cls', NOW)
        rows = [['2026-10-26 22:30:00', f'市场资讯{i}', '报道正文，材料仍需核实。', ''] for i in range(100)]
        self.assertEqual(len(parse_news(payload(rows), 'tushare_cls', NOW)), 15)


if __name__ == '__main__': unittest.main()
