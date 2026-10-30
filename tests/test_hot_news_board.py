import json
from copy import deepcopy
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import app
from backend.auth import AuthStore
from backend.hot_news_board import (FEED_KEY, MAX_STALE, SOURCES, TTL, VERSION, HotNewsBoardService,
    digest, fetch_board, filter_board, merge_topics, normalized_title, parse_baidu, parse_weibo, rule_screen, validate_screening)
from backend.news_cleaning import SHANGHAI
from backend.providers import ProviderError
from backend.research_store import ResearchStore

NOW = datetime(2026, 10, 26, 12, tzinfo=SHANGHAI).timestamp()


def board_row(title='美国宣布加征进口关税', rank=1, summary=''):
    return {'title': title, 'rank': rank, 'heat': '123456', 'summary': summary, 'url': 'https://www.baidu.com/s?wd=test'}


def topic(title='美国宣布加征进口关税', summary=''):
    return {'id': digest(title), 'title': title, 'summary': summary, 'sources': [], 'rank_score': .01}


def model_row(item):
    return {'id': item['id'], 'decision': 'keep', 'channels': ['trade'],
            'reason': '关税相关变化可能改变进出口成本，需要核实执行范围。', 'evidence': item['title'], 'sectors': [],
            'event': {'kind': 'policy', 'actor': item['title'][:2], 'action': '宣布关税变化', 'new_fact': item['title'],
                      'novelty': 'new_development', 'importance': 'major_public', 'attribution': 'announcement'}}


def query_fixture():
    def item(key, title, decision, channels, positions):
        return {'id': key, 'title': title, 'summary': '摘要：' + title, 'rank': 1, 'rank_score': 0,
                'screening': {'decision': decision, 'channels': channels, 'event': {
                    'kind': 'policy', 'actor': title[:2], 'action': '宣布新措施', 'new_fact': '摘要：' + title,
                    'novelty': 'new_development', 'importance': 'major_public', 'attribution': 'reported'} if decision == 'keep' else None},
                'sources': [{'id': source, 'rank': rank, 'observed_timestamp': observed, 'heat': heat}
                            for source, rank, observed, heat in positions]}
    return {'items': [
        item('trade', '美国加征关税', 'keep', ['trade', 'regulation'], [('baidu', 1, NOW, '10'), ('weibo', 30, NOW - 120, '99999999')]),
        item('shipping', '航道受阻与AI物流', 'keep', ['supply', 'cost'], [('baidu', 3, NOW, '1'), ('weibo', 1, NOW + 60, '1')]),
        item('fandom', '明星红毯同框', 'exclude', [], [('baidu', 2, NOW, '9999999999')]),
        item('rumor', '关税传闻', 'review', [], [('weibo', 2, NOW + 120, '100')]),
    ]}


class FilterTests(unittest.TestCase):
    def test_scope_counts_ignore_decision_and_limit(self):
        result = filter_board(query_fixture(), decision='keep', q='关税', limit=1)
        self.assertEqual(result['counts'], {'keep': 1, 'review': 1, 'exclude': 0})
        self.assertEqual(result['candidate_total'], 4)
        self.assertEqual(result['matched_total'], 1)
        self.assertEqual(result['items'][0]['id'], 'trade')

    def test_channel_source_and_query_compose_without_extra_sources_affecting_rank(self):
        result = filter_board(query_fixture(), decision='keep', source='weibo', channel='cost', q='物流')
        self.assertEqual([row['id'] for row in result['items']], ['shipping'])
        self.assertEqual(result['counts'], {'keep': 1, 'review': 0, 'exclude': 0})
        self.assertEqual(result['items'][0]['rank_score'], round(1 / 61, 8))

    def test_platform_order_uses_rank_instead_of_raw_heat(self):
        result = filter_board(query_fixture(), source='weibo', decision='keep')
        self.assertEqual([row['id'] for row in result['items']], ['shipping', 'trade'])
        self.assertEqual(result['items'][0]['rank'], 1)
        self.assertEqual(result['items'][1]['rank'], 2)

    def test_latest_means_source_collection_time(self):
        result = filter_board(query_fixture(), sort='latest')
        self.assertEqual([row['id'] for row in result['items']][:2], ['rumor', 'shipping'])

    def test_unicode_casefold_search_is_literal_and_not_regex(self):
        self.assertEqual(filter_board(query_fixture(), q=' ａｉ ')['items'][0]['id'], 'shipping')
        self.assertEqual(filter_board(query_fixture(), q='[关税]')['items'], [])
        self.assertEqual(filter_board(query_fixture(), q="' OR 1=1 --")['items'], [])

    def test_empty_result_preserves_channels_and_counts(self):
        result = filter_board(query_fixture(), channel='funding')
        self.assertEqual(result['matched_total'], 0)
        self.assertEqual(result['counts'], {'keep': 0, 'review': 0, 'exclude': 0})
        self.assertEqual(len(result['channel_options']), 7)

    def test_limit_does_not_change_matching_total_or_mutate_cached_snapshot(self):
        fixture = query_fixture(); original = deepcopy(fixture)
        result = filter_board(fixture, limit=1)
        self.assertEqual(result['matched_total'], 4)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(fixture, original)


