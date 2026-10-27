import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch
from backend.auth import AuthStore
from backend.research_store import ResearchStore
from backend.hot_news_events import editorial_reason, rule_event, validate_event
from backend.hot_news_board import HotNewsBoardService, FEED_KEY, REFRESH_KEY, VERSION, digest, public_board, rule_screen
from tests.test_hot_news_board import topic, board_row, NOW, query_fixture, model_row


class NewsValueTests(unittest.TestCase):
    def test_concrete_major_developments_are_candidates(self):
        titles = ['美国宣布加征进口关税', '伊朗宣布封锁霍尔木兹海峡', '中国人民银行宣布降准',
                  '小米宣布召回116887辆SU7标准版汽车', '三部门启动尊界V800刹车踏板支架断裂联合调查',
                  '苹果发布首款折叠屏iPhone Duo', '媒体报道iPhone Duo用户反馈屏幕裂开',
                  'CrowdStrike更新引发全球大规模宕机', '台风导致多地主要港口停航',
                  '奥迪中国宣布双品牌架构正式分家']
        for title in titles:
            with self.subTest(title=title):
                result = rule_screen(topic(title))
                self.assertEqual(result['decision'], 'keep')
                validate_event(result['event'], title + '\n')
        self.assertEqual(rule_event(topic(titles[6]))['event']['attribution'], 'reported_claim')

    def test_discussion_is_not_promoted_by_possible_economic_connections(self):
        cases = [
            ('“看不见的外贸”畅跑全球', 'AI企业通过API提供服务，可能改变算力、电力与跨境贸易。'),
            ('超长预售45天原来是在等退货回来', '女装店依靠退货库存周转，消费者讨论物流和平台。'),
            ('冷冻烘焙卖3块精品面包卖30块', '成本结构不同，可能影响食品饮料行业。'),
            ('人造古镇为啥集体哑火了', '多年累计投资超过500亿元，讨论文旅经营问题。'),
            ('深圳禁鸣喇叭那么多车咋知道谁按的', '用麦克风阵列定位车辆，也许带动设备采购。'),
            ('有KTV的列车火了 包厢点唱68元一小时', '旅游服务体验受到关注，若推广可能影响铁路需求。'),
            ('匡威被年轻人抛弃是早晚问题', '讨论品牌审美和鞋服需求。'),
            ('新型芯片实现量产', '没有公司、型号、规模和发布时间。'),
            ('国际原油价格小幅上涨', '例行日度报价，没有新事件或异常变化。'),
            ('这款手机到底值不值得买', '个人使用体验。'),
            ('海外AI巨头加码算力，某算力ETF月内净流入额领跑', '借产业报道宣传基金资金流入。'),
            ('CPO概念震荡反弹，某科技股涨停', '盘中板块与股价表现。'),
            ('食品饮料板块异动拉升', '盘中行情。'),
            ('光大期货1027黄金点评：金价小幅反弹', '例行投资点评。'),
            ('港股早报：某AI公司提交IPO申请', '多事件汇总，不能因内嵌大事保留整篇。'),
            ('中国石油前三季度乙烯产量同比增长创历史同期新高', '周期统计宣传。'),
            ('“顾客是上帝 员工也是人”', '知名连锁品牌谈服务理念。'),
            ('大和：SK海力士撤回Solidigm上市计划，维持买入评级', '研报解读，不能以其中引用的公司消息保留整篇评级。'),
            ('数字员工持证上岗 企业AI迈向组织提效', '论坛报道与应用趋势宣传。'),
        ]
        for title, summary in cases:
            with self.subTest(title=title):
                self.assertNotEqual(rule_screen(topic(title, summary))['decision'], 'keep')

    def test_schema_requires_new_fact_and_significance_not_just_market_path(self):
        item = topic(); good = model_row(item)
        from backend.hot_news_board import validate_screening
        for change in ({'novelty': 'background'}, {'importance': 'minor'}, {'actor': '不存在的公司'}, {'new_fact': '材料以外的重要事件已发生'}):
            row = {**good, 'event': {**good['event'], **change}}
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_screening(json.dumps({'items': [row]}), [item])
        bad = topic('“看不见的外贸”畅跑全球', '只是背景讨论，没有刚发生的事件。')
        with self.assertRaises(ValueError):
            validate_screening(json.dumps({'items': [model_row(bad)]}), [bad])

    def test_public_payload_keeps_only_accepted_events_and_hides_legacy_screenings(self):
        fixture = query_fixture()
        result = public_board(fixture)
        self.assertEqual({r['id'] for r in result['items']}, {'trade', 'shipping'})
        self.assertEqual(result['counts'], {'keep': 2})
        self.assertEqual(result['candidate_total'], 2)
        fixture['items'][0]['screening'].pop('event')
        self.assertEqual([r['id'] for r in public_board(fixture)['items']], ['shipping'])
        self.assertEqual(len(fixture['items']), 4)


class RefreshTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.tmp.name) / 'test.db'); auth.initialize()
        self.store = ResearchStore(auth); self.store.initialize()
        self.service = HotNewsBoardService(self.store, AsyncMock(), clock=lambda: NOW)
        self.config = patch('backend.hot_news_board.read_config', return_value={}); self.config.start()

    async def asyncTearDown(self):
        await self.service.close(); self.config.stop(); self.tmp.cleanup()

    async def test_slow_collection_returns_immediately_and_joiners_do_not_duplicate_work(self):
        gate = asyncio.Event()
        async def collect(source):
            await gate.wait()
            return [board_row(), board_row('超长预售45天原来是在等退货回来', 2)]
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock(side_effect=collect)) as fetch:
            response = await asyncio.wait_for(self.service.request_refresh(), .8)
            self.assertEqual(response['refresh']['status'], 'running')
            other = HotNewsBoardService(self.store, AsyncMock(), clock=lambda: NOW)
            joined = await asyncio.wait_for(other.request_refresh(), .8)
            self.assertEqual(joined['refresh']['status'], 'running')
            self.assertIsNone(other.task)
            gate.set(); await asyncio.wait_for(self.service.task, 3)
        self.assertEqual(fetch.await_count, 2)
        result = public_board(self.service.feed())
        self.assertEqual(result['refresh']['status'], 'completed')
        self.assertEqual([r['title'] for r in result['items']], ['美国宣布加征进口关税'])
        self.assertIsNone(self.store.get('lease', digest([VERSION, 'refresh'])))

    async def test_background_failure_is_retryable_and_does_not_destroy_previous_snapshot(self):
        self.store.put('hot_news_board', FEED_KEY, {'version': 'older', 'topics': [], 'sources': {}, 'refreshed_at': NOW})
        with patch.object(self.service, 'screen', side_effect=RuntimeError('fixture failure')), patch('backend.hot_news_board.fetch_board', new=AsyncMock(return_value=[board_row()])):
            await self.service.request_refresh(); await self.service.task
        self.assertEqual(self.service.feed()['refresh']['status'], 'error')
        self.assertEqual(self.store.get('hot_news_board', FEED_KEY)['version'], 'older')
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock(return_value=[board_row()])):
            await self.service.request_refresh(); await self.service.task
        self.assertEqual(self.service.feed()['refresh']['status'], 'completed')

    async def test_shutdown_releases_lease_and_reports_interruption(self):
        gate = asyncio.Event()
        async def collect(source): await gate.wait()
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock(side_effect=collect)):
            await self.service.request_refresh(); await asyncio.sleep(.03); await self.service.close()
        self.assertEqual(self.service.refresh_status()['status'], 'error')
        self.assertIsNone(self.store.get('lease', digest([VERSION, 'refresh'])))

    async def test_old_selection_version_is_rescreened_even_within_ttl(self):
        with patch('backend.hot_news_board.fetch_board', new=AsyncMock(return_value=[board_row()])) as fetch:
            await self.service.refresh()
            record = self.store.get('hot_news_board', FEED_KEY); record['version'] = 'old'
            self.store.put('hot_news_board', FEED_KEY, record)
            await self.service.request_refresh(); await self.service.task
        self.assertEqual(fetch.await_count, 4)

    async def test_expired_lease_is_not_displayed_as_permanent_progress(self):
        self.store.put('hot_news_board', REFRESH_KEY, {'token': 'old', 'status': 'running'})
        self.assertEqual(self.service.refresh_status()['status'], 'error')


if __name__ == '__main__': unittest.main()
