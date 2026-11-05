import copy
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import pandas as pd
import requests

from backend.quant import n2_inputs as inputs


def stock_data():
    days = pd.bdate_range('2025-07-01','2026-11-05').strftime('%Y%m%d').tolist()
    prices = {}
    factors = {}
    basics = {}
    for i, stamp in enumerate(days):
        price = 10.+math.sin(i/10)*.1
        prices[stamp] = dict(trade_date=stamp,open=price,high=price+.1,low=price-.1,close=price,vol=10000.)
        factors[stamp] = 2.
        basics[stamp] = dict(trade_date=stamp,close=price,pe_ttm=10.,total_share=100000.,total_mv=price*100000.)
    income = [dict(end_date=period,ann_date=ann,f_ann_date=ann,report_type='1',n_income_attr_p=profit,n_income=profit)
              for period,ann,profit in [('20251231','20260301',1e9),('20260630','20260801',6e8),
                                       ('20241231','20250301',8e8),('20250630','20250801',4e8)]]
    forecasts = [dict(report_date='20261020',create_time='2026-10-20 20:00:00',report_title='年度研究',
                      org_name='机构'+str(n),quarter=str(year)+'Q4',np=profit)
                 for n in range(3) for year,profit in [(2026,120000.),(2027,150000.),(2028,180000.)]]
    return dict(prices=prices,price_dates=days,factors=factors,factor_dates=days,basics=basics,
                income=income,cashflow=[],balance=[],fallback={},forecasts=forecasts,
                industry=[dict(in_date='20200101',out_date=None,l1_name='银行',l2_name='银行',name='测试银行')])


