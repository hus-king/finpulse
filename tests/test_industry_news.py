import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import Response

from backend.auth import AuthStore
from backend.research_store import ResearchStore
from backend.providers import ProviderError
from backend.industry import build_topics, match_industry, select_analyses
from backend.industry_news import IndustryNewsService
from backend.news_cleaning import SHANGHAI, clean_report
from backend.research import ResearchService
from backend.recommendations import build_digest
from backend.research_api import analyze_saved

STOCK = {'code': '601857', 'name': '中国石油', 'exchange': 'SH', 'industry': 'A 股'}
ANALYSIS = {'sentiment_score': 0, 'summary': '原油供需出现变化', 'causal_chain': ['来源披露原油价格变化', '可能影响相关业务经营条件', '市场预期仍需进一步核验'], 'uncertainty': '行业间接关联，缺少主营收入占比'}
REPLY = {'content': json.dumps({**ANALYSIS, 'assessment':'positive' if ANALYSIS['sentiment_score'] else 'neutral', 'confidence':'medium', 'horizon':'medium', 'positive_factors':['公司披露业务进展'] if ANALYSIS['sentiment_score'] else [], 'negative_factors':[], 'watch_points':['执行进度'], 'sentiment_score':ANALYSIS['sentiment_score']*25}, ensure_ascii=False), 'model': 'test', 'elapsed_ms': 1, 'usage': {}, 'request_id': 'test'}


def news(title='国际原油价格回落', url='https://example.com/oil', day='2026-10-08', **extra):
    return {'title': title, 'content': '国际原油价格回落，布伦特原油期货价格下跌，市场正在观察石油供需变化与相关政策。', 'url': url, 'published_date': day, **extra}


class IndustryCleaningTests(unittest.TestCase):
    def clean(self, rows, company=None, extracts=None):
        groups = [{'stock': '中国石油', 'news_scope': 'company', 'response': {'results': company or []}},
                  {'stock': '中国石油', 'news_scope': 'industry', 'response': {'results': rows}}]
        return clean_report({'date_range': ['2026-10-01', '2026-10-08'], 'searches': groups}, extracts,
            {'中国石油': {'code': '601857', 'aliases': ['中国石油', '601857']}}, industry_profile={'industry': '石油行业', 'status': 'ok'})

    def test_industry_news_without_company_name_has_auditable_relation(self):
        result = self.clean([news()])
        row = result['items'][0]
        self.assertEqual(row['news_scope'], 'industry')
        self.assertIn('原油价格', row['related_factors'])
        self.assertIn('石油行业', row['relevance_reason'])
        self.assertEqual(result['summary']['industry_events'], 1)

    def test_industry_does_not_relax_date_source_or_relevance_checks(self):
        rows = [news(day='2026-09-01'), news(url='https://example.com/missing', published_date=None),
                news(title='某公司招聘新员工', url='https://example.com/jobs'),
                news(url='https://data.eastmoney.com/zjlx/601857.html'), news(url='https://example.com/conflict'),
                news(url='https://www.investing.com/commodities/crude-oil'), news(url='https://data.eastmoney.com/notice/601857.html')]
        result = self.clean(rows, extracts={'https://example.com/conflict': '国际原油价格回落\n发布时间：2026-09-30\n国际原油价格下跌，石油供需出现变化，市场仍需持续观察供给变化。'})
        self.assertFalse(result['items'])
        reasons = {reason for row in result['audit'] for reason in row['reason_codes']}
        self.assertTrue({'out_of_range', 'missing_date', 'not_industry_event', 'quote_page'} <= reasons)

    def test_industry_article_in_company_query_is_not_implicitly_admitted(self):
        result = self.clean([], company=[news()])
        self.assertFalse(result['items'])
        self.assertIn('not_primary_entity', result['audit'][0]['reason_codes'])

    def test_duplicate_company_and_industry_sources_keep_direct_scope(self):
        row = news(title='中国石油发布经营进展')
        result = self.clean([row], company=[row])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['news_scope'], 'company')
        self.assertEqual(result['summary']['merged'], 1)