class ParserTests(unittest.TestCase):
    def test_baidu_reads_embedded_json_and_preserves_rank_heat_summary(self):
        rows = [{'word': '置顶新闻', 'isTop': True, 'index': 0},
                {'word': '美国宣布加征进口关税', 'index': 0, 'hotScore': '456789', 'desc': '贸易相关话题摘要'},
                {'word': '娱乐话题', 'index': 2, 'hotScore': 123},
                {'word': '推广内容', 'index': 3, 'is_ad': True}]
        raw = '<html><!--s-data:' + json.dumps({'data': {'cards': [{'content': rows}]}}, ensure_ascii=False) + '--></html>'
        parsed = parse_baidu(raw)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]['rank'], 1)
        self.assertEqual(parsed[1]['rank'], 3)
        self.assertEqual(parsed[0]['heat'], '456789')
        self.assertEqual(parsed[0]['summary'], '贸易相关话题摘要')
        self.assertTrue(parsed[0]['url'].startswith('https://www.baidu.com/s?wd='))

    def test_baidu_structure_changes_fail_explicitly(self):
        for raw in ('<html>请登录</html>', '<!--s-data:{invalid}-->', '<!--s-data:{"data":{"cards":[]}}-->'):
            with self.subTest(raw=raw), self.assertRaises(ProviderError):
                parse_baidu(raw)

    def test_weibo_preserves_platform_positions_and_skips_ads(self):
        result = parse_weibo({'data': {'realtime': [
            {'word': '新闻一', 'rank': 0, 'num': 98765}, {'word': '广告', 'rank': 1, 'is_ad': 1},
            {'word': '新闻三', 'rank': 2, 'raw_hot': 45678}]}})
        self.assertEqual([row['rank'] for row in result], [1, 3])
        self.assertEqual(result[1]['heat'], '45678')

    def test_weibo_does_not_turn_forbidden_or_login_into_empty_success(self):
        for payload in ({'error': 'Forbidden'}, {'data': '<login>'}, {'data': {'realtime': []}}, []):
            with self.subTest(payload=payload), self.assertRaises(ProviderError):
                parse_weibo(payload)

    def test_merging_uses_normalized_exact_titles_and_keeps_both_heat_scales(self):
        records = {'baidu': {'items': [board_row('美国 加征关税', 3)], 'collected_at': 't1'},
                   'weibo': {'items': [{**board_row('#美国加征关税#', 2), 'heat': '99999999'}], 'collected_at': 't2'}}
        merged = merge_topics(records)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]['sources']), 2)
        self.assertEqual(merged[0]['rank_score'], round(1 / 63 + 1 / 62, 8))
        self.assertEqual([entry['heat'] for entry in merged[0]['sources']], ['123456', '99999999'])
        self.assertEqual(normalized_title('＃新闻！＃'), '新闻')

    def test_different_events_are_not_merged_by_shared_keywords(self):
        self.assertEqual(len(merge_topics({'baidu': {'items': [board_row('美国加征关税'), board_row('美国取消关税', 2)], 'collected_at': 't'}})), 2)


