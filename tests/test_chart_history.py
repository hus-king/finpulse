import asyncio
import copy
import json
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from backend.auth import AuthStore
from backend.research_store import ResearchStore
from backend.news_cleaning import SHANGHAI
from backend.chart_history import ChartHistoryService, adjusted_bars, AdjustmentError


def bar(day,price):
    return dict(date=day,open=price,high=price+2,low=price-1,close=price+1,volume=1000)

BARS=[bar('2011-01-04',10),bar('2026-11-05',20)]
FACTORS=[dict(ts_code='600519.SH',trade_date='20110104',adj_factor=2.),
         dict(ts_code='600519.SH',trade_date='20261105',adj_factor=4.)]


class AdjustmentMathTests(unittest.TestCase):
    def test_normalization_is_end_date_for_qfq_and_absolute_for_hfq(self):
        raw=copy.deepcopy(BARS)
        q=adjusted_bars(raw,FACTORS,'600519','qfq')
        h=adjusted_bars(raw,FACTORS,'600519','hfq')
        self.assertEqual(q[-1],raw[-1])
        for key in ('open','high','low','close'):
            self.assertEqual(q[0][key],raw[0][key]/2)
            self.assertEqual(h[0][key],raw[0][key]*2)  # Never reset first2011factor to1.
            self.assertEqual(h[-1][key],raw[-1][key]*4)
        self.assertEqual(raw,BARS)
        self.assertEqual([r['volume'] for r in h],[1000,1000])

    def test_future_factors_are_not_used_and_halted_gaps_are_not_padded(self):
        future=FACTORS+[dict(ts_code='600519.SH',trade_date='20261106',adj_factor=8.)]
        self.assertEqual(adjusted_bars(BARS,future,'600519','qfq'),adjusted_bars(BARS,FACTORS,'600519','qfq'))
        self.assertEqual(len(adjusted_bars(BARS,FACTORS,'600519','hfq')),2)

    def test_missing_invalid_duplicate_and_wrong_symbol_factors_fail(self):
        cases=[FACTORS[:1],FACTORS[1:],FACTORS+[FACTORS[0]],
               [dict(FACTORS[0],adj_factor=0),FACTORS[1]],
               [dict(FACTORS[0],adj_factor=float('nan')),FACTORS[1]],
               [dict(r,ts_code='000001.SZ') for r in FACTORS]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(AdjustmentError):
                adjusted_bars(BARS,rows,'600519','qfq')


class ChartHistoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        auth=AuthStore(Path(self.temp.name)/'db');auth.initialize()
        self.store=ResearchStore(auth);self.store.initialize()
        self.raw=dict(candles=copy.deepcopy(BARS),quote=dict(price=21,adjustment='none'))
        self.daily=SimpleNamespace(store=self.store,clock=lambda:datetime(2026,11,6,16,tzinfo=SHANGHAI),
            cached=lambda code:copy.deepcopy(self.raw),snapshot=AsyncMock(return_value={'refreshing':False}))
        self.fetch=AsyncMock(return_value=copy.deepcopy(FACTORS))
        self.service=ChartHistoryService(self.daily,fetcher=self.fetch)
        self.addAsyncCleanup(self.service.close)

    async def ready(self,mode='qfq'):
        for _ in range(100):
            result=await self.service.get({'code':'600519'},mode)
            if result['status']!='loading':return result
            await asyncio.sleep(.01)
        self.fail('adjustment fetch did not settle')

    async def test_none_never_fetches_and_modes_share_factors_without_raw_cache_writes(self):
        self.store.put('daily_market','600519',self.raw)
        before=self.store.get('daily_market','600519')
        raw=await self.service.get({'code':'600519'},'none')
        self.assertEqual(raw['candles'],BARS);self.fetch.assert_not_awaited()
        q=await self.ready();h=await self.ready('hfq')
        self.assertEqual(q['status'],'ready');self.assertEqual(h['status'],'ready')
        self.assertEqual(q['from_date'],'2011-01-04');self.assertEqual(q['anchor_date'],'2026-11-05')
        self.assertEqual(self.fetch.await_count,1)
        self.assertEqual(self.store.get('daily_market','600519'),before)
        self.assertEqual(self.raw,dict(candles=BARS,quote=dict(price=21,adjustment='none')))

    async def test_new_price_day_requires_new_factors_instead_of_carrying_old_one(self):
        await self.ready()
        self.raw['candles'].append(bar('2026-11-06',30))
        self.fetch.return_value=FACTORS+[dict(ts_code='600519.SH',trade_date='20261106',adj_factor=5.)]
        result=await self.ready()
        self.assertEqual(result['anchor_date'],'2026-11-06')
        self.assertEqual(result['candles'][-1],self.raw['candles'][-1])
        self.assertEqual(self.fetch.await_count,2)

    async def test_truncated_provider_returns_unavailable_without_fake_factor(self):
        self.fetch.return_value=FACTORS[1:]
        result=await self.ready()
        self.assertEqual(result['status'],'unavailable');self.assertEqual(result['candles'],[])
        self.assertIn('因子',result['message'])

    async def test_provider_failure_is_sanitized_and_repeated_read_does_not_retry_immediately(self):
        self.fetch.side_effect=RuntimeError('SECRET TOKEN provider body')
        result=await self.ready()
        self.assertEqual(result['status'],'unavailable');self.assertNotIn('SECRET',result['message'])
        await self.ready();self.assertEqual(self.fetch.await_count,1)

    async def test_failed_new_day_refresh_preserves_valid_older_factor_cache(self):
        first=await self.ready()
        self.raw['candles'].append(bar('2026-11-06',30));self.fetch.side_effect=RuntimeError('offline')
        self.assertEqual((await self.ready())['status'],'unavailable')
        self.raw['candles'].pop()
        older=await self.ready()
        self.assertEqual(older['status'],'ready');self.assertEqual(older['candles'],first['candles'])

    async def test_indices_reject_adjusted_modes_and_close_cancels_work(self):
        with self.assertRaises(AdjustmentError):await self.service.get({'code':'sh000300'},'qfq')
        self.assertEqual((await self.service.get({'code':'sh000300'},'none'))['status'],'ready')
        blocker=asyncio.Event()
        async def blocked(*args):await blocker.wait();return FACTORS
        self.service.fetcher=blocked
        self.assertEqual((await self.service.get({'code':'600519'},'qfq'))['status'],'loading')
        await self.service.close()
        self.assertFalse(self.service.tasks)

    async def test_http_source_uses_disjoint_bounded_date_windows(self):
        import httpx
        windows=[]
        def respond(request):
            data=json.loads(request.content);params=data['params']
            windows.append((params['start_date'],params['end_date']))
            self.assertEqual(data['api_name'],'adj_factor')
            self.assertEqual(params['ts_code'],'600519.SH')
            rows=[r for r in FACTORS if params['start_date']<=r['trade_date']<=params['end_date']]
            return httpx.Response(200,json=dict(code=0,data=dict(fields=['ts_code','trade_date','adj_factor'],
                items=[[r['ts_code'],r['trade_date'],r['adj_factor']] for r in rows])))
        self.service.config=dict(http_url='https://factors.invalid',token='TEST')
        self.service.client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        rows=await self.service._fetch('600519','2011-01-01','2026-11-05')
        self.assertEqual(windows,[('20110101','20141231'),('20150101','20181231'),
                                  ('20190101','20221231'),('20230101','20261105')])
        self.assertEqual(adjusted_bars(BARS,rows,'600519','qfq')[-1],BARS[-1])

class ChartHistoryAPITests(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.chart_history_api import router
        self.service=SimpleNamespace(get=AsyncMock(return_value=dict(code='600519',adjustment='qfq',status='loading',candles=[])))
        app=FastAPI();app.state.chart_history=self.service
        app.state.research=SimpleNamespace(catalog=SimpleNamespace(get=lambda code:dict(code=code) if code in ('600519','sh000300') else None))
        app.include_router(router);self.client=TestClient(app)

    def test_public_read_is_no_store_and_validates_adjustment_and_security(self):
        response=self.client.get('/api/market/600519/history?adjustment=qfq')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.service.get.assert_awaited_once_with({'code':'600519'},'qfq')
        self.assertEqual(self.client.get('/api/market/600519/history?adjustment=invalid').status_code,422)
        self.assertEqual(self.client.get('/api/market/unknown/history').status_code,404)
        self.service.get.side_effect=AdjustmentError('指数不适用个股复权方式')
        self.assertEqual(self.client.get('/api/market/sh000300/history?adjustment=qfq').status_code,422)