class PitPrimitivesTests(unittest.TestCase):
    def test_suspension_reference_close_is_preserved_without_inventing_an_open(self):
        row=dict(open=0.,high=0.,low=0.,close=15.56,vol=0.)
        bar=inputs._bar(row,'20260915',2.)
        self.assertFalse(bar['tradable'])
        self.assertEqual(bar['open'],0.)
        self.assertEqual(bar['close'],31.12)
        self.assertEqual(bar['volume'],0.)
        with self.assertRaises(inputs.N2InputError):
            inputs._bar(dict(row,vol=1.),'20260915')
    def test_forecast_create_and_report_dates_strictly_precede_day(self):
        data = stock_data()
        before = inputs.forecast_frame(data['forecasts'],'20261104',2025,1e9)
        later = copy.deepcopy(data['forecasts'])
        later += [dict(row,report_date='20261103',create_time='2026-11-04 00:01:00',np=row['np']*9) for row in data['forecasts']]
        later += [dict(row,report_date='20261104',create_time='2026-11-03 20:00:00',np=row['np']*9) for row in data['forecasts']]
        pd.testing.assert_frame_equal(before,inputs.forecast_frame(later,'20261104',2025,1e9))
        self.assertNotEqual(before.iloc[0]['2028E_预测净利润(元)'],inputs.forecast_frame(later[:18],'20261105',2025,1e9).iloc[0]['2028E_预测净利润(元)'])

    def test_financial_announcement_and_report_period_are_both_causal(self):
        data = stock_data()
        before = inputs.select_statements(data['income'],'20261104')
        rows = data['income']+[dict(end_date='20260930',ann_date='20261103',f_ann_date='20261104',report_type='1',n_income_attr_p=1e12)]
        self.assertEqual(before,inputs.select_statements(rows,'20261104'))
        self.assertIn('20260930',inputs.select_statements(rows,'20261105'))
        with self.assertRaisesRegex(ValueError,'冲突'):
            inputs.select_statements(data['income']+[dict(data['income'][0],n_income_attr_p=2e9)],'20261104')

    def test_forward_peg_matches_declared_formula_and_frozen_engine(self):
        data = stock_data();basic=data['basics']['20261103']
        row = inputs.rank_record('000001.SZ',data,'20261104','20261103',basic)
        cap = basic['close']*100000.*10000
        expected = (cap/1.2e9)/((math.sqrt(1.8/1.2)-1)*100)
        self.assertAlmostEqual(row['前瞻PEG'],expected,places=12)
        self.assertEqual(row['机构数'],3)
        self.assertEqual(row['报告截止'],'2026-10-20')
        self.assertEqual(inputs._engine_proof(),inputs.ENGINE_HASHES)

    def test_nonfinancial_eligibility_receives_causal_cash_and_balance_fields(self):
        data=stock_data();data['industry'][0].update(l1_name='机械设备',l2_name='工程机械')
        data['income'] += [dict(end_date='20231231',ann_date='20240401',f_ann_date='20240401',report_type='1',n_income=1e9,n_income_attr_p=1e9)]
        data['cashflow'] = [dict(end_date=f'{year}1231',ann_date=f'{year+1}0401',f_ann_date=f'{year+1}0401',report_type='1',n_cashflow_act=1.2e9,c_pay_acq_const_fiolta=.2e9) for year in (2023,2024,2025)]
        data['balance'] = [dict(end_date='20260630',ann_date='20260801',f_ann_date='20260801',report_type='1',money_cap=1e9)]
        row=inputs.rank_record('000001.SZ',data,'20261104','20261103',data['basics']['20261103'])
        self.assertEqual(row['自由现金流_亿'],30.)
        self.assertAlmostEqual(row['利润现金兑现率'],3.6e9/2.8e9)
        self.assertEqual(row['净负债_亿'],-10.)
        data['balance'][0].pop('money_cap')
        with self.assertRaisesRegex(ValueError,'货币资金缺失'):
            inputs.rank_record('000001.SZ',data,'20261104','20261103',data['basics']['20261103'])

    def test_merge_retains_source_and_conflicting_same_date_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)/'base';overlay=Path(folder)/'overlay'
            inputs._atomic(base/'daily/000001.SZ.json',[dict(ts_code='000001.SZ',trade_date='20261027',close=10)])
            original=(base/'daily/000001.SZ.json').read_bytes()
            inputs._cache_rows(overlay,Path('daily/000001.SZ.json'),[dict(ts_code='000001.SZ',trade_date='20261028',close=11)],
                               api='daily',params={'ts_code':'000001.SZ'},end='20261028')
            merged=inputs._merge_rows(base,overlay,Path('daily/000001.SZ.json'))
            self.assertEqual(len(merged),2)
            self.assertEqual((base/'daily/000001.SZ.json').read_bytes(),original)
            self.assertTrue(inputs._cache_fresh(overlay,Path('daily/000001.SZ.json'),'20261028'))
            with self.assertRaisesRegex(inputs.N2InputError,'版本冲突'):
                inputs._dated(merged+[dict(ts_code='000001.SZ',trade_date='20261027',close=99)],'trade_date','000001.SZ')
            with self.assertRaisesRegex(inputs.N2InputError,'缺少股票档案'):
                inputs._merge_rows(base,overlay,Path('income/000001.SZ.json'))

    def test_provider_errors_never_expose_credentials_or_raw_messages(self):
        provider=inputs._Provider({'http_url':'https://example.invalid/api','token':'PRIVATE-TOKEN'})
        response=type('Response',(),{'json':lambda self:{'code':403,'msg':'PRIVATE-TOKEN'}})()
        with patch.object(inputs.requests,'post',return_value=response),self.assertRaises(inputs.N2InputError) as error:
            provider.query('daily',{},'')
        self.assertNotIn('PRIVATE-TOKEN',str(error.exception))
        with patch.object(inputs.requests,'post',side_effect=requests.RequestException('PRIVATE-TOKEN')),patch.object(inputs.time,'sleep'),self.assertRaises(inputs.N2InputError) as error:
            provider.query('daily',{},'')
        self.assertNotIn('PRIVATE-TOKEN',str(error.exception))

    def test_two_threads_share_429_cooldown_and_two_per_second_gate(self):
        provider=inputs._Provider({'http_url':'https://example.invalid/api','token':'PRIVATE-TOKEN'})
        stamps=[];lock=threading.Lock()
        class Response:
            status_code=200
            headers={'Retry-After':'5'}
            def __init__(self,code):self.code=code
            def json(self):return {'code':self.code,'data':{'fields':[],'items':[]}}
        def post(*args,**kwargs):
            with lock:
                index=len(stamps);stamps.append(time.monotonic())
            return Response(429 if index==0 else 0)
        with patch.object(inputs.requests,'post',side_effect=post),ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(provider.query,api,{},'') for api in ('daily','adj_factor')]
            self.assertEqual([f.result(timeout=8) for f in futures],[[],[]])
        self.assertEqual(provider.calls,3)
        self.assertGreaterEqual(stamps[1]-stamps[0],4.9)
        self.assertGreaterEqual(stamps[2]-stamps[1],.45)

    def test_persistent_429_aborts_later_requests_after_one_retry(self):
        provider=inputs._Provider({'http_url':'https://example.invalid/api','token':'PRIVATE-TOKEN'})
        class Response:
            status_code=429
            headers={'Retry-After':'0'}
            def json(self):raise AssertionError('HTTP429 does not need body parsing')
        with patch.object(inputs.requests,'post',return_value=Response()) as post:
            with self.assertRaisesRegex(inputs.N2InputError,'refresh_aborted') as error:
                provider.query('daily',{},'')
            self.assertTrue(provider.abort_event.is_set())
            with self.assertRaisesRegex(inputs.N2InputError,'provider_429_aborted'):
                provider.query('report_rc',{},'')
        self.assertEqual(post.call_count,2)
        self.assertEqual(provider.calls,2)
        self.assertNotIn('PRIVATE-TOKEN',str(error.exception))
        for value,want in [('1',5.),('9000',60.),('garbage',5.)]:
            response=Response();response.headers={'Retry-After':value}
            self.assertEqual(provider._retry_after(response),want)


class CompleteInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)/'base';self.overlay=Path(self.temp.name)/'overlay'
        self.data=stock_data();self.members=[f'{i:06d}.SZ' for i in range(1,301)]
        inputs._atomic(self.base/'membership_summary.json',{'202609':dict(trade_date='20260930',stocks=self.members)})
        inputs._atomic(self.base/'reference/csi300_price.json',[{'trade_date':d} for d in self.data['price_dates']])
        inputs._atomic(self.base/'data_manifest.json',{'end':'20261103'})

    def load(self,data=None,**kwargs):
        with patch.object(inputs,'_stock',return_value=data or self.data):
            return inputs.load_inputs(self.base,self.overlay,'2026-11-04',**kwargs)

    def test_complete_cross_section_and_execution_separate_from_history(self):
        output=self.load(extra_codes=['600028'])
        self.assertEqual(len(output['rows']),300)
        self.assertEqual(len(output['coverage']),300)
        self.assertEqual(len(output['histories']),301)
        self.assertNotIn('600028',{row['code'] for row in output['rows']})
        self.assertEqual(output['sectors']['600028'],'银行')
        self.assertEqual(output['signal_date'],'2026-11-03')
        self.assertEqual(output['calendar'][-1],'2026-11-03')
        self.assertEqual(output['histories']['000001']['raw'][-1]['date'],'2026-11-03')
        self.assertIn('2026-11-04',output['daily']['000001'])
        self.assertNotIn('2026-11-05',output['daily']['000001'])
        raw=output['histories']['000001']['raw'][-1];adjusted=output['histories']['000001']['adjusted'][-1]
        self.assertEqual(adjusted['close'],raw['close']*2)
        self.assertEqual(raw['volume'],10000*100)
        json.dumps(output,allow_nan=False)

    def test_future_prices_and_financials_do_not_change_rank_inputs(self):
        before=self.load();data=copy.deepcopy(self.data)
        data['prices']['20261104']['close']*=1.01
        data['prices']['20261104']['high']*=1.01
        data['factors']['20261104']=999
        data['income'].append(dict(end_date='20260930',ann_date='20261104',f_ann_date='20261104',report_type='1',n_income_attr_p=1e12))
        after=self.load(data)
        self.assertEqual(before['rows'],after['rows'])
        self.assertEqual(before['histories'],after['histories'])

    def test_missing_factors_fail_instead_of_changing_cross_section(self):
        data=copy.deepcopy(self.data);data['factors']={};data['factor_dates']=[]
        with self.assertRaisesRegex(inputs.N2InputError,'复权因子'):
            self.load(data)
        data=copy.deepcopy(self.data);data['factors'].pop('20261103')
        with self.assertRaisesRegex(inputs.N2InputError,'复权因子'):
            self.load(data)

    def test_universe_and_refresh_coverage_gates(self):
        inputs._atomic(self.base/'membership_summary.json',{'202609':dict(trade_date='20260930',stocks=self.members[:-1])})
        with self.assertRaisesRegex(inputs.N2InputError,'不完整'):
            self.load()
        inputs._atomic(self.base/'membership_summary.json',{'202609':dict(trade_date='20260930',stocks=self.members)})
        inputs._atomic(self.base/'data_manifest.json',{'end':'20261027'})
        with self.assertRaisesRegex(inputs.N2InputError,'尚未完整更新'):
            self.load()
        inputs._atomic(self.overlay/'refresh-state.json',{'status':'complete','end':'20261103','failed_stocks':0,'finished_at':'2026-11-04T00:00:00Z'})
        output=self.load()
        self.assertEqual(output['provenance']['fetched_at'],'2026-11-04T00:00:00Z')

    def test_refresh_keeps_successful_cache_and_retries_only_failed_endpoint(self):
        class Provider:
            calls=0
            fail=True
            apis=[]
            abort_event=threading.Event()
            def ranged(self,api,params,fields,cap):
                self.calls+=1;self.apis.append(api)
                if api=='adj_factor' and self.fail:
                    raise inputs.N2InputError('adj_factor: provider_code_429')
                return [dict(ts_code='000001.SZ',trade_date='20261104',adj_factor=2.)]
        provider=Provider();fresh=inputs._cache_fresh
        def cached(overlay,relative,end):
            return fresh(overlay,relative,end) if relative.parts[0] in ('daily','adj_factor') else True
        def saved(base,overlay,relative):
            return [dict(trade_date='20261104' if relative.parts[0]=='reference' else '20261103')]
        base_bytes=(self.base/'data_manifest.json').read_bytes()
        with patch.object(inputs,'_Provider',return_value=provider),patch.object(inputs,'_memberships',return_value={'20261030':['000001.SZ']}),patch.object(inputs,'_closed_months',return_value=[]),patch.object(inputs,'_saved_rows',side_effect=saved),patch.object(inputs,'_cache_fresh',side_effect=cached):
            with self.assertRaisesRegex(inputs.N2InputError,'1只股票失败'):
                inputs.refresh_archive(self.base,self.overlay,{},'20261104')
            self.assertTrue((self.overlay/'daily/000001.SZ.json').is_file())
            self.assertEqual(json.loads((self.overlay/'refresh-state.json').read_text())['status'],'failed')
            provider.fail=False
            result=inputs.refresh_archive(self.base,self.overlay,{},'20261104')
        self.assertEqual(result['status'],'complete')
        self.assertEqual(provider.apis,['daily','adj_factor','adj_factor'])
        self.assertEqual((self.base/'data_manifest.json').read_bytes(),base_bytes)

    def test_persistent_limit_stops_new_stock_tasks(self):
        class Provider:
            calls=0
            abort_event=threading.Event()
            def ranged(self,api,params,fields,cap):
                self.calls+=1;self.abort_event.set()
                raise inputs.N2InputError('daily: provider_code_429; refresh_aborted')
        provider=Provider()
        def saved(base,overlay,relative):
            return [dict(trade_date='20261104' if relative.parts[0]=='reference' else '20261103')]
        with patch.object(inputs,'_Provider',return_value=provider),patch.object(inputs,'_memberships',return_value={'20261030':self.members}),patch.object(inputs,'_closed_months',return_value=[]),patch.object(inputs,'_saved_rows',side_effect=saved):
            with self.assertRaisesRegex(inputs.N2InputError,'只股票失败'):
                inputs.refresh_archive(self.base,self.overlay,{},'20261104')
        state=json.loads((self.overlay/'refresh-state.json').read_text())
        # Two workers may already have entered their first request when one
        # reports the limit; no further stock may start after the shared abort.
        self.assertGreaterEqual(provider.calls,1)
        self.assertLessEqual(provider.calls,2)
        self.assertEqual(state['status'],'failed')
        self.assertEqual(state['abort_reason'],'provider_429')
        self.assertLessEqual(state['completed_stocks'],2)
        self.assertGreaterEqual(state['deferred_stocks'],298)

    def test_pre_stock_refresh_failure_is_recorded_and_base_cannot_be_output(self):
        with patch.object(inputs,'_Provider'),patch.object(inputs,'_memberships',side_effect=inputs.N2InputError('membership incomplete')):
            with self.assertRaisesRegex(inputs.N2InputError,'membership incomplete'):
                inputs.refresh_archive(self.base,self.overlay,{},'20261104')
        state=json.loads((self.overlay/'refresh-state.json').read_text())
        self.assertEqual(state['status'],'failed')
        self.assertIn('finished_at',state)
        before=(self.base/'data_manifest.json').read_bytes()
        with self.assertRaisesRegex(inputs.N2InputError,'独立overlay'):
            inputs.refresh_archive(self.base,self.base,{},'20261104')
        self.assertEqual((self.base/'data_manifest.json').read_bytes(),before)


if __name__=='__main__':
    unittest.main()
