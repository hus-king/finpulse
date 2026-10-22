"""Leased daily collection and atomic publication of CSI300 PEG snapshots."""
import asyncio
import json
import logging
import os
import signal
import sys
import uuid
import time as wall_time
from datetime import datetime, time, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .news_cleaning import SHANGHAI
from .providers import ROOT, read_config
from .peg_snapshot import build_snapshot

LOG = logging.getLogger(__name__)


class PegService:
    heartbeat_seconds = 60
    lease_seconds = 180
    timeout_seconds = 7200

    def __init__(self, store, *, collector=None, expected_total=300, data_dir=None, clock=None):
        self.store, self.expected_total = store, expected_total
        self.clock = clock or (lambda: datetime.now(SHANGHAI))
        settings = read_config().get('peg',{})
        self.data_dir = Path(data_dir or os.environ.get('FINPULSE_PEG_DATA_DIR') or settings.get('data_dir')
                             or (ROOT/'config.local.json').resolve().parent/'data'/'peg')
        self.collector = collector or self._collect
        self.scheduler = None
        self.tasks = set()
        self.process = None
        self.run_dir = None
        self.workers = max(1,min(6,int(settings.get('workers',4))))

    @property
    def enabled(self):
        cfg = read_config()
        background = os.environ.get('FINPULSE_SCHEDULER_ENABLED',str(int(cfg.get('scheduler',{}).get('enabled',False)))) == '1'
        return os.environ.get('FINPULSE_PEG_ENABLED',str(int(background and cfg.get('peg',{}).get('enabled',True)))) == '1'

    def snapshot(self):
        saved = self.store.get('peg','latest',default={})
        attempt = self.store.get('peg','last_attempt')
        if attempt and attempt.get('status') == 'running':
            lease = self.store.get('lease','peg-daily-global',default={})
            if lease.get('expires',0) <= wall_time.time():
                attempt = {**attempt,'status':'interrupted','error':'上次更新已中断，已保留上次榜单。'}
        now = self.clock().astimezone(SHANGHAI)
        next_run = datetime.combine(now.date(),time(21),SHANGHAI)
        if next_run <= now or saved.get('as_of_date') == now.date().isoformat():next_run += timedelta(days=1)
        status = 'ready' if saved else 'empty'
        if attempt and attempt.get('status') == 'running':status = 'updating'
        elif attempt and attempt.get('status') in ('failed','interrupted') and saved:status = 'stale'
        return {'snapshot_id':None,'published_at':None,'as_of_date':None,'market_date':None,
                'collection_started_at':None,'collection_finished_at':None,
                'summary':dict(total=0,eligible=0,observing=0,not_applicable=0,missing=0,failed=0),'rows':[],
                **saved, 'status':status, 'last_attempt':attempt,
                'schedule':{'time':'21:00','timezone':'Asia/Shanghai','enabled':self.enabled,
                            'running':bool(self.scheduler and self.scheduler.running),
                            'next_run':next_run.isoformat() if self.enabled else None}}

    def start(self):
        if self.scheduler and self.scheduler.running:return self.scheduler
        if not self.enabled:return None
        self.scheduler = AsyncIOScheduler(timezone='Asia/Shanghai')
        self.scheduler.add_job(self.run,'cron',hour=21,minute=0,id='peg_daily',
                               max_instances=1,coalesce=True,misfire_grace_time=7200)
        self.scheduler.start()
        task=asyncio.create_task(self._catchup());self.tasks.add(task);task.add_done_callback(self.tasks.discard)
        return self.scheduler

    async def _catchup(self):
        now=self.clock().astimezone(SHANGHAI)
        if now.hour < 21:return
        attempt=await asyncio.to_thread(self.store.get,'peg','last_attempt',default={})
        if attempt.get('status') == 'running':
            lease=await asyncio.to_thread(self.store.get,'lease','peg-daily-global',default={})
            remaining=lease.get('expires',0)-wall_time.time()
            if remaining > 0:
                await asyncio.sleep(min(remaining+1,self.lease_seconds+1))
                attempt=await asyncio.to_thread(self.store.get,'peg','last_attempt',default={})
        if attempt.get('date') != now.date().isoformat() or attempt.get('status') in ('running','interrupted'):
            await self.run()

    async def _stop_process(self):
        process=self.process
        if process and process.returncode is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:await asyncio.wait_for(process.wait(),5)
            except asyncio.TimeoutError:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await process.wait()

    async def _collect(self):
        self.run_dir.mkdir(parents=True,exist_ok=False,mode=0o700)
        prefix=self.run_dir/'csi300'
        # Preserve public source diagnostics on disk; never return a raw subprocess log to users.
        with (self.run_dir/'collection.log').open('wb') as log:
            env={**os.environ,'PYTHONUNBUFFERED':'1','PYTHONDONTWRITEBYTECODE':'1'}
            self.process=await asyncio.create_subprocess_exec(
                sys.executable,'-m','backend.peg_collect','--workers',str(self.workers),'--prefix',str(prefix),
                cwd=ROOT,env=env,stdout=log,stderr=asyncio.subprocess.STDOUT,start_new_session=True)
            try:
                code=await self.process.wait()
                if code:raise RuntimeError('collector exit '+str(code))
            finally:
                await self._stop_process();self.process=None
        raw=prefix.with_name('csi300_raw.jsonl')
        def load():
            return [json.loads(line) for line in raw.read_text().splitlines() if line.strip()]
        return await asyncio.to_thread(load)

    def _publish(self, published, completed_run, token):
        now=int(wall_time.time())
        mysql=self.store.db.dialect=='mysql'
        with self.store.db.transaction() as conn:
            suffix=' FOR UPDATE' if mysql else ''
            record=conn.execute("SELECT payload FROM research_records WHERE namespace='lease' AND record_key='peg-daily-global' AND owner=''"+suffix).fetchone()
            lease=json.loads(record['payload']) if record else {}
            if lease.get('token')!=token or lease.get('expires',0)<=wall_time.time():
                raise RuntimeError('publication lease lost')
            sql='INSERT INTO research_records(namespace,record_key,owner,payload,created_at,updated_at) VALUES(?,?,?,?,?,?) '
            sql+=('ON DUPLICATE KEY UPDATE payload=VALUES(payload),updated_at=VALUES(updated_at)' if mysql
                  else 'ON CONFLICT(namespace,record_key,owner) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at')
            for namespace,key,value in [('peg_history',published['snapshot_id'],published),('peg','latest',published),('peg','last_attempt',completed_run)]:
                conn.execute(sql,(namespace,key,'',json.dumps(value,ensure_ascii=False,allow_nan=False),now,now))

    def _finish_attempt(self, run):
        # Serialize with publication: cancellation cannot mark a committed snapshot as interrupted.
        with self.store.db.transaction() as conn:
            suffix=' FOR UPDATE' if self.store.db.dialect=='mysql' else ''
            row=conn.execute("SELECT payload FROM research_records WHERE namespace='peg' AND record_key='last_attempt' AND owner=''"+suffix).fetchone()
            current=json.loads(row['payload']) if row else {}
            if current.get('id')==run['id'] and current.get('status')!='completed':
                conn.execute("UPDATE research_records SET payload=?,updated_at=? WHERE namespace='peg' AND record_key='last_attempt' AND owner=''",
                             (json.dumps(run,ensure_ascii=False,allow_nan=False),int(wall_time.time())))

    async def run(self, force=False):
        token=await asyncio.to_thread(self.store.claim,'peg-daily-global',self.lease_seconds)
        if not token:return {'status':'busy'}
        owner=asyncio.current_task();self.tasks.add(owner)
        now=self.clock().astimezone(SHANGHAI);day=now.date().isoformat()
        run={'id':uuid.uuid4().hex,'date':day,'status':'running','started_at':now.isoformat(),
             'completed':0,'total':self.expected_total}
        self.run_dir=self.data_dir/day/run['id']
        pulse=None;started=False
        try:
            previous=await asyncio.to_thread(self.store.get,'peg','latest')
            if previous and previous.get('as_of_date')==day and not force:return {'status':'already_completed'}
            await asyncio.to_thread(self.store.put,'peg','last_attempt',run.copy());started=True
            async def heartbeat():
                while True:
                    await asyncio.sleep(self.heartbeat_seconds)
                    valid=await asyncio.to_thread(self.store.renew,'peg-daily-global',token,self.lease_seconds)
                    if not valid:
                        owner.cancel();return
                    path=self.run_dir/'csi300_raw.jsonl'
                    if path.exists():
                        def count():
                            with path.open() as handle:return sum(1 for line in handle if line.strip())
                        run['completed']=await asyncio.to_thread(count)
                        await asyncio.to_thread(self.store.put,'peg','last_attempt',run.copy())
            pulse=asyncio.create_task(heartbeat())
            def pulse_failed(task):
                if not task.cancelled() and task.exception():owner.cancel()
            pulse.add_done_callback(pulse_failed)
            records=await asyncio.wait_for(self.collector(),self.timeout_seconds)
            if previous and previous.get('as_of_date')==day:
                previous=await asyncio.to_thread(self.store.get,'peg_history',previous.get('previous_snapshot_id',''))
            published=await asyncio.to_thread(build_snapshot,records,now=self.clock(),started_at=run['started_at'],
                                              previous=previous,expected_total=self.expected_total)
            if not await asyncio.to_thread(self.store.renew,'peg-daily-global',token,self.lease_seconds):
                raise RuntimeError('lease lost before publication')
            completed={**run,'status':'completed','completed':len(records),'snapshot_id':published['snapshot_id'],
                       'finished_at':self.clock().isoformat()}
            await asyncio.to_thread(self._publish,published,completed,token)
            run.update(completed)
        except asyncio.CancelledError:
            run.update(status='interrupted',error='更新已中断，已保留上次榜单');raise
        except Exception as exc:
            LOG.warning('PEG daily update failed (%s)',type(exc).__name__)
            message=str(exc) if isinstance(exc,ValueError) else '本次更新失败，已保留上次榜单。请查看后台采集日志。'
            run.update(status='failed',error=message)
        finally:
            if pulse:
                pulse.cancel();await asyncio.gather(pulse,return_exceptions=True)
            await self._stop_process()
            if started:
                run['finished_at']=self.clock().isoformat()
                await asyncio.to_thread(self._finish_attempt,run.copy())
            await asyncio.to_thread(self.store.release,'peg-daily-global',token)
            self.tasks.discard(owner)
        return run

    async def close(self):
        if self.scheduler:
            self.scheduler.shutdown(wait=False);self.scheduler=None
        tasks=[t for t in self.tasks if t is not asyncio.current_task() and not t.done()]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        await self._stop_process()