class RuleScreenTests(unittest.TestCase):
    def test_trade_and_geopolitics_do_not_need_financial_keywords(self):
        for title, channel in [('美国宣布加征进口关税', 'trade'), ('伊朗宣布封锁霍尔木兹海峡', 'supply'),
                               ('红海主要航道受袭中断通航', 'cost'), ('以色列宣布达成停火协议', 'risk')]:
            with self.subTest(title=title):
                result = rule_screen(topic(title))
                self.assertEqual(result['decision'], 'keep')
                self.assertIn(channel, result['channels'])
                self.assertIn(result['evidence'], title + '\n')

    def test_covers_macro_commodities_disasters_and_technology(self):
        for title in ('中国人民银行宣布降准', '美国宣布对汽车加征关税', '台风导致多地主要港口停航', '苹果发布首款折叠屏iPhone Duo'):
            with self.subTest(title=title):
                self.assertEqual(rule_screen(topic(title))['decision'], 'keep')

    def test_pure_fandom_is_excluded_but_brand_penalties_are_kept(self):
        self.assertEqual(rule_screen(topic('两位明星红毯同框引粉丝热议'))['decision'], 'exclude')
        self.assertEqual(rule_screen(topic('小米宣布召回116887辆SU7标准版汽车'))['decision'], 'keep')

    def test_ambiguous_or_denied_events_remain_review(self):
        for title in ('网传伊朗封锁霍尔木兹海峡', '伊朗否认封锁霍尔木兹海峡', '美国是否加征关税？',
                      '人造黄金会不会击穿金价', '某国领导人会见嘉宾', '青海发生小范围地震'):
            with self.subTest(title=title):
                self.assertEqual(rule_screen(topic(title))['decision'], 'review')

    def test_history_does_not_become_a_current_war_signal(self):
        item = topic('台湾抗日历史纪念', '记者重走抗日历史现场，回顾乙未战争与历史文化。')
        self.assertEqual(rule_screen(item)['decision'], 'review')

    def test_infrastructure_is_not_missed_for_lack_of_finance_label(self):
        self.assertEqual(rule_screen(topic('国家水网到底有多硬核', '重大水利工程正在建设，将涉及供水与发电。'))['decision'], 'exclude')
        self.assertEqual(rule_screen(topic('国家重大水利工程正式开工建设'))['decision'], 'keep')


