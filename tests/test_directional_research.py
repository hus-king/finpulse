import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime
from backend.news_cleaning import SHANGHAI
from pathlib import Path
from unittest.mock import AsyncMock, patch
from pydantic import ValidationError
from backend.prompts import EvidenceAnalysis, PROMPT_VERSION, parse_json
from backend.evidence import build_overview
from backend.auth import AuthStore
from backend.research_store import ResearchStore
from backend.research import ResearchService
from tests.test_evidence import BASE

NOW=datetime(2026,10,9,12,tzinfo=SHANGHAI)

POS={**BASE,'sentiment_score':25,'assessment':'positive'}

class DirectionalContractTests(unittest.TestCase):
    def test_completed_ai_judgment_must_have_nonzero_direction(self):
        for change in ({'sentiment_score':0,'assessment':'neutral'},{'sentiment_score':None,'assessment':'mixed'},{'sentiment_score':None,'assessment':'insufficient'},{'sentiment_score':0,'assessment':'positive'}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                parse_json(json.dumps({**POS,**change}),EvidenceAnalysis)
        self.assertEqual(parse_json(json.dumps(POS),EvidenceAnalysis)['sentiment_score'],25)

    def test_overview_uses_scores_and_old_ambiguous_results_become_pending(self):
        rows=[{'id':'p','title':'订单增长','time':'2026-10-08','score':40,'analysis_status':'completed','analysis':{**POS,'sentiment_score':40,'version':PROMPT_VERSION}},
              {'id':'n','title':'成本上涨','time':'2026-10-07','score':-20,'analysis_status':'completed','analysis':{**POS,'sentiment_score':-20,'assessment':'negative','negative_factors':['成本上涨'],'version':PROMPT_VERSION}},
              {'id':'old','title':'旧版零分','score':0,'analysis_status':'completed','analysis':{'sentiment_score':0,'version':'news-v4-evidence','assessment':'neutral'}}]
        overview=build_overview(rows,now=NOW)
        self.assertEqual(overview.get('net_score'),11.5)
        self.assertEqual(overview['counts'],{'positive':1,'negative':1,'pending':1})
        self.assertEqual([row['score'] for row in overview.get('score_series',[])],[-20,40])

    def test_old_directional_scores_keep_their_real_scale_without_conversion(self):
        rows=[{'id':'v4','title':'旧版方向分','time':'2026-10-08','score':25,'analysis_status':'completed','analysis':{**POS,'version':'news-v4-evidence'}},
              {'id':'v3','title':'旧三档分','score':1,'analysis_status':'completed','analysis':{'sentiment_score':1,'version':'news-v3-industry'}}]
        overview=build_overview(rows,now=NOW)
        self.assertEqual(overview.get('net_score'),25)
        self.assertEqual(overview['counts']['pending'],1)

class AnalyzeAllTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();auth=AuthStore(Path(self.temp.name)/'test.db');auth.initialize()
        self.store=ResearchStore(auth);self.store.initialize()
        self.model=AsyncMock(return_value={'content':json.dumps(POS),'model':'test'})
        self.service=ResearchService(self.store,self.model)
        self.stock={'code':'600028','name':'中国石化','exchange':'SH'}
        self.service.catalog.items['600028']=self.stock
        self.item={'title':'中国石化业务进展','content':'中国石化披露业务进展，订单增长，具体利润率与交付进度待核验。','time':'2026-10-08','url':'https://example.com/news','text_source':'extracted_body','date_status':'body_verified','sources':[]}
        self.profile_patch=patch('backend.providers.business_profile',new=AsyncMock(return_value={'code':'600028','main_business':'炼化与销售','source':'test'}));self.profile_patch.start();self.addCleanup(self.profile_patch.stop)
        news=[{**self.item,'id':str(i),'title':f'中国石化第{i}项业务进展','url':f'https://example.com/news/{i}','analysis_status':'pending','analysis':None,'score':None} for i in range(9)]
        news.append({**self.item,'id':'ready','analysis_status':'completed','analysis':{**POS,'version':PROMPT_VERSION},'score':25})
        self.store.put('dashboard','600028',{'stock':self.stock,'news':news,'candles':[],'pipeline':{'warnings':[],'counts':{},'stages':{}},'quote':{},'as_of':None})
    async def asyncTearDown(self):
        await self.service.close();self.temp.cleanup()
    async def test_all_pending_articles_are_scored_without_news_or_market_collection(self):
        self.assertTrue(hasattr(self.service,'analyze_all'),'Missing batch analysis workflow')
        with patch('backend.providers.search_news',new=AsyncMock(side_effect=AssertionError('Do not re-search'))), patch('backend.providers.daily_market',new=AsyncMock(side_effect=AssertionError('Do not refresh market'))):
            result=await self.service.analyze_all('600028')
        self.assertEqual(result['pipeline']['counts']['analysis_finished'],9)
        self.assertTrue(all(n['analysis_status']=='completed' and n['score']!=0 for n in result['news']))
        self.assertEqual(self.model.await_count,9)
        await self.service.analyze_all('600028')
        self.assertEqual(self.model.await_count,9)

    async def test_batch_failure_keeps_other_scores_and_allows_retry(self):
        self.assertTrue(hasattr(self.service,'analyze_all'),'Missing batch analysis workflow')
        self.model.side_effect=[RuntimeError('offline')]+[{'content':json.dumps(POS),'model':'test'}]*8
        result=await self.service.analyze_all('600028')
        self.assertEqual(sum(n['analysis_status']=='failed' for n in result['news']),1)
        self.assertEqual(result['pipeline']['job_status'],'partial')
        self.model.side_effect=None
        result=await self.service.analyze_all('600028')
        self.assertEqual(sum(n['analysis_status']=='failed' for n in result['news']),0)
        self.assertEqual(result['pipeline']['job_status'],'completed')

    async def test_concurrent_accounts_share_one_batch(self):
        first,second=await asyncio.gather(self.service.refresh_shared('600028',analysis_only=True),self.service.refresh_shared('600028',analysis_only=True))
        self.assertEqual(self.model.await_count,9)
        self.assertEqual(first['revision'],second['revision'])

    async def test_interruption_preserves_finished_scores_and_releases_pending(self):
        started=asyncio.Event()
        async def slow_model(*args,**kwargs):
            started.set()
            await asyncio.Event().wait()
        self.model.side_effect=slow_model
        task=asyncio.create_task(self.service.analyze_all('600028'))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        saved=self.store.get('dashboard','600028')
        self.assertFalse(any(n['analysis_status']=='running' for n in saved['news']))
        self.assertEqual(saved['news'][-1]['score'],25)
        self.assertEqual(saved['pipeline']['stages']['analysis'],'failed')

    async def test_cancel_during_first_database_write_cannot_restore_running_snapshot(self):
        entered=threading.Event();release=threading.Event();original=self.store.put
        def delayed(*args,**kwargs):
            if args[0]=='dashboard' and not entered.is_set():
                entered.set();release.wait(5)
            return original(*args,**kwargs)
        with patch.object(self.store,'put',side_effect=delayed):
            task=asyncio.create_task(self.service.analyze_all('600028'))
            await asyncio.to_thread(entered.wait,5)
            self.assertTrue(entered.is_set())
            task.cancel();release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        saved=self.store.get('dashboard','600028')
        self.assertFalse(any(row['analysis_status']=='running' for row in saved['news']))
        self.assertEqual(saved['pipeline']['stages']['analysis'],'failed')

    async def test_zero_model_output_retries_without_inventing_a_score(self):
        self.stock['business_profile']={'main_business':'炼化','status':'ok'}
        zero={'content':json.dumps({**POS,'sentiment_score':0}),'model':'test'}
        self.model.side_effect=[zero,{'content':json.dumps(POS),'model':'test'}]
        result=await self.service.analyze_document(self.stock,self.item)
        self.assertEqual(result['analysis']['sentiment_score'],25)
        self.assertEqual(self.model.await_count,2)
        self.model.reset_mock();self.model.side_effect=[zero,zero]
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            await self.service.analyze_document(self.stock,{**self.item,'title':'另一条新闻'})
        self.assertEqual(self.model.await_count,2)

    async def test_failed_progress_write_stops_siblings_before_unlocking_stock(self):
        original=self.store.put;writes=0;active=0;calls=0
        def fail_once(*args,**kwargs):
            nonlocal writes
            if args[0]=='dashboard':
                writes+=1
                if writes==2:
                    raise RuntimeError('temporary database failure')
            return original(*args,**kwargs)
        async def controlled(*args,**kwargs):
            nonlocal active,calls
            calls+=1;active+=1
            try:
                if calls==1:
                    return {'content':json.dumps(POS),'model':'test'}
                await asyncio.Event().wait()
            finally:
                active-=1
        self.model.side_effect=controlled
        with patch.object(self.store,'put',side_effect=fail_once):
            with self.assertRaises(RuntimeError):
                await self.service.refresh_shared('600028',analysis_only=True)
        self.assertEqual(active,0)
        self.assertFalse(self.service.locks['600028'].locked())
        self.assertLess(calls,9)
        saved=self.store.get('dashboard','600028')
        self.assertEqual(sum(n['analysis_status']=='completed' for n in saved['news']),2)
        self.assertFalse(any(n['analysis_status']=='running' for n in saved['news']))

    async def test_cancel_during_completed_article_write_waits_for_real_thread(self):
        entered=threading.Event();release=threading.Event();original=self.store.put;calls=0
        def delayed(*args,**kwargs):
            if args[0]=='dashboard' and args[2]['pipeline']['counts']['analysis_finished']==1 and not entered.is_set():
                entered.set();release.wait(5)
            return original(*args,**kwargs)
        async def controlled(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:
                return {'content':json.dumps(POS),'model':'test'}
            await asyncio.Event().wait()
        self.model.side_effect=controlled
        with patch.object(self.store,'put',side_effect=delayed):
            task=asyncio.create_task(self.service.analyze_all('600028'))
            await asyncio.to_thread(entered.wait,5)
            self.assertTrue(entered.is_set())
            task.cancel()
            await asyncio.sleep(.02)
            self.assertFalse(task.done())
            # A second cancellation must not strand the write thread either.
            task.cancel()
            await asyncio.sleep(.02)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        saved=self.store.get('dashboard','600028')
        self.assertEqual(sum(n['analysis_status']=='completed' for n in saved['news']),2)
        self.assertFalse(any(n['analysis_status']=='running' for n in saved['news']))

    async def test_saved_noise_is_excluded_from_batch_and_company_score(self):
        saved=self.store.get('dashboard','600028')
        saved['news'].append({**self.item,'id':'noise','title':'股票代码验证怎么做','analysis_status':'pending','analysis':None,'score':None})
        self.store.put('dashboard','600028',saved)
        result=await self.service.analyze_all('600028')
        self.assertEqual(self.model.await_count,9)
        self.assertEqual(len(result['news']),10)
        self.assertEqual(result['excluded_news'][0]['id'],'noise')
        self.assertEqual(result['research_overview']['total'],10)
