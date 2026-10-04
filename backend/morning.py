"""Daily shared refresh, private digests and durable channel delivery state."""
import asyncio
import hashlib
import os
import uuid
import time
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import HTTPException

from .catalog import DEFAULT_WATCHLIST
from .news_cleaning import SHANGHAI
from .providers import read_config
from .recommendations import build_digest


def timestamp():
    return datetime.now(SHANGHAI).isoformat()


class MorningService:
    def __init__(self, research):
        self.research, self.store = research, research.store
        self.scheduler = None
        self.task = None
        self.catchup_task = None
        self.runs = set()
        self.launch_lock = asyncio.Lock()
        self.schedule_signature = None

    @property
    def instance_enabled(self):
        configured = read_config().get('scheduler', {}).get('enabled', False)
        return os.environ.get('FINPULSE_SCHEDULER_ENABLED', str(int(configured))) == '1'

    def settings(self):
        defaults = {'enabled': True, 'morning_time': '08:30', 'every_day': True, 'lookback_days': 7, 'max_articles': 3}
        configured = read_config().get('scheduler', {})
        defaults.update({key: value for key, value in configured.items() if key in defaults and key != 'enabled'})
        return {**defaults, **self.store.get('scheduler', 'settings', default={})}

    def status(self):
        job = self.scheduler.get_job('morning_digest') if self.scheduler else None
        return {**self.settings(), 'instance_enabled': self.instance_enabled, 'running': bool(self.scheduler and self.scheduler.running), 'next_run': job.next_run_time.isoformat() if job and job.next_run_time else None, 'last_run': self.store.get('morning_run', 'latest'), 'timezone': 'Asia/Shanghai', 'active_research_jobs': len(self.research.active), 'research_parallelism': 2, 'queue_limit': self.research.max_pending}

    def start_scheduler(self):
        if not self.instance_enabled:
            return None
        self.scheduler = AsyncIOScheduler(timezone='Asia/Shanghai')
        self.scheduler.add_job(self.tick, 'interval', minutes=15, id='morning_retry', max_instances=1, coalesce=True)
        self.configure_cron()
        self.scheduler.start()
        # Reconcile only today's due run and persisted retries after a restart.
        self.catchup_task = asyncio.create_task(self.tick())
        return self.scheduler

    def configure_cron(self, settings=None):
        if not self.scheduler:
            return
        settings = settings if settings is not None else self.settings()
        signature = (settings['enabled'], settings['morning_time'], settings['every_day'])
        if signature == self.schedule_signature:
            return
        if self.scheduler.get_job('morning_digest'):
            self.scheduler.remove_job('morning_digest')
        if settings['enabled']:
            hour, minute = map(int, settings['morning_time'].split(':'))
            self.scheduler.add_job(self.run, 'cron', hour=hour, minute=minute, day_of_week='*' if settings['every_day'] else 'mon-fri', id='morning_digest', max_instances=1, coalesce=True, misfire_grace_time=3600)
        self.schedule_signature = signature

    async def reload_schedule(self):
        settings = await asyncio.to_thread(self.settings)
        self.configure_cron(settings)
        return settings

    async def tick(self):
        settings = await self.reload_schedule()
        now = datetime.now(SHANGHAI)
        if not settings['enabled']:
            return
        if (settings['every_day'] or now.weekday() < 5) and now.strftime('%H:%M') >= settings['morning_time']:
            await self.run()
        await self.retry_deliveries()

    async def launch(self):
        async with self.launch_lock:
            if self.task and not self.task.done():
                return await asyncio.to_thread(self.store.get, 'morning_run', 'latest')
            # Cross-instance run() acquires the durable global lease before any API work.
            current = await asyncio.to_thread(self.store.get, 'morning_run', 'latest', default={})
            if current.get('status') == 'running':
                lease = await asyncio.to_thread(self.store.get, 'lease', 'morning-global', default={})
                if lease.get('expires', 0) > time.time():
                    return current
            queued = {'id': uuid.uuid4().hex, 'date': datetime.now(SHANGHAI).date().isoformat(), 'status': 'queued', 'stage': '等待每日更新', 'started_at': timestamp(), 'stocks': {}, 'users': 0, 'warnings': []}
            await asyncio.to_thread(self.store.put, 'morning_run', 'latest', queued)
            self.task = asyncio.create_task(self.run(manual=True, run_id=queued['id']))
            return queued

    def active_users(self):
        with self.store.db.connection() as conn:
            return [dict(row) for row in conn.execute('SELECT id FROM users WHERE is_active=1').fetchall()]

    def account_active(self, owner):
        with self.store.db.connection() as conn:
            account = conn.execute('SELECT is_active FROM users WHERE id=?', (owner,)).fetchone()
        return bool(account and account['is_active'])

    def targets(self, settings):
        preferences = {account['id']: self.store.get('subscription', 'settings', account['id'], {}) for account in self.active_users()}
        owners = [owner for owner, preference in preferences.items() if preference.get('enabled', True)]
        days = {}
        for owner in owners:
            for code in self.store.get('watchlist', 'list', owner, DEFAULT_WATCHLIST):
                days[code] = max(days.get(code, settings['lookback_days']), preferences[owner].get('lookback_days', 7))
        return owners, days

    async def run(self, manual=False, run_id=None):
        token = await asyncio.to_thread(self.store.claim, 'morning-global')
        if not token:
            return {'status': 'busy'}
        self.runs.add(asyncio.current_task())
        prior = None
        date = datetime.now(SHANGHAI).date().isoformat()
        run = {'id': run_id or uuid.uuid4().hex, 'date': date, 'status': 'running', 'stage': '合并用户自选股', 'started_at': timestamp(), 'stocks': {}, 'users': 0, 'warnings': []}
        async def heartbeat():
            while True:
                await asyncio.sleep(60)
                if not await asyncio.to_thread(self.store.renew, 'morning-global', token):
                    raise RuntimeError('Daily update lease lost')
        pulse = asyncio.create_task(heartbeat())
        # A failed heartbeat must stop work rather than let another instance duplicate it.
        current_task = asyncio.current_task()
        def lost_lease(task):
            if not task.cancelled() and task.exception():
                current_task.cancel()
        pulse.add_done_callback(lost_lease)
        try:
            prior = await asyncio.to_thread(self.store.get, 'morning_run', date)
            if not manual and prior and prior.get('status') in ('completed', 'partial'):
                return prior
            await asyncio.to_thread(self.store.db.cleanup_expired)
            await asyncio.to_thread(self.store.put, 'morning_run', 'latest', run.copy())
            settings = await asyncio.to_thread(self.settings)
            owners, stock_days = await asyncio.to_thread(self.targets, settings)
            codes = sorted(stock_days)
            run['stage'] = '更新共享新闻与历史行情'
            await asyncio.to_thread(self.store.put, 'morning_run', 'latest', run.copy())
            async def update(code):
                prior_stock = await asyncio.to_thread(self.store.get, 'daily_stock', date + ':' + code)
                if prior_stock and prior_stock.get('status') in ('completed', 'partial'):
                    run['stocks'][code] = {**prior_stock, 'reused': True}
                    return
                try:
                    snapshot = await self.research.refresh_shared(code, stock_days[code], settings['max_articles'], False)
                    pipeline = snapshot['pipeline']
                    outcome = {'status': 'partial' if pipeline['warnings'] else 'completed', 'warnings': pipeline['warnings'], 'updated_at': timestamp()}
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    outcome = {'status': 'failed', 'warnings': ['更新失败（' + type(exc).__name__ + '），晨报将提示旧数据'], 'updated_at': timestamp()}
                run['stocks'][code] = outcome
                await asyncio.to_thread(self.store.put, 'daily_stock', date + ':' + code, outcome)
                await asyncio.to_thread(self.store.put, 'morning_run', 'latest', {**run, 'stocks': dict(run['stocks'])})
            # Upper bound fanout; collect itself limits simultaneous provider jobs to two.
            for offset in range(0, len(codes), 40):
                await asyncio.gather(*(update(code) for code in codes[offset:offset + 40]))
            run['stage'] = '生成个人晨报与发送订阅'
            await asyncio.to_thread(self.store.put, 'morning_run', 'latest', run.copy())
            for owner in owners:
                if not await asyncio.to_thread(self.account_active, owner):
                    continue
                try:
                    # A date's report is retained; normal reruns do not rewrite it.
                    digest = await asyncio.to_thread(self.store.get, 'briefing', date, owner)
                    if not digest or manual:
                        digest = await asyncio.to_thread(self.generate, owner)
                    await self.deliver_digest(owner, digest)
                    run['users'] += 1
                except Exception as exc:
                    run['warnings'].append('一份个人晨报处理失败（' + type(exc).__name__ + '）')
            run['status'] = 'partial' if run['warnings'] or any(row['status'] != 'completed' for row in run['stocks'].values()) else 'completed'
            run['stage'] = '每日更新完成'
        except asyncio.CancelledError:
            run.update(status='interrupted', stage='服务停止；重启后可继续今日更新')
            raise
        except Exception as exc:
            run.update(status='failed', stage='每日更新失败', warnings=[type(exc).__name__])
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
            # Leave previously completed report state intact on an idempotent skip.
            if not (not manual and prior and prior.get('status') in ('completed', 'partial')):
                run['finished_at'] = timestamp()
                await asyncio.to_thread(self.store.put, 'morning_run', date, run)
                await asyncio.to_thread(self.store.put, 'morning_run', 'latest', run)
            await asyncio.to_thread(self.store.release, 'morning-global', token)
            self.runs.discard(asyncio.current_task())
        return run

    def generate(self, owner):
        digest = build_digest(self.store, owner)
        self.store.put('briefing', digest['date'], digest, owner)
        self.store.put('briefing', 'latest', digest, owner)
        return digest

    async def deliver_digest(self, owner, digest, test=False):
        from .briefing import deliver
        if not await asyncio.to_thread(self.account_active, owner):
            return {}
        subscription = await asyncio.to_thread(self.store.get, 'subscription', 'settings', owner, {})
        if not test and not subscription.get('enabled', True):
            return {}
        key = 'test-' + uuid.uuid4().hex if test else digest['date']
        lease_key = hashlib.sha256(('delivery:' + owner + ':' + key).encode()).hexdigest()
        token = await asyncio.to_thread(self.store.claim, lease_key, ttl=120)
        if not token:
            return {'status': 'busy'}
        try:
            record = await asyncio.to_thread(self.store.get, 'delivery', key, owner, {'at': timestamp(), 'date': digest['date'], 'kind': 'test' if test else 'daily', 'status': {}, 'channels': {}})
            for channel, field, enabled_field in [('email', 'email', 'email_enabled'), ('wechat', 'pushplus_token', 'wechat_enabled')]:
                recipient = subscription.get(field)
                if not recipient or not subscription.get(enabled_field, True):
                    continue
                recipient_hash = hashlib.sha256(recipient.encode()).hexdigest()
                state = record['channels'].get(channel, {})
                if state.get('recipient_hash') != recipient_hash:
                    state = {'attempts': 0, 'recipient_hash': recipient_hash}
                # Unknown/inflight might already have been accepted: never auto-resend.
                if state.get('status') in ('sent', 'accepted', 'unknown', 'sending') or state['attempts'] >= 3:
                    continue
                if state.get('retry_after', 0) > time.time():
                    continue
                state.update(status='sending', attempts=state['attempts'] + 1, attempted_at=timestamp())
                record['channels'][channel] = state
                record['status'][channel] = 'sending'
                await asyncio.to_thread(self.store.put, 'delivery', key, record, owner)
                one_channel = {field: recipient}
                outcome = (await deliver(one_channel, digest)).get(channel, 'failed')
                state['status'] = record['status'][channel] = outcome
                if outcome == 'not_configured':
                    state['attempts'] -= 1  # A missing SMTP account is not a send attempt.
                if outcome in ('failed', 'not_configured'):
                    state['retry_after'] = time.time() + 900
                record['at'] = timestamp()
                await asyncio.to_thread(self.store.put, 'delivery', key, record, owner)
            await asyncio.to_thread(self.store.put, 'delivery', 'latest', record, owner)
            return record
        finally:
            await asyncio.to_thread(self.store.release, lease_key, token)

    async def retry_deliveries(self):
        today = datetime.now(SHANGHAI).date().isoformat()
        for row in await asyncio.to_thread(self.store.list, 'delivery', limit=1000):
            if row['key'] != today or not any(state['status'] in ('failed', 'not_configured') and state['attempts'] < 3 and state.get('retry_after', 0) <= time.time() for state in row['value'].get('channels', {}).values()):
                continue
            digest = await asyncio.to_thread(self.store.get, 'briefing', today, row['owner'])
            if digest:
                await self.deliver_digest(row['owner'], digest)

    async def close(self):
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
        tasks = {task for task in (*self.runs, self.task, self.catchup_task) if task and not task.done()}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