class ModelScreenTests(unittest.TestCase):
    def test_valid_screening(self):
        item = topic()
        self.assertEqual(validate_screening(json.dumps({'items': [model_row(item)]}), [item])[0]['decision'], 'keep')

    def test_rejects_missing_duplicate_and_added_topics(self):
        item = topic()
        for rows in ([], [model_row(item), model_row(item)], [{**model_row(item), 'id': 'unknown'}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_screening(json.dumps({'items': rows}), [item])

    def test_rejects_invented_quotes_and_incoherent_decisions(self):
        item = topic()
        for overrides in ({'evidence': '未提供的其他新闻'}, {'decision': 'exclude'}, {'channels': []},
                          {'channels': ['trade', 'trade']}, {'channels': ['unknown']}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                validate_screening(json.dumps({'items': [{**model_row(item), **overrides}]}), [item])


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.directory.name) / 'test.db'); auth.initialize()
        self.store = ResearchStore(auth); self.store.initialize()
        self.completion = AsyncMock()
        self.service = HotNewsBoardService(self.store, self.completion, clock=lambda: NOW)
        self.config = patch('backend.hot_news_board.read_config', return_value={}); self.config.start()
        self.source_config = patch('backend.hot_news_board.configured_sources', return_value={}); self.source_config.start()

    async def asyncTearDown(self):
        self.source_config.stop(); self.config.stop(); self.directory.cleanup()

    async def fetch(self, source):
        return [board_row('美国宣布加征进口关税'), board_row('明星红毯同框', 2), board_row('某地普通生活话题', 3)]

    async def test_read_never_fetches_or_invokes_model(self):
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock()) as fetch:
            self.assertEqual(self.service.feed()['items'], [])
        fetch.assert_not_awaited(); self.completion.assert_not_awaited()

    async def test_refresh_fetches_all_topic_sources_then_screens_and_caches(self):
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock(side_effect=self.fetch)) as fetch:
            result = await self.service.refresh()
            await self.service.refresh()
        self.assertEqual(fetch.await_count, len(SOURCES))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['counts'], {'keep': 1, 'review': 1, 'exclude': 1})
        self.assertEqual(len(result['items'][0]['sources']), len(SOURCES))
        self.completion.assert_not_awaited()

    async def test_partial_source_failure_is_explicit_with_working_source(self):
        async def fetch(source):
            if source == 'weibo':
                raise ProviderError('微博热搜暂不可读取（HTTP 403）')
            return [board_row()]
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock(side_effect=fetch)):
            result = await self.service.refresh()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['sources'][1]['status'], 'unavailable')
        self.assertIn('403', result['sources'][1]['error'])

    async def test_both_failed_is_unavailable_and_does_not_fabricate_news(self):
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock(side_effect=ProviderError('unavailable'))):
            result = await self.service.refresh()
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['items'], [])

    async def test_failure_retains_old_timestamp_and_expired_items_disappear(self):
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock(side_effect=self.fetch)):
            await self.service.refresh()
        self.service.clock = lambda: NOW + TTL + 1
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock(side_effect=ProviderError('blocked'))):
            result = await self.service.refresh()
        self.assertEqual(result['sources'][0]['status'], 'stale')
        self.assertEqual(result['items'][0]['sources'][0]['observed_timestamp'], NOW)
        self.assertTrue(result['items'][0]['sources'][0]['stale'])
        self.service.clock = lambda: NOW + MAX_STALE + 1
        self.assertEqual(self.service.feed()['items'], [])
        self.assertEqual(self.service.feed()['status'], 'unavailable')

    async def test_expired_one_source_removes_its_rank_boost(self):
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock(side_effect=self.fetch)):
            await self.service.refresh()
        record = self.store.get('hot_news_board', FEED_KEY)
        for item in record['topics']:
            for source in item['sources'][1:]: source['observed_timestamp'] = NOW - MAX_STALE
        self.store.put('hot_news_board', FEED_KEY, record)
        result = self.service.feed()
        self.assertEqual(len(result['items'][0]['sources']), 1)
        self.assertEqual(result['items'][0]['rank_score'], round(1 / 61, 8))

    async def test_refresh_lease_prevents_duplicate_collection(self):
        self.store.claim(digest([VERSION, 'refresh']), 900)
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock()) as fetch:
            with self.assertRaises(HTTPException) as error:
                await self.service.refresh()
        self.assertEqual(error.exception.status_code, 409)
        fetch.assert_not_awaited()

    async def test_model_results_are_shared_cached_by_content_and_model(self):
        async def complete(messages, **kwargs):
            data = json.loads(messages[1]['content'])['topics']
            return {'content': json.dumps({'items': [model_row(item) for item in data]}), 'model': 'fixture'}
        self.completion.side_effect = complete
        with patch('backend.hot_news_board.read_config', return_value={'model': 'fixture', 'base_url': 'https://example.com', 'api_key': 'not-a-real-key'}):
            first, _ = await self.service.screen([topic()])
            await self.service.screen([topic()])
            self.assertEqual(self.completion.await_count, 1)
            await self.service.screen([topic(summary='新的范围说明')])
            self.assertEqual(self.completion.await_count, 2)
        self.assertEqual(first[0]['screening']['method'], 'model')
        with patch('backend.hot_news_board.read_config', return_value={'model': 'different', 'api_key': 'not-a-real-key'}):
            await self.service.screen([topic()])

    async def test_model_cache_separates_platform_summary_from_reported_excerpt(self):
        async def complete(messages, **kwargs):
            data = json.loads(messages[1]['content'])['topics']
            return {'content': json.dumps({'items': [model_row(item) for item in data]}), 'model': 'fixture'}
        self.completion.side_effect = complete
        plain = topic()
        reported = {**topic(), 'sources': [{'id': 'tushare_cls', 'kind': 'news'}]}
        with patch('backend.hot_news_board.read_config', return_value={'model': 'fixture', 'api_key': 'fixture'}):
            await self.service.screen([plain])
            await self.service.screen([reported])
            await self.service.screen([plain])
        self.assertEqual(self.completion.await_count, 2)
        self.assertEqual(self.completion.await_count, 3)

    async def test_invalid_model_output_falls_back_without_losing_review_items(self):
        self.completion.return_value = {'content': '{invalid'}
        with patch('backend.hot_news_board.read_config', return_value={'model': 'fixture', 'api_key': 'not-a-real-key'}):
            result, warnings = await self.service.screen([topic(), topic('某地普通生活话题')])
        self.assertEqual([item['screening']['decision'] for item in result], ['keep', 'review'])
        self.assertTrue(warnings)
        self.assertTrue(all(item['screening']['method'] == 'rules' for item in result))
        self.assertEqual(self.store.list('hot_news_screen'), [])

    async def test_batches_are_bounded_and_complete(self):
        sizes = []
        async def complete(messages, **kwargs):
            data = json.loads(messages[1]['content'])['topics']; sizes.append(len(data))
            return {'content': json.dumps({'items': [model_row(item) for item in data]})}
        self.completion.side_effect = complete
        with patch('backend.hot_news_board.read_config', return_value={'model': 'fixture', 'api_key': 'not-a-real-key'}):
            result, _ = await self.service.screen([topic(f'美国宣布加征进口关税，涉及商品编号{i}') for i in range(23)])
        self.assertEqual(sorted(sizes), [3, 5, 5, 5, 5])
        self.assertEqual(len(result), 23)


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_forbidden_is_reported_and_redirects_not_followed(self):
        for status in (403, 302, 429):
            async def handler(request):
                return httpx.Response(status, headers={'Location': 'https://passport.weibo.com/login'}, json={'error': 'blocked'})
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            with patch('backend.hot_news_board.httpx.AsyncClient', return_value=client) as factory:
                with self.subTest(status=status), self.assertRaises(ProviderError):
                    await fetch_board('weibo')
                self.assertFalse(factory.call_args.kwargs['follow_redirects'])