class IndustryClassificationTests(unittest.TestCase):
    def test_classification_level_suffix_is_not_a_required_news_keyword(self):
        topics = build_topics('专用设备Ⅱ')
        self.assertNotIn('Ⅱ', topics[0]['query'])
        self.assertEqual(topics[0]['industry'], '专用设备Ⅱ')
        relation = match_industry('专用设备Ⅱ', '专用设备行业订单增长', '专用设备行业订单增长，生产需求增加，设备制造商正在调整产能。')
        self.assertIn('行业动态', relation['factors'])

    def test_rail_and_automation_topics_have_their_own_factors(self):
        for industry, title, body, term in (
            ('轨交设备Ⅱ', '全国铁路固定资产投资同比增长', '全国铁路固定资产投资同比增长，动车组采购订单增加，轨道交通建设需求改善。', '动车组'),
            ('自动化设备', '工业机器人销量增长带动设备更新', '工业机器人销量增长，制造业设备更新需求增加，自动化设备订单改善。', '工业机器人'),
        ):
            with self.subTest(industry=industry):
                topics = build_topics(industry)
                self.assertEqual(len(topics), 2)
                self.assertIn(term, topics[1]['query'])
                self.assertTrue(match_industry(industry, title, body)['factors'])
                self.assertFalse(match_industry(industry, '互联网公司发布招聘计划', body)['factors'])


    def test_sector_mentions_do_not_turn_policing_or_recruitment_into_equipment_news(self):
        for industry, title, body in (
            ('轨交设备Ⅱ', '铁路警方调整投资诈骗案件举报方式', '铁路警方调整投资诈骗案件举报方式，旅客可通过新的窗口进行举报，办理流程详见通知。'),
            ('自动化设备', '制造业招聘需求增长', '制造业招聘需求增长，办公室调整面试安排，招聘人员公布了新的岗位要求。'),
        ):
            with self.subTest(industry=industry):
                self.assertFalse(match_industry(industry, title, body)['factors'])


    def test_generic_business_headline_requires_sector_and_business_evidence_in_main_body(self):
        title = '新产线投产，制造企业扩大投资'
        body = '企业新产线投产。轨道交通控制设备的生产能力提升，列车装备订单增加。'
        self.assertIn('轨交装备需求', match_industry('轨交设备Ⅱ', title, body)['factors'])
        self.assertFalse(match_industry('轨交设备Ⅱ', title, '企业新产线投产，食品加工订单增加。')['factors'])
        self.assertFalse(match_industry('轨交设备Ⅱ', title, '企业调整食堂。轨道交通协会举办招聘面试。')['factors'])

    def test_device_market_growth_and_iot_policy_have_event_and_factor_context(self):
        relation = match_industry('自动化设备', '角接触轴承市场展望：自动化需求加速增长', '自动化设备订单增长，工业机器人应用需求增长，轴承企业投资扩大生产。')
        self.assertTrue(relation['factors'])
        relation = match_industry('通信设备', '九部门物联网行动方案重塑通信产业', '行动方案推动物联网连接规模增长，通信设备产业需求扩大。')
        self.assertTrue(relation['factors'])


class IndustryResearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.temp.name) / 'research.db')
        auth.initialize()
        self.store = ResearchStore(auth)
        self.store.initialize()
        self.model = AsyncMock(return_value=REPLY)
        self.service = ResearchService(self.store, self.model)
        self.service.catalog.items[STOCK['code']] = STOCK
        business_patch = patch('backend.providers.business_profile', new=AsyncMock(return_value={'code':STOCK['code'],'main_business':'测试主营业务','source':'test'}))
        business_patch.start()
        self.addCleanup(business_patch.stop)
        self.profile_patch = patch('backend.providers.stock_profile', new=AsyncMock(return_value={**STOCK, 'industry': '石油行业', 'source': 'test'}))
        self.profile_patch.start()
        self.addCleanup(self.profile_patch.stop)

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def collect(self, industry_response=None, source_error=None):
        day = datetime.now(SHANGHAI).date().isoformat()
        company = [news(title=f'中国石油披露经营业务进展 {i}', url=f'https://example.com/company{i}', day=day,
            content=f'中国石油披露经营业务进展 {i}，公司介绍生产经营安排与业务情况，实际执行效果仍需进一步核验。') for i in range(3)]
        with patch('backend.providers.search_news', new=AsyncMock(return_value={'results': company})), \
             patch('backend.providers.akshare_news', new=AsyncMock(return_value={'results': []})), \
             patch('backend.providers.tavily', new=AsyncMock(return_value={'results': []})), \
             patch('backend.providers.daily_market', new=AsyncMock(return_value={'candles': [], 'price': None})), \
             patch('backend.providers.search_industry_news', new=AsyncMock(side_effect=source_error, return_value=industry_response or {'results': [news(day=day)]})):
            return await self.service.collect(STOCK['code'], 7, 3, False)

    async def test_full_collection_reserves_industry_budget_and_passes_profile_to_model(self):
        result = await self.collect()
        rows = result['news']
        self.assertEqual(len(rows), 4)
        selected = [row for row in rows if row['analysis_status'] == 'completed']
        self.assertEqual(len(selected), 3)
        self.assertEqual(sum(row['news_scope'] == 'industry' for row in selected), 1)
        payloads = [json.loads(call.args[0][1]['content']) for call in self.model.await_args_list]
        payload = next(row for row in payloads if row['news_scope'] == 'industry')
        self.assertEqual(payload['industry_profile']['industry'], '石油行业')
        self.assertIn('原油价格', payload['related_factors'])
        self.assertTrue(payload['relevance_reason'])
        self.assertEqual(result['industry_profile']['status'], 'ok')

    async def test_new_company_news_survives_industry_source_failure(self):
        day = datetime.now(SHANGHAI).date().isoformat()
        self.store.put('dashboard', STOCK['code'], {'news': [{'id': 'old', 'title': '国际原油价格回落', 'url': 'https://example.com/old', 'time': day,
            'news_scope': 'industry', 'industry': '石油行业', 'related_factors': ['原油价格'], 'relevance_reason': '旧关联', 'content': '旧的原油供需资料',
            'score': 0, 'analysis': ANALYSIS, 'analysis_status': 'completed', 'tag': '行业新闻', 'text_source': 'search_fragments', 'date_status': 'metadata_only', 'sources': []}]})
        result = await self.collect(source_error=ProviderError('行业源失败'))
        self.assertEqual(sum(row.get('news_scope', 'company') == 'company' for row in result['news']), 3)
        self.assertTrue(next(row for row in result['news'] if row['id'] == 'old')['stale'])
        self.assertTrue(any('行业源失败' in warning for warning in result['pipeline']['warnings']))

    async def test_stale_shared_search_material_is_labelled_and_keeps_prior_analysis(self):
        first = await self.collect()
        original = next(row for row in first['news'] if row['news_scope'] == 'industry')
        self.service.industry_news.clock = lambda: time.time() + 1801
        second = await self.collect(source_error=ProviderError('刷新失败'))
        row = next(row for row in second['news'] if row['url'] == original['url'])
        self.assertTrue(row['stale'])
        self.assertEqual(row['analysis'], original['analysis'])
        self.assertEqual(row['score'], original['score'])

    async def test_company_news_is_published_while_industry_profile_is_pending(self):
        gate, entered, visible = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def profile(*args):
            entered.set()
            await gate.wait()
            return {**STOCK, 'industry': '石油行业', 'source': 'test'}
        def progress(stage):
            snapshot = self.store.get('dashboard', STOCK['code']) or {}
            if snapshot.get('news') and snapshot['pipeline']['stages']['news'] == 'ready':
                visible.set()
        day = datetime.now(SHANGHAI).date().isoformat()
        with patch('backend.providers.stock_profile', new=AsyncMock(side_effect=profile)), \
             patch('backend.providers.search_news', new=AsyncMock(return_value={'results': [news(title='中国石油披露经营业务进展', day=day)]})), \
             patch('backend.providers.akshare_news', new=AsyncMock(return_value={'results': []})), \
             patch('backend.providers.tavily', new=AsyncMock(return_value={'results': []})), \
             patch('backend.providers.search_industry_news', new=AsyncMock(return_value={'results': []})), \
             patch('backend.providers.daily_market', new=AsyncMock(return_value={'candles': [], 'price': None})):
            task = asyncio.create_task(self.service.collect(STOCK['code'], 7, 3, False, progress))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                await asyncio.wait_for(visible.wait(), 1)
                self.assertFalse(task.done())
                self.assertEqual(self.store.get('dashboard', STOCK['code'])['news'][0]['news_scope'], 'company')
            finally:
                gate.set()
                await task

    async def test_cancel_during_industry_wait_preserves_old_company_and_industry_material(self):
        day = datetime.now(SHANGHAI).date().isoformat()
        previous = [{'id': scope, 'news_scope': scope, 'title': '中国石油原有材料', 'url': 'https://example.com/old/' + scope,
                     'content': '原有来源材料', 'industry': '石油行业', 'time': day, 'score': 0, 'analysis': ANALYSIS, 'analysis_status': 'completed'} for scope in ('company', 'industry')]
        self.store.put('dashboard', STOCK['code'], {'news': previous})
        gate, visible = asyncio.Event(), asyncio.Event()
        async def profile(*args):
            await gate.wait()
            return {**STOCK, 'industry': '石油行业', 'source': 'test'}
        def progress(stage):
            if stage == '公司新闻已就绪，行业新闻继续检索':
                visible.set()
        with patch('backend.providers.stock_profile', new=AsyncMock(side_effect=profile)), \
             patch('backend.providers.search_news', new=AsyncMock(side_effect=ProviderError('公司源失败'))), \
             patch('backend.providers.akshare_news', new=AsyncMock(side_effect=ProviderError('公司源失败'))), \
             patch('backend.providers.search_industry_news', new=AsyncMock(return_value={'results': []})), \
             patch('backend.providers.daily_market', new=AsyncMock(side_effect=ProviderError('行情失败'))):
            task = asyncio.create_task(self.service.collect(STOCK['code'], 7, 3, False, progress))
            try:
                await asyncio.wait_for(visible.wait(), 1)
                early = self.store.get('dashboard', STOCK['code'])
                self.assertEqual({row['id'] for row in early['news']}, {'company', 'industry'})
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                final = self.store.get('dashboard', STOCK['code'])
                self.assertEqual({row['id'] for row in final['news']}, {'company', 'industry'})
                self.assertTrue(all(row['analysis'] == ANALYSIS for row in final['news']))
            finally:
                gate.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_company_and_profile_versions_isolate_analysis_cache(self):
        article = {'title': '国际原油价格回落', 'content': '原油价格下跌，供需变化待核验。', 'time': '2026-10-08', 'url': 'https://example.com/oil',
            'text_source': 'search_fragments', 'date_status': 'metadata_only', 'news_scope': 'industry', 'industry': '石油行业', 'related_factors': ['原油价格'], 'relevance_reason': '行业关联'}
        first = {**STOCK, 'industry_profile': {'industry': '石油行业', 'version': 'v1'}}
        await self.service.analyze_document(first, article)
        await self.service.analyze_document(first, article)
        await self.service.analyze_document({**first, 'industry_profile': {'industry': '石油行业', 'version': 'v2'}}, article)
        await self.service.analyze_document({**first, 'code': '600028', 'name': '中国石化'}, article)
        self.assertEqual(self.model.await_count, 3)

    async def test_manual_analysis_survives_stale_search_refresh(self):
        first = await self.collect()
        row = next(row for row in first['news'] if row['news_scope'] == 'industry')
        row.update(analysis=None, score=None, analysis_status='pending')
        row.pop('analysis_prompt_version', None)
        self.store.put('dashboard', STOCK['code'], first)
        reply = await analyze_saved(STOCK['code'], row['id'], Response(), user={'id': 'reader'}, research=self.service)
        saved = next(row for row in self.store.get('dashboard', STOCK['code'])['news'] if row['news_scope'] == 'industry')
        self.assertEqual(saved.get('analysis_prompt_version'), reply['prompt_version'])
        self.service.industry_news.clock = lambda: time.time() + 1801
        result = await self.collect(source_error=ProviderError('刷新失败'))
        retained = next(row for row in result['news'] if row['news_scope'] == 'industry')
        self.assertTrue(retained['stale'])
        self.assertEqual(retained['analysis'], saved['analysis'])

    async def test_digest_deduplicates_industry_news_and_preserves_each_company_impact(self):
        self.store.put('watchlist', 'list', ['601857', '600028'], 'reader')
        for code, name, score in [('601857', '中国石油', 1), ('600028', '中国石化', -1)]:
            self.store.put('dashboard', code, {'stock': {'code': code, 'name': name}, 'as_of': '2026-10-08T10:00:00+08:00', 'news': [
                {'id': code, 'title': '国际原油价格回落', 'url': 'https://example.com/oil', 'time': '2026-10-08', 'score': score,
                 'news_scope': 'industry', 'industry': '石油行业', 'related_factors': ['原油价格'], 'relevance_reason': '材料涉及原油价格',
                 'analysis': {**ANALYSIS, 'summary': name + '的独立研判'}}]})
        digest = build_digest(self.store, 'reader', now=datetime(2026, 10, 8, 10, tzinfo=SHANGHAI))
        self.assertEqual(len(digest['recommendations']), 1)
        row = digest['recommendations'][0]
        self.assertEqual(row['news_scope'], 'industry')
        self.assertEqual({impact['code']: impact['score'] for impact in row['company_impacts']}, {'601857': 1, '600028': -1})
        self.assertIn('中国石油的独立研判', digest['html'])
        self.assertIn('中国石化的独立研判', digest['html'])

    async def test_merged_digest_labels_staleness_for_each_company(self):
        self.store.put('watchlist', 'list', ['601857', '600028'], 'reader')
        for code, name, stale, score in [('601857', '中国石油', True, 0), ('600028', '中国石化', False, 2)]:
            self.store.put('dashboard', code, {'stock': {'code': code, 'name': name}, 'as_of': '2026-10-08T10:00:00+08:00', 'news': [
                {'id': code, 'title': '国际原油价格回落', 'url': 'https://example.com/oil', 'time': '2026-10-08', 'score': score,
                 'news_scope': 'industry', 'stale': stale, 'industry': '炼化及贸易', 'analysis': ANALYSIS}]})
        digest = build_digest(self.store, 'reader', now=datetime(2026, 10, 8, 10, tzinfo=SHANGHAI))
        impacts = {row['code']: row for row in digest['recommendations'][0]['company_impacts']}
        self.assertTrue(impacts['601857']['stale'])
        self.assertFalse(impacts['600028']['stale'])
        self.assertIn('中国石油（沿用上次材料）', digest['html'])

    async def test_changed_business_context_does_not_reuse_stale_analysis(self):
        first=await self.collect()
        old=next(row for row in first['news'] if row['news_scope']=='industry')
        self.store.put('company_business',STOCK['code'],{})
        self.service.industry_news.clock=lambda:time.time()+1801
        with patch('backend.providers.business_profile',new=AsyncMock(return_value={'code':STOCK['code'],'main_business':'完全不同的上游开采业务','source':'test'})):
            result=await self.collect(source_error=ProviderError('行业查询暂不可用'))
        stale=next(row for row in result['news'] if row['url']==old['url'])
        self.assertIsNone(stale.get('analysis'))
        self.assertIsNone(stale.get('score'))

    async def test_raw_and_paid_extraction_share_twelve_document_budget(self):
        day=datetime.now(SHANGHAI).date().isoformat()
        rows=[news(title=f'中国石油公告中标订单{i}' if i>=8 else f'中国石油盘中行情{i}',url=f'https://example.com/company-{i}',day=day,
                   content='中国石油订单增长，企业公布经营进展，更多详细资料需要核验。',
                   **({'raw_content':'中国石油订单增长，企业公布经营进展，新建生产线投产并扩大产能。'} if i>=8 else {})) for i in range(16)]
        gate=asyncio.Event()
        async def delayed_profile(stock):
            await gate.wait()
            return {**STOCK,'industry':'石油行业','status':'ok'}
        async def extract(endpoint,payload):
            gate.set()
            return {'results':[{'url':url,'raw_content':'中国石油订单增长，企业公布经营进展，新建生产线投产并扩大产能。'} for url in payload.get('urls',[])]}
        with patch.object(self.service.industry_news,'profile',new=AsyncMock(side_effect=delayed_profile)), patch('backend.providers.search_news',new=AsyncMock(return_value={'results':rows})), patch('backend.providers.akshare_news',new=AsyncMock(return_value={'results':[]})), patch('backend.providers.search_industry_news',new=AsyncMock(return_value={'results':[]})), patch('backend.providers.daily_market',new=AsyncMock(return_value={'candles':[],'price':None})), patch('backend.providers.tavily',new=AsyncMock(side_effect=extract)):
            result=await self.service.collect(STOCK['code'],7,3,False)
        self.assertLessEqual(result['pipeline']['counts']['extracted'],12)


