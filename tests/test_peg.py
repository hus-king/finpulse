import asyncio
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from backend.auth import AuthStore
from backend.news_cleaning import SHANGHAI
from backend.research_store import ResearchStore
from backend.peg_snapshot import build_snapshot
from backend.peg_service import PegService

NOW = datetime(2026, 10, 22, 21, 0, tzinfo=SHANGHAI)


def row(code='000425', **changes):
    result = {'代码': code, '名称': '测试公司'+code, '行业': '工程机械', '状态': '成功',
              '最新价': 10., '总市值_亿': 500., 'PE_TTM': 12., '前瞻PE': 10.,
              '前瞻PEG': .5, '前瞻CAGR_%': 20., '基期年': 2025, '基期净利润_亿': 90.,
              '前瞻起始年': 2026, '前瞻起始净利润_亿': 100., '预测年': 2028,
              '预测净利润_亿': 144., '机构数': 15, '预测分歧度_%': 5., '报告截止': '2026-10-01',
              '自由现金流_亿': 20., '平均FCF收益率_%': 5., '利润现金兑现率': 1.1,
              '净负债_亿': 20., '股东收益率_%': 3., '历史波动率_%': 25., '基期异常': '否',
              '采集时间': NOW.isoformat()}
    return {**result, **changes}


def snapshot(rows, previous=None):
    return build_snapshot(rows, now=NOW, started_at=NOW.isoformat(), previous=previous,
                          expected_total=len(rows))


class PegSnapshotTests(unittest.TestCase):
    def test_positive_peg_groups_ties_and_nullable_metrics(self):
        data = snapshot([row('000425'), row('600519', **{'前瞻PEG':.2}),
                         row('000001', 行业='银行', **{'前瞻PEG':.1}),
                         row('300274', **{'前瞻PEG':-.1}), row('300750', **{'前瞻PEG':0})])
        rows = {r['code']:r for r in data['rows']}
        self.assertEqual([rows[c]['rank'] for c in ('600519','000425','000001')], [1,2,1])
        self.assertEqual(rows['000001']['group'], '金融')
        self.assertIsNone(rows['300274']['rank'])
        self.assertIsNone(rows['300750']['rank'])
        self.assertEqual(data['summary']['eligible'], 3)
        self.assertEqual(data['market_date'], '2026-10-22')
        tied = snapshot([row('600519'),row('000425')])
        self.assertEqual([r['code'] for r in tied['rows']], ['000425','600519'])

    def test_confidence_and_risks_preserve_engine_rules_without_changing_peg(self):
        data = snapshot([row('000425', **{'自由现金流_亿':-1}),row('600519', 机构数=1)])
        rows = {r['code']:r for r in data['rows']}
        self.assertEqual(rows['000425']['rank'], 1)
        self.assertIn('自由现金流为负',rows['000425']['risk_tags'])
        self.assertEqual(rows['000425']['peg'], .5)
        self.assertEqual(rows['600519']['status'], '观察')
        self.assertIsNone(rows['600519']['rank'])

    def test_only_comparable_previous_snapshots_produce_rank_deltas(self):
        first = snapshot([row('000425'),row('600519', **{'前瞻PEG':.2})])
        second = snapshot([row('000425', **{'前瞻PEG':.1}),row('600519', **{'前瞻PEG':.2})],first)
        self.assertEqual(second['rows'][0]['rank_change'],1)
        self.assertEqual(second['rows'][1]['rank_change'],-1)
        first['method_version']='another-method'
        third = snapshot([row('000425'),row('600519')],first)
        self.assertTrue(all(r['rank_change'] is None for r in third['rows']))
        self.assertTrue(all(r['rank_status']=='initial' for r in third['rows']))

    def test_changed_universe_and_groups_have_explicit_comparison_status(self):
        first = snapshot([row('000425'),row('600519')])
        second = snapshot([row('000425',行业='银行'),row('300274')],first)
        rows={r['code']:r for r in second['rows']}
        self.assertEqual(rows['000425']['rank_status'],'group_changed')
        self.assertEqual(rows['300274']['rank_status'],'new')
        self.assertIsNone(rows['000425']['rank_change'])

    def test_reject_incomplete_duplicate_invalid_and_source_wide_failure(self):
        for rows, expected in [([row()],300),([row(),row()],2),([row('x')],1),
                               ([row(状态='失败',失败原因='网络超时')],1)]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                build_snapshot(rows,now=NOW,started_at=NOW.isoformat(),expected_total=expected)

    def test_assessment_cannot_silently_drop_a_constituent(self):
        import pandas as pd
        from integrations.stock_analyzer.selection import assess_csi300_frame
        assessed=assess_csi300_frame(pd.DataFrame([row()]),as_of_date='2026-10-22')
        with patch('backend.peg_snapshot.assess_csi300_frame',return_value=assessed),self.assertRaises(ValueError):
            snapshot([row(),row('600519')])

    def test_pinned_engine_files_match_provenance_and_financial_cash_is_not_required(self):
        import hashlib,json
        from backend.peg_snapshot import ENGINE_ROOT,SOURCE
        for name,digest in SOURCE['files'].items():
            self.assertEqual(hashlib.sha256((ENGINE_ROOT/name).read_bytes()).hexdigest(),digest)
        financial=row('000001',行业='银行')
        for field in ('自由现金流_亿','平均FCF收益率_%','利润现金兑现率','净负债_亿'):
            financial.pop(field)
        self.assertEqual(snapshot([financial])['rows'][0]['status'],'合格')

    def test_unknown_nonfinite_data_stays_unranked_and_json_safe(self):
        import json
        data=snapshot([row('000425'),row('600519', **{'最新价':float('nan')})])
        json.dumps(data,allow_nan=False)
        missing=next(r for r in data['rows'] if r['code']=='600519')
        self.assertIsNone(missing['price']);self.assertIsNone(missing['rank'])
        self.assertEqual(missing['status'],'数据缺失')


class PegServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.auth=AuthStore(Path(self.temp.name)/'test.db')
        self.auth.initialize();self.store=ResearchStore(self.auth);self.store.initialize()
        self.collector=AsyncMock(return_value=[row()])
        self.service=PegService(self.store,collector=self.collector,expected_total=1,
                                data_dir=Path(self.temp.name)/'runs',clock=lambda:NOW)

    async def asyncTearDown(self):
        await self.service.close();self.temp.cleanup()

    async def test_publish_and_daily_idempotence_are_durable_across_instances(self):
        before=self.service.snapshot();self.assertEqual(before['status'],'empty')
        self.collector.assert_not_awaited()
        await self.service.run()
        self.assertEqual(self.service.snapshot()['summary']['eligible'],1)
        other=PegService(self.store,collector=self.collector,expected_total=1,clock=lambda:NOW)
        self.assertEqual((await other.run())['status'],'already_completed')
        self.assertEqual(self.collector.await_count,1)

    async def test_failed_update_preserves_last_success_and_exposes_safe_error(self):
        await self.service.run();old=self.service.snapshot()['snapshot_id']
        self.collector.side_effect=RuntimeError('secret upstream internal detail')
        result=await self.service.run(force=True)
        self.assertEqual(result['status'],'failed')
        current=self.service.snapshot()
        self.assertEqual(current['snapshot_id'],old);self.assertEqual(current['status'],'stale')
        self.assertNotIn('secret',current['last_attempt']['error'])
        self.assertEqual(current['summary']['eligible'],1)

    async def test_incomplete_batch_and_publication_failure_keep_old_snapshot(self):
        await self.service.run();old=self.service.snapshot()['snapshot_id']
        self.collector.return_value=[]
        self.assertEqual((await self.service.run(force=True))['status'],'failed')
        self.assertEqual(self.service.snapshot()['snapshot_id'],old)
        self.collector.return_value=[row()]
        original=self.auth.transaction
        @contextmanager
        def failing_transaction():
            with original() as conn:
                class Proxy:
                    def execute(self,sql,params=()):
                        if params[:2]==('peg','latest'):raise RuntimeError('publication failure')
                        return conn.execute(sql,params)
                yield Proxy()
        with patch.object(self.auth,'transaction',side_effect=failing_transaction):
            self.assertEqual((await self.service.run(force=True))['status'],'failed')
        self.assertEqual(len(self.store.list('peg_history')),1)
        self.assertEqual(self.service.snapshot()['snapshot_id'],old)

    async def test_publication_is_fenced_against_ownership_loss_after_renewal(self):
        await self.service.run();old=self.service.snapshot()['snapshot_id']
        original=self.store.renew
        def steal(key,token,ttl):
            result=original(key,token,ttl)
            self.store.release(key,token)
            self.store.claim(key,ttl)
            return result
        with patch.object(self.store,'renew',side_effect=steal):
            result=await self.service.run(force=True)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(self.service.snapshot()['snapshot_id'],old)
        self.assertEqual(len(self.store.list('peg_history')),1)

    async def test_scheduler_start_is_idempotent(self):
        with patch.dict('os.environ',{'FINPULSE_PEG_ENABLED':'1'}),patch.object(self.service,'_catchup',new=AsyncMock()):
            first=self.service.start();second=self.service.start()
            try:self.assertIs(first,second)
            finally:
                if first is not second:first.shutdown(wait=False)

    async def test_cross_instance_lease_prevents_concurrent_collection(self):
        gate=asyncio.Event()
        async def wait():await gate.wait();return [row()]
        self.collector.side_effect=wait
        task=asyncio.create_task(self.service.run())
        while not self.collector.await_count:await asyncio.sleep(.01)
        other=PegService(self.store,collector=self.collector,expected_total=1,clock=lambda:NOW)
        self.assertEqual((await other.run())['status'],'busy')
        gate.set();await task
        self.assertEqual(self.collector.await_count,1)

    async def test_heartbeat_renews_lease_and_loss_cancels_collection(self):
        self.service.heartbeat_seconds=.01
        self.collector.side_effect=lambda:None
        async def wait():await asyncio.sleep(20)
        self.collector.side_effect=wait
        with patch.object(self.store,'renew',return_value=False) as renew:
            task=asyncio.create_task(self.service.run())
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertGreater(renew.call_count,0)
        self.assertEqual(self.store.get('peg','last_attempt')['status'],'interrupted')
        self.assertIsNone(self.store.get('lease','peg-daily-global'))

    async def test_stale_running_record_is_not_displayed_as_permanent_progress(self):
        await self.service.run()
        self.store.put('peg','last_attempt',{'id':'dead','date':'2026-10-22','status':'running','started_at':NOW.isoformat()})
        self.assertEqual(self.service.snapshot()['status'],'stale')
        self.assertEqual(self.service.snapshot()['last_attempt']['status'],'interrupted')

    async def test_restart_catches_up_interrupted_but_not_successful_daily_run(self):
        self.store.put('peg','last_attempt',{'id':'dead','date':'2026-10-22','status':'interrupted','started_at':NOW.isoformat()})
        await self.service._catchup()
        self.assertEqual(self.collector.await_count,1)
        await self.service._catchup()
        self.assertEqual(self.collector.await_count,1)

    async def test_cron_is_daily_21_beijing_and_local_default_is_off(self):
        with patch.dict('os.environ',{'FINPULSE_SCHEDULER_ENABLED':'0','FINPULSE_PEG_ENABLED':'0'}):
            self.assertIsNone(self.service.start())
        with patch.dict('os.environ',{'FINPULSE_PEG_ENABLED':'1'}):
            self.service.start()
            job=self.service.scheduler.get_job('peg_daily')
            self.assertEqual(str(job.trigger.timezone),'Asia/Shanghai')
            self.assertIn("hour='21'",str(job.trigger));self.assertIn("minute='0'",str(job.trigger))


class PegApiTests(unittest.TestCase):
    def test_public_get_and_export_do_not_collect_and_update_is_not_public(self):
        from backend.app import app
        from tests.test_research import ORIGIN
        with tempfile.TemporaryDirectory() as directory:
            app.state.auth_store=AuthStore(Path(directory)/'api.db')
            try:
                with TestClient(app,base_url='http://localhost') as client:
                    response=client.get('/api/screener/csi300')
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(response.json()['status'],'empty')
                    saved=snapshot([row()]);app.state.research.store.put('peg','latest',saved)
                    self.assertEqual(client.get('/api/screener/csi300').json()['summary']['total'],1)
                    export=client.get('/api/screener/csi300/export.csv')
                    self.assertEqual(export.status_code,200);self.assertIn('前瞻PEG',export.text)
                    self.assertIn('000425',export.text)
                    self.assertEqual(client.post('/api/screener/csi300',headers=ORIGIN,json={}).status_code,405)
            finally:del app.state.auth_store