class PublicReadTests(unittest.TestCase):
    def test_cached_board_is_public_and_get_has_no_external_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            app.state.auth_store = AuthStore(Path(directory) / 'test.db')
            try:
                with TestClient(app, base_url='http://localhost') as client:
                    with patch('backend.hot_news_board.fetch_board', new=AsyncMock()) as fetch:
                        response = client.get('/api/hot-news')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    fetch.assert_not_awaited()
            finally:
                del app.state.auth_store

    def test_filter_parameters_are_validated_and_get_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            app.state.auth_store = AuthStore(Path(directory) / 'test.db')
            try:
                with TestClient(app, base_url='http://localhost') as client:
                    with patch.object(app.state.hot_news, 'feed', return_value=query_fixture()), \
                         patch('backend.hot_news_board.fetch_board', new=AsyncMock()) as fetch:
                        response = client.get('/api/hot-news', params={'decision': 'keep', 'source': 'baidu', 'channel': 'trade', 'q': '关税', 'limit': 1})
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()['items'][0]['id'], 'trade')
                        self.assertEqual(response.json()['filters']['source'], 'baidu')
                        for params in ({'source': 'unknown'}, {'channel': 'stock'}, {'decision': 'bull'}, {'sort': 'profit'},
                                       {'limit': 0}, {'limit': 101}, {'q': 'a' * 81}):
                            with self.subTest(params=params):
                                self.assertEqual(client.get('/api/hot-news', params=params).status_code, 422)
                        fetch.assert_not_awaited()
            finally:
                del app.state.auth_store


if __name__ == '__main__':
    unittest.main()