class IndustryRuleTests(unittest.TestCase):
    def test_unknown_specialization_has_generic_queries(self):
        topics = build_topics('专用设备')
        self.assertGreaterEqual(len(topics), 1)
        self.assertLessEqual(len(topics), 2)
        self.assertIn('专用设备', topics[0]['query'])
        self.assertEqual(build_topics('A 股'), [])
        self.assertEqual(build_topics(''), [])

    def test_cross_industry_factors_and_material_evidence(self):
        cases = [
            ('石油行业', '国际原油价格回落', '国际原油价格回落，布伦特原油期货下跌，市场正在观察供需变化。', '原油价格'),
            ('炼化及贸易', '国际原油价格回落', '国际原油价格回落，布伦特原油期货下跌，市场正在观察供需变化。', '原油价格'),
            ('银行', '央行宣布下调政策利率', '央行宣布下调政策利率，信贷政策及融资成本出现调整，执行情况仍需观察。', '利率与信贷'),
            ('半导体', '芯片出口管制政策调整', '主管部门宣布调整芯片出口管制政策，半导体设备供应条件出现变化。', '产业政策'),
            ('医药商业', '药品集采政策发布', '有关部门发布药品集采政策，医保支付安排和采购规则需要进一步核验。', '医药政策'),
            ('航空机场', '航空燃油价格上涨', '航空燃油价格上涨，国际原油供应出现变化，燃油成本影响仍需结合经营资料观察。', '燃油成本'),
        ]
        for industry, title, content, factor in cases:
            with self.subTest(industry=industry):
                self.assertIn(factor, match_industry(industry, title, content)['factors'])
        self.assertFalse(match_industry('银行', '某银行招聘公告', '某银行发布新的人员招聘安排，报名条件及面试时间请查阅官方网站。')['factors'])
        self.assertFalse(match_industry('石油行业', '国际原油价格回落', '今天介绍一种新的学习方法，更多资料请查看网站提供的相关链接。')['factors'])
        self.assertFalse(match_industry('石油行业', '城市旅游活动开幕', '城市旅游活动开幕，文末顺便列出原油价格下跌等其他新闻链接。')['factors'])

    def test_generic_industry_requires_industry_and_event_in_body(self):
        matched = match_industry('专用设备', '专用设备行业需求改善', '专用设备行业需求改善，新订单增长，企业生产安排正在调整。')
        self.assertTrue(matched['factors'])
        self.assertFalse(match_industry('专用设备', '专用设备行业需求改善', '这是旅游活动的时间表及行程安排，欢迎查看活动报名要求。')['factors'])

    def test_analysis_budget_reserves_industry_and_fills_shortfalls(self):
        company = [{'id': str(i), 'news_scope': 'company'} for i in range(5)]
        industry = [{'id': 'i' + str(i), 'news_scope': 'industry'} for i in range(3)]
        self.assertEqual([row['id'] for row in select_analyses(company + industry, 3)], ['0', '1', 'i0'])
        self.assertEqual(len(select_analyses(company, 3)), 3)
        self.assertEqual(len(select_analyses(industry, 3)), 3)
        self.assertEqual(len(select_analyses(company + industry, 1)), 1)


class IndustryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.temp.name) / 'test.db')
        auth.initialize()
        self.store = ResearchStore(auth)
        self.store.initialize()
        self.now = 1791420000.0
        self.service = IndustryNewsService(self.store, clock=lambda: self.now)
        self.profile = {'code': '601857', 'name': '中国石油', 'industry': '石油行业', 'source': 'test'}

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def test_profile_cache_restore_and_failed_refresh_preserves_verified_industry(self):
        with patch('backend.providers.stock_profile', new=AsyncMock(return_value=self.profile)) as fetch:
            profile = await self.service.profile(STOCK)
            self.assertEqual(profile['industry'], '石油行业')
            self.assertEqual(profile['status'], 'ok')
            restored = IndustryNewsService(self.store, clock=lambda: self.now)
            try:
                self.assertEqual((await restored.profile(STOCK))['industry'], '石油行业')
                self.assertEqual(fetch.await_count, 1)
            finally:
                await restored.close()
        self.now += 8 * 86400
        with patch('backend.providers.stock_profile', new=AsyncMock(side_effect=ProviderError('源失败'))) as fetch:
            profile = await self.service.profile(STOCK)
            self.assertEqual(profile['status'], 'stale')
            self.assertEqual(profile['industry'], '石油行业')
            self.assertIn('源失败', profile['error'])
            await self.service.profile(STOCK)
            self.assertEqual(fetch.await_count, 1)

    async def test_wrong_stock_and_missing_industry_are_not_guessed(self):
        for response in ({**self.profile, 'code': '600028'}, {**self.profile, 'industry': 'A 股'}):
            with self.subTest(response=response), patch('backend.providers.stock_profile', new=AsyncMock(return_value=response)):
                self.now += 301
                profile = await self.service.profile(STOCK)
                self.assertEqual(profile['status'], 'unavailable')
                self.assertIsNone(profile['industry'])

    async def test_twenty_viewers_and_cross_instance_share_query(self):
        other = IndustryNewsService(self.store, clock=lambda: self.now)
        gate = asyncio.Event()
        entered = asyncio.Event()
        topic = build_topics('石油行业')[0]
        async def fetch(*args, **kwargs):
            entered.set()
            await gate.wait()
            return {'results': [{'title': '国际原油价格下跌', 'url': 'https://example.com/oil'}]}
        with patch('backend.providers.search_industry_news', new=AsyncMock(side_effect=fetch)) as source:
            first = asyncio.create_task(self.service.search(topic, '2026-10-01', '2026-10-08'))
            await entered.wait()
            viewers = [asyncio.create_task(other.search(topic, '2026-10-01', '2026-10-08')) for _ in range(20)]
            gate.set()
            results = await asyncio.gather(first, *viewers)
            self.assertEqual(source.await_count, 1)
            self.assertTrue(all(row['response']['results'][0]['title'] == '国际原油价格下跌' for row in results))
        await other.close()

    async def test_failed_search_preserves_cache_and_periods_do_not_share(self):
        topic = build_topics('医药商业')[0]
        with patch('backend.providers.search_industry_news', new=AsyncMock(return_value={'results': [{'title': '药品集采'}]})) as fetch:
            await self.service.search(topic, '2026-10-01', '2026-10-08')
            await self.service.search(topic, '2026-09-01', '2026-10-08')
            self.assertEqual(fetch.await_count, 2)
        self.now += 1801
        with patch('backend.providers.search_industry_news', new=AsyncMock(side_effect=ProviderError('行业检索失败'))) as fetch:
            result = await self.service.search(topic, '2026-10-01', '2026-10-08')
            self.assertEqual(result['status'], 'stale')
            self.assertEqual(result['response']['results'][0]['title'], '药品集采')
            await self.service.search(topic, '2026-10-01', '2026-10-08')
            self.assertEqual(fetch.await_count, 1)

    async def test_source_concurrency_is_bounded_and_cancelled_viewer_is_isolated(self):
        active = peak = 0
        async def fetch(*args, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.03)
            active -= 1
            return {'results': []}
        with patch('backend.providers.search_industry_news', new=AsyncMock(side_effect=fetch)):
            requests = [asyncio.create_task(self.service.search(build_topics(name)[0], '2026-10-01', '2026-10-08')) for name in ('专用设备', '纺织服装', '食品饮料', '旅游酒店', '出版', '船舶制造')]
            requests[0].cancel()
            await asyncio.gather(*requests, return_exceptions=True)
        self.assertLessEqual(peak, 2)

    async def test_industry_search_respects_mysql_record_key_limit(self):
        # SQLite accepts longer keys; production research_records.record_key is
        # VARCHAR(64). Reproduce that schema contract on every lease operation.
        original = {name: getattr(self.store, name) for name in ('claim', 'release')}
        def bounded(name):
            def invoke(key, *args):
                self.assertLessEqual(len(key), 64, 'MySQL record_key overflow')
                return original[name](key, *args)
            return invoke
        with patch.object(self.store, 'claim', side_effect=bounded('claim')), \
             patch.object(self.store, 'release', side_effect=bounded('release')), \
             patch('backend.providers.search_industry_news', new=AsyncMock(return_value={'results': [news()]})) as source:
            result = await self.service.search(build_topics('炼化及贸易')[1], '2026-09-08', '2026-10-08')
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(len(result['response']['results']), 1)
            source.assert_awaited_once()
        self.assertEqual(self.store.list('lease'), [])
