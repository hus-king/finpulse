import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from backend.auth import AuthStore
from backend.research import ResearchService
from backend.research_store import ResearchStore
from tests.test_evidence import BASE

class EvidenceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        auth=AuthStore(Path(self.temp.name)/'test.db'); auth.initialize()
        self.store=ResearchStore(auth); self.store.initialize()
        self.model=AsyncMock(return_value={'content':json.dumps(BASE),'model':'test'})
        self.service=ResearchService(self.store,self.model)
        self.stock={'code':'600028','name':'中国石化','exchange':'SH','industry':'炼化'}
        self.item={'title':'国际原油价格回落','content':'原油价格下跌，炼化业务原材料成本下降，需核验库存减值。','time':'2026-10-08','url':'https://example.com/oil','text_source':'extracted_body','date_status':'body_verified'}
    async def asyncTearDown(self):
        await self.service.close(); self.temp.cleanup()
    async def test_current_business_facts_and_shanghai_time_are_passed_and_invalidate_cache(self):
        self.stock['business_profile']={'main_business':'炼油与石化','version':'business-v1','status':'ok','url':'https://example.com/facts'}
        first=await self.service.analyze_document(self.stock,self.item)
        self.assertEqual(first['analysis'].get('assessment'),'positive')
        payload=json.loads(self.model.call_args.args[0][1]['content'])
        self.assertEqual(payload.get('business_profile',{}).get('main_business'),'炼油与石化')
        self.assertTrue(payload.get('analysis_time','').endswith('+08:00'))
        await self.service.analyze_document(self.stock,self.item)
        self.assertEqual(self.model.await_count,1)
        self.stock['business_profile']['main_business']='上游油气开采'
        await self.service.analyze_document(self.stock,self.item)
        self.assertEqual(self.model.await_count,2)
    async def test_missing_profile_allows_weak_direction_with_low_confidence(self):
        self.model.return_value={'content':json.dumps({**BASE,'confidence':'low','sentiment_score':5}),'model':'test'}
        with patch('backend.providers.business_profile',new=AsyncMock(side_effect=RuntimeError('offline'))):
            reply=await self.service.analyze_document(self.stock,self.item)
        self.assertEqual(reply['analysis']['sentiment_score'],5)
        self.assertEqual(reply['analysis'].get('confidence'),'low')

    async def test_briefing_importance_uses_new_scale_and_labels_insufficient(self):
        from datetime import datetime
        from backend.news_cleaning import SHANGHAI
        from backend.prompts import PROMPT_VERSION
        from backend.recommendations import build_digest
        self.store.put('watchlist','list',['600028'],'reader')
        self.store.put('dashboard','600028',{'stock':self.stock,'as_of':'2026-10-08T10:00:00+08:00','news':[
            {**self.item,'id':'impact','score':25,'analysis':{**BASE,'sentiment_score':25,'version':'news-v4-evidence'}},
            {**self.item,'id':'missing','url':'https://example.com/missing','title':'供应链变化','score':None,'analysis':{**BASE,'sentiment_score':None,'assessment':'insufficient','version':'news-v4-evidence'}}]})
        digest=build_digest(self.store,'reader',now=datetime(2026,10,8,12,tzinfo=SHANGHAI))
        item=next(row for row in digest['recommendations'] if row['id']=='impact')
        self.assertEqual(item['components']['重要性'],6)
        missing=next(row for row in digest['recommendations'] if row['id']=='missing')
        self.assertEqual(missing.get('score_label'),'待研判')

    async def test_corrected_publication_date_and_material_quality_invalidate_analysis(self):
        self.stock['business_profile']={'main_business':'炼油','status':'ok'}
        await self.service.analyze_document(self.stock,self.item)
        for change in ({'time':'2020-01-01'},{'text_source':'search_fragments'},{'date_status':'metadata_only'}):
            await self.service.analyze_document(self.stock,{**self.item,**change})
        self.assertEqual(self.model.await_count,4)

    async def test_truncated_model_output_recovers_with_one_bounded_retry(self):
        from fastapi import HTTPException
        self.stock['business_profile']={'main_business':'炼油','status':'ok'}
        self.model.side_effect=[HTTPException(502,'模型输出达到长度上限，请增加模型输出预算。'),{'content':json.dumps(BASE),'model':'test'}]
        result=await self.service.analyze_document(self.stock,self.item)
        self.assertEqual(result['analysis']['assessment'],'positive')
        self.assertEqual(self.model.await_count,2)
        self.assertLessEqual(self.model.call_args.kwargs['max_tokens'],8192)

    async def test_provider_rate_limit_is_not_retried_as_format_failure(self):
        from fastapi import HTTPException
        self.stock['business_profile']={'main_business':'炼油','status':'ok'}
        self.model.side_effect=HTTPException(502,'请求额度或频率受限（上游 HTTP 429）。')
        with self.assertRaises(HTTPException):
            await self.service.analyze_document(self.stock,self.item)
        self.assertEqual(self.model.await_count,1)
