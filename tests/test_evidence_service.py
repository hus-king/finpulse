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
    async def test_missing_profile_does_not_fail_analysis_and_insufficient_stays_null(self):
        self.model.return_value={'content':json.dumps({**BASE,'assessment':'insufficient','sentiment_score':None,'positive_factors':[]}),'model':'test'}
        with patch('backend.providers.business_profile',new=AsyncMock(side_effect=RuntimeError('offline'))):
            reply=await self.service.analyze_document(self.stock,self.item)
        self.assertIsNone(reply['analysis']['sentiment_score'])
        self.assertEqual(reply['analysis'].get('assessment'),'insufficient')

    async def test_briefing_importance_uses_new_scale_and_labels_insufficient(self):
        from datetime import datetime
        from backend.news_cleaning import SHANGHAI
        from backend.prompts import PROMPT_VERSION
        from backend.recommendations import build_digest
        self.store.put('watchlist','list',['600028'],'reader')
        self.store.put('dashboard','600028',{'stock':self.stock,'as_of':'2026-10-08T10:00:00+08:00','news':[
            {**self.item,'id':'impact','score':75,'analysis':{**BASE,'sentiment_score':75,'version':PROMPT_VERSION}},
            {**self.item,'id':'missing','url':'https://example.com/missing','title':'供应链变化','score':None,'analysis':{**BASE,'sentiment_score':None,'assessment':'insufficient','version':PROMPT_VERSION}}]})
        digest=build_digest(self.store,'reader',now=datetime(2026,10,8,12,tzinfo=SHANGHAI))
        item=next(row for row in digest['recommendations'] if row['id']=='impact')
        self.assertLessEqual(item['components']['重要性'],20)
        missing=next(row for row in digest['recommendations'] if row['id']=='missing')
        self.assertEqual(missing.get('score_label'),'待补证')
