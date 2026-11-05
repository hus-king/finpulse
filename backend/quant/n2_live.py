"""Live N2 orchestration; money commits use the shared replay decision kernel."""
import asyncio
from datetime import datetime, time as clock_time, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import signal
import sys
import time
import uuid

from .. import providers
from ..news_cleaning import SHANGHAI
from ..paper_rules import PaperError, board_rules, new_account, valid_quote
from ..trading_calendar import is_trade_day, previous_trade_day
from .n2_book import (digest, fill_order, finish_plan, make_plan, prepare_orders,
                      refresh_entry_factors, seed_state)
from .n2_corporate import settle_cash_actions
from .n2_policy import (N2_STRATEGY_ID, N2_VERSION, price_exit_codes,
                        rank_candidates, select_portfolio)
from .peg_strategy import price_change_limit
from .store import default_config

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
MAX_ARTIFACT_BYTES = 128 * 1024**2
EXECUTION_NOTE = ('闭合分钟按结束标签解释；10:00固定目标，10:02提交卖单、10:07提交条件买单；'
                  '每分钟最多1%成交量，买入取最高价、卖出取最低价，按父订单累计收取至少20bp总费用。')


class N2Strategy:
    def metadata(self):
        return dict(id=N2_STRATEGY_ID, name='四因子 · 双股倒波动', version=N2_VERSION,
                    available=True, kind='n2', universe=[],
                    cadence='交易日10:00决策；10:02卖出、10:07买入',
                    description='PEG35.1%/动量24.9%/趋势15%/低波动25%；固定2股倒波动、前9保留、3次离榜确认、18%退出、20交易日冷却、5个百分点免微调。')

    def validate_config(self, config):
        if not isinstance(config, dict) or config:
            raise ValueError('双股倒波动策略采用已冻结规则，不接受自定义参数')
        return {}


def initialize_state(store, conn, owner):
    account = store.paper._get(conn, 'paper_account', 'account', owner, new_account())
    state = store.paper._get(conn, 'n2_state', 'state', owner, None)
    if state is None:
        rows = conn.execute('SELECT payload FROM research_records WHERE namespace=? AND owner=?',
                            ('paper_trade', owner)).fetchall()
        try:
            state = seed_state([json.loads(r['payload']) for r in rows], account)
        except (ValueError, KeyError, TypeError) as error:
            raise PaperError('N2_STATE_INVALID', '无法从成交记录恢复双股策略建仓锚点，请核验账户。') from error
        store.paper._put(conn, 'n2_state', 'state', owner, state)
    if state['sequence'] != account['sequence'] or set(state['entries']) != set(account['positions']):
        raise PaperError('N2_STATE_INVALID', '双股策略锚点与成交账本不一致。')
    return account, state


def _stamp(value):
    result = datetime.fromisoformat(value)
    return result.replace(tzinfo=SHANGHAI) if result.tzinfo is None else result.astimezone(SHANGHAI)


def _sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            result.update(block)
    return result.hexdigest()


def _input_shape(value, day):
    expected = previous_trade_day(datetime.fromisoformat(day).date())
    coverage = value.get('coverage', [])
    if (expected is None or value.get('date') != day or value.get('signal_date') != expected.isoformat()
            or not value.get('calendar') or value['calendar'][-1] != expected.isoformat()
            or len(coverage) != 300 or len({r.get('code') for r in coverage}) != 300
            or not isinstance(value.get('rows'), list) or not isinstance(value.get('histories'), dict)
            or not isinstance(value.get('provenance'), dict)):
        raise ValueError('双股策略输入日期或300股覆盖校验失败')
    return value


class N2InputService:
    """A leased disposable collector, with one immutable input artifact per day."""
    lease_seconds = 180
    timeout_seconds = 3600

    def __init__(self, records, *, clock=None):
        self.records = records
        self.clock = clock or (lambda: datetime.now(SHANGHAI))
        self.task = None
        self.process = None
        self._cached = None

    def load(self, day):
        meta = self.records.get('n2_inputs', day, default=None)
        if meta is None:
            meta = self.records.get('n2_inputs', 'latest', default={})
        if not meta or meta.get('date') != day:
            raise ValueError('双股策略当日输入尚未准备，保留持仓等待更新')
        try:
            path = Path(meta['path']).resolve(strict=True)
            directory = os.environ.get('FINPULSE_N2_DATA_DIR')
            if directory and not path.is_relative_to(Path(directory).resolve()):
                raise ValueError('artifact outside managed directory')
            identity = (str(path), meta['sha256'], day)
            if self._cached and self._cached[0] == identity:
                return self._cached[1], meta
            if not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES or _sha(path) != meta['sha256']:
                raise ValueError('artifact hash or size mismatch')
            value = _input_shape(json.loads(path.read_text()), day)
            if meta.get('signal_date') != value['signal_date']:
                raise ValueError('metadata cutoff mismatch')
        except (OSError, KeyError, ValueError, TypeError):
            raise ValueError('双股策略原始数据文件未通过日期及哈希校验，保留持仓') from None
        self._cached = (identity, value)
        return value, meta

    def load_actions(self, now):
        """Cash rights may use the last local artifact, including tonight's one."""
        today = now.astimezone(SHANGHAI).date()
        current = self.records.get('n2_inputs', today.isoformat(), default=None)
        if current is not None:
            return self.load(today.isoformat())[0]
        meta = self.records.get('n2_inputs', 'latest', default=None)
        if not meta:
            return {}
        day = datetime.fromisoformat(meta['date']).date()
        if day > today+timedelta(days=1):
            raise ValueError('分红输入超过可用日期，暂停结算与新增交易')
        return self.load(day.isoformat())[0]

    def desired_day(self, now):
        today = now.date()
        trading_today = is_trade_day(today)
        if trading_today is None:
            return None
        clock = now.time().replace(tzinfo=None)
        if trading_today and clock < clock_time(10):
            return today.isoformat()
        if clock < clock_time(21, 5):
            return None
        tomorrow = today + timedelta(days=1)
        return tomorrow.isoformat() if is_trade_day(tomorrow) is True else None

    async def maybe_prepare(self, configs):
        active = [(owner, cfg) for owner, cfg in configs if cfg.get('strategy_id') == N2_STRATEGY_ID]
        if not active or self.task and not self.task.done():
            return
        day = self.desired_day(self.clock().astimezone(SHANGHAI))
        if day is None:
            return
        try:
            await asyncio.to_thread(self.load, day)
            return
        except ValueError:
            pass
        names = ('FINPULSE_N2_BASE_DIR', 'FINPULSE_N2_DATA_DIR', 'FINPULSE_N2_PROVIDER_CONFIG')
        if not all(os.environ.get(name) for name in names):
            return
        attempt = await asyncio.to_thread(self.records.get, 'n2_inputs', 'attempt', default={})
        if attempt.get('date') == day and attempt.get('retry_at', 0) > time.time():
            return
        extras = set()
        for owner, _ in active:
            book = await asyncio.to_thread(self.records.get, 'paper_account', 'account', owner, default={})
            extras.update(book.get('positions', {}))
        self.task = asyncio.create_task(self._run(day, sorted(extras)))

    async def _stop_process(self):
        process = self.process
        if process is None or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), 5)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    def _publish(self, run, metadata, token):
        now = int(time.time())
        with self.records.db.transaction() as conn:
            suffix = ' FOR UPDATE' if self.records.db.dialect == 'mysql' else ''
            row = conn.execute('SELECT payload FROM research_records WHERE namespace=? AND record_key=? AND owner=?' + suffix,
                               ('lease', 'n2-daily-global', '')).fetchone()
            lease = json.loads(row['payload']) if row else {}
            if lease.get('token') != token or lease.get('expires', 0) <= time.time():
                raise ValueError('N2 preparation lease lost')
            old = conn.execute('SELECT payload FROM research_records WHERE namespace=? AND record_key=? AND owner=?',
                               ('n2_inputs', 'latest', '')).fetchone()
            previous = json.loads(old['payload']) if old else {}
            values = [('n2_inputs', metadata['date'], metadata), ('n2_inputs', 'attempt', run)]
            if not previous or previous.get('date', '') <= metadata['date']:
                values.append(('n2_inputs', 'latest', metadata))
            sql = 'INSERT INTO research_records(namespace,record_key,owner,payload,created_at,updated_at) VALUES(?,?,?,?,?,?) '
            sql += ('ON DUPLICATE KEY UPDATE payload=VALUES(payload),updated_at=VALUES(updated_at)' if self.records.db.dialect == 'mysql'
                    else 'ON CONFLICT(namespace,record_key,owner) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at')
            for namespace, key, value in values:
                conn.execute(sql, (namespace, key, '', json.dumps(value, ensure_ascii=False, allow_nan=False), now, now))

    def _finish_attempt(self, run):
        with self.records.db.transaction() as conn:
            suffix = ' FOR UPDATE' if self.records.db.dialect == 'mysql' else ''
            row = conn.execute('SELECT payload FROM research_records WHERE namespace=? AND record_key=? AND owner=?' + suffix,
                               ('n2_inputs', 'attempt', '')).fetchone()
            previous = json.loads(row['payload']) if row else {}
            if previous.get('id') == run['id'] and previous.get('status') != 'complete':
                conn.execute('UPDATE research_records SET payload=?,updated_at=? WHERE namespace=? AND record_key=? AND owner=?',
                             (json.dumps(run, ensure_ascii=False, allow_nan=False), int(time.time()), 'n2_inputs', 'attempt', ''))

    async def _run(self, day, extras):
        token = await asyncio.to_thread(self.records.claim, 'n2-daily-global', self.lease_seconds)
        if not token:
            return
        owner = asyncio.current_task()
        pulse = None
        run = dict(id=uuid.uuid4().hex, date=day, status='running', started_at=self.clock().isoformat())
        try:
            try:
                await asyncio.to_thread(self.load, day)
                return
            except ValueError:
                pass
            await asyncio.to_thread(self.records.put, 'n2_inputs', 'attempt', run.copy())
            directory = Path(os.environ['FINPULSE_N2_DATA_DIR']).resolve() / 'runs' / day / run['id']
            directory.mkdir(parents=True, exist_ok=False, mode=0o700)
            artifact = directory / 'inputs.json'
            async def heartbeat():
                while True:
                    await asyncio.sleep(30)
                    if not await asyncio.to_thread(self.records.renew, 'n2-daily-global', token, self.lease_seconds):
                        owner.cancel()
                        return
            pulse = asyncio.create_task(heartbeat())
            pulse.add_done_callback(lambda task: owner.cancel() if not task.cancelled() and task.exception() else None)
            command = [sys.executable, str(ROOT/'scripts/quant/refresh_n2.py'),
                       '--base', os.environ['FINPULSE_N2_BASE_DIR'],
                       '--overlay', str(Path(os.environ['FINPULSE_N2_DATA_DIR'])/'raw-overlay'),
                       '--config', os.environ['FINPULSE_N2_PROVIDER_CONFIG'],
                       '--decision-day', day, '--artifact', str(artifact)]
            for code in extras:
                command.extend(['--extra-code', code])
            with (directory/'collection.log').open('wb') as log:
                self.process = await asyncio.create_subprocess_exec(*command, cwd=ROOT,
                    stdout=log, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
                    env={**os.environ, 'OMP_NUM_THREADS':'1', 'OPENBLAS_NUM_THREADS':'1', 'MKL_NUM_THREADS':'1'})
                result = await asyncio.wait_for(self.process.wait(), self.timeout_seconds)
            if result:
                raise ValueError('N2 bounded input worker failed')
            if artifact.stat().st_size > MAX_ARTIFACT_BYTES:
                raise ValueError('N2 artifact exceeded size budget')
            inputs = await asyncio.to_thread(lambda: _input_shape(json.loads(artifact.read_text()), day))
            metadata = dict(date=day, signal_date=inputs['signal_date'], path=str(artifact),
                            sha256=await asyncio.to_thread(_sha, artifact), prepared_at=self.clock().isoformat())
            run.update(status='complete', finished_at=self.clock().isoformat())
            await asyncio.to_thread(self._publish, run, metadata, token)
        except asyncio.CancelledError:
            run.update(status='interrupted', reason='双股策略输入准备已中断', retry_at=time.time()+900)
            await asyncio.to_thread(self._finish_attempt, run.copy())
            raise
        except Exception:
            LOG.warning('N2 input preparation failed; private worker log retained')
            run.update(status='failed', reason='双股策略输入更新失败，稍后重试', retry_at=time.time()+900,
                       finished_at=self.clock().isoformat())
            await asyncio.to_thread(self._finish_attempt, run.copy())
        finally:
            if pulse:
                pulse.cancel()
                await asyncio.gather(pulse, return_exceptions=True)
            await self._stop_process()
            await asyncio.to_thread(self.records.release, 'n2-daily-global', token)

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self._stop_process()


def _cash_owners(service, configs):
    owners = {owner: cfg for owner, cfg in configs if cfg.get('strategy_id') == N2_STRATEGY_ID}
    with service.store.db.connection() as conn:
        rows = conn.execute("SELECT r.owner FROM research_records r JOIN users u ON u.id=r.owner "
                            "WHERE r.namespace='n2_state' AND r.record_key='state' AND u.is_active=1").fetchall()
    for row in rows:
        owner = row['owner']
        if owner not in owners:
            owners[owner] = service.store.config(owner)
    return owners


def _cash_config(service, conn, owner, cfg):
    current = service.paper.store._get(conn, 'quant_config', 'config', owner, default_config())
    if any(current.get(k) != cfg.get(k) for k in ('revision', 'mode', 'strategy_id', 'strategy_config')):
        raise PaperError('AUTOMATION_CHANGED', '分红结算期间账户配置已改变，下一轮重新核验。')
    return current


def settle_owner_cash(service, owner, cfg, now, inputs):
    """Post entitlements and money with the actual owner book, even while paused."""
    with service.store.locked(owner) as conn:
        _cash_config(service, conn, owner, cfg)
        paper = service.paper.store
        state = paper._get(conn, 'n2_state', 'state', owner, None)
        if state is None:
            return []
        account = paper._get(conn, 'paper_account', 'account', owner, new_account())
        if state['sequence'] != account['sequence']:
            raise ValueError('分红结算的策略状态与成交账本序号不一致')
        rows = conn.execute('SELECT payload FROM research_records WHERE namespace=? AND owner=?',
                            ('paper_trade', owner)).fetchall()
        trades = sorted((json.loads(row['payload']) for row in rows), key=lambda t:t['sequence'])
        trades = [t for t in trades if t['sequence'] <= account['sequence'] and _stamp(t['executed_at']) <= now]
        updated, updated_state, events = settle_cash_actions(account, state, trades,
                                                            inputs.get('dividends', {}), now)
        for event in events:
            if paper._get(conn, 'paper_corporate', event['event_id'], owner, None) is not None:
                raise ValueError('公司现金权益记录与已登记状态不一致，暂停重复入账')
            paper._put(conn, 'paper_corporate', event['event_id'], owner, event)
        if updated != account:
            paper._put(conn, 'paper_account', 'account', owner, updated)
        if updated_state != state:
            paper._put(conn, 'n2_state', 'state', owner, updated_state)
        runtime = paper._get(conn, 'quant_runtime', 'state', owner, {})
        if runtime.get('component') == 'n2_cash':
            paper._put(conn, 'quant_runtime', 'state', owner,
                       dict(status='ok', revision=cfg['revision'], checked_at=now.isoformat(), component='n2_cash'))
        return events


def _cash_failure(service, owner, cfg, now, reason):
    with service.store.locked(owner) as conn:
        _cash_config(service, conn, owner, cfg)
        service.paper.store._put(conn, 'quant_runtime', 'state', owner,
                                dict(status='error', component='n2_cash', revision=cfg['revision'],
                                     checked_at=now.isoformat(), reason='公司现金权益结算失败：'+reason))


async def settle_n2_cash(service, configs, now):
    """No network: one bad book must not block another owner's entitlements."""
    owners = await asyncio.to_thread(_cash_owners, service, configs)
    failed = set()
    for owner, cfg in owners.items():
        try:
            inputs = await asyncio.to_thread(service.n2_inputs.load_actions, now)
            await asyncio.to_thread(settle_owner_cash, service, owner, cfg, now, inputs)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failed.add(owner)
            reason = str(error) if isinstance(error, (PaperError, ValueError, KeyError, TypeError)) else '请核验现金权益与账本'
            if not isinstance(error, (PaperError, ValueError, KeyError, TypeError)):
                LOG.exception('N2 cash settlement failed owner=%s', owner)
            try:
                await asyncio.to_thread(_cash_failure, service, owner, cfg, now, reason)
            except PaperError:
                pass  # The new configuration owns its runtime status.
            except Exception:
                LOG.exception('N2 cash runtime could not be saved owner=%s', owner)
    return failed


def _guard(service, conn, owner, cfg, token):
    store, paper = service.store, service.paper.store
    current = paper._get(conn, 'quant_config', 'config', owner, default_config())
    if (current.get('mode') != 'algorithm' or current.get('strategy_id') != N2_STRATEGY_ID
            or current.get('revision') != cfg['revision'] or current.get('strategy_config', {})):
        raise PaperError('AUTOMATION_CHANGED', '双股策略配置已改变，本轮订单停止。')
    suffix = ' FOR UPDATE' if store.db.dialect == 'mysql' else ''
    row = conn.execute('SELECT payload FROM research_records WHERE namespace=? AND record_key=? AND owner=?' + suffix,
                       ('lease', 'quant:'+owner, '')).fetchone()
    lease = json.loads(row['payload']) if row else {}
    if lease.get('token') != token or lease.get('expires', 0) <= time.time():
        raise PaperError('QUANT_LEASE_LOST', '双股策略执行租约已失效。')
    return current


def _expire_old(service, conn, owner, day):
    paper = service.paper.store
    rows = conn.execute('SELECT record_key,payload FROM research_records WHERE namespace=? AND owner=? AND record_key<?',
                        ('quant_decision', owner, day)).fetchall()
    for row in rows:
        plan = json.loads(row['payload'])
        if plan.get('strategy_id') == N2_STRATEGY_ID and plan.get('status') == 'running':
            plan.update(status='expired', reason='上一交易日的双股计划已过期；保留实际成交与持仓。')
            paper._put(conn, 'quant_decision', row['record_key'], owner, plan)


def _latch_day(service, owner, cfg, token, inputs):
    with service.store.locked(owner) as conn:
        _guard(service, conn, owner, cfg, token)
        account, state = initialize_state(service.store, conn, owner)
        state = refresh_entry_factors(state, inputs['histories'], inputs['signal_date'])
        state['pending'].update(price_exit_codes(state, inputs['histories'], inputs['signal_date']))
        ranked = rank_candidates(inputs['rows'], inputs['histories'], inputs['calendar'], inputs['signal_date'])
        sectors = {r['code']:r.get('industry_l1') for r in ranked}
        sectors.update(inputs.get('sectors', {}))
        selection, state = select_portfolio(ranked, account['positions'], state, inputs['date'],
                                             [*inputs['calendar'], inputs['date']], sectors)
        service.paper.store._put(conn, 'n2_state', 'state', owner, state)
        return account, state, selection


def _save_plan(service, owner, cfg, key, token, plan, state, sequence):
    with service.store.locked(owner) as conn:
        _guard(service, conn, owner, cfg, token)
        previous = service.paper.store._get(conn, 'quant_decision', key, owner, None)
        if previous:
            return previous
        account, current = initialize_state(service.store, conn, owner)
        if account['sequence'] != sequence or digest(current) != digest(state):
            raise PaperError('AUTOMATION_CHANGED', '双股策略建仓状态已变化，请重新评估。')
        _expire_old(service, conn, owner, plan['date'])
        service.paper.store._put(conn, 'quant_decision', key, owner, plan)
        return plan


def _plan_guard(service, conn, owner, cfg, key, token):
    _guard(service, conn, owner, cfg, token)
    plan = service.paper.store._get(conn, 'quant_decision', key, owner, None)
    if (not plan or plan.get('strategy_id') != N2_STRATEGY_ID or plan.get('model_version') != N2_VERSION
            or plan.get('revision') != cfg['revision'] or plan.get('status') != 'running'
            or plan.get('date') != service.clock().astimezone(SHANGHAI).date().isoformat()):
        raise PaperError('AUTOMATION_CHANGED', '双股策略计划已过期、结束或版本不符。')
    account, state = initialize_state(service.store, conn, owner)
    if plan.get('book_sequence') != account['sequence']:
        raise PaperError('N2_STATE_INVALID', '双股计划与当前账本序号不一致。')
    return plan, account, state


def _progress(service, owner, cfg, key, token, *, side=None, **changes):
    with service.store.locked(owner) as conn:
        plan, account, _ = _plan_guard(service, conn, owner, cfg, key, token)
        if side:
            prepare_orders(plan, owner+':'+str(cfg['revision']), side, account)
        plan.update(changes)
        service.paper.store._put(conn, 'quant_decision', key, owner, plan)
        return plan


def _bar_for_fill(cached, stamp, plan, order, now):
    valid_quote(cached, now)
    if cached.get('volume_unit', 'shares') != 'shares':
        raise PaperError('QUOTE_UNAVAILABLE', '双股分钟成交量单位未核验。')
    end = _stamp(stamp)
    begin = end-timedelta(minutes=1)
    low, high = (603, 606) if order['side'] == 'sell' else (608, 611)
    minute = end.hour*60+end.minute
    if (end.date().isoformat() != plan['date'] or end.second or end.microsecond or not low <= minute <= high
            or end > now or _stamp(plan['created_at']) > begin):
        raise PaperError('N2_WINDOW_CLOSED', '分钟尚未闭合、早于计划提交或不在本轮成交窗口。')
    bars = [b for b in cached['candles'] if _stamp(b['date']) == end]
    if len(bars) != 1:
        raise PaperError('QUOTE_UNAVAILABLE', '本轮原始分钟行情缺失或重复。')
    bar = bars[0]
    try:
        values = [float(bar[k]) for k in ('open', 'close', 'high', 'low', 'volume')]
    except (KeyError, ValueError, TypeError, OverflowError):
        raise PaperError('QUOTE_UNAVAILABLE', '本轮原始分钟OHLC或成交量无效。') from None
    if (not all(math.isfinite(v) for v in values) or min(values[:4]) <= 0 or values[4] <= 0
            or values[3] > min(values[0], values[1]) or values[2] < max(values[0], values[1]) or values[3] > values[2]):
        raise PaperError('QUOTE_UNAVAILABLE', '本轮原始分钟OHLC或成交量无效。')
    return bar, end


def commit_bar(service, owner, cfg, key, token, order_id, code, stamp, expected_sha):
    """One real closed-bar fill, serialized with money/config/state and UUID replay."""
    paper = service.paper.store
    bar_id = code+':'+_stamp(stamp).isoformat()
    request_id = str(uuid.uuid5(uuid.UUID(order_id), bar_id))
    with service.store.locked(owner) as conn:
        previous = paper._get(conn, 'paper_trade', request_id, owner, None)
        if previous:
            if previous.get('parent_order_id') != order_id or previous.get('code') != code:
                raise PaperError('IDEMPOTENCY_CONFLICT', '双股成交编号已被另一笔订单使用。')
            return paper._public(previous)
        if paper._get(conn, 'paper_retired_trade', request_id, owner, None):
            raise PaperError('ACCOUNT_RESET', '旧双股成交已归档。')
        plan, account, state = _plan_guard(service, conn, owner, cfg, key, token)
        now = service.clock().astimezone(SHANGHAI)
        if now.hour*60+now.minute > 612:
            raise PaperError('N2_WINDOW_CLOSED', '双股本轮订单已结束，保留实际持仓。')
        order = next((o for o in plan['orders'] if o.get('id') == order_id and o['code'] == code), None)
        if order is None or order['side'] == 'buy' and not plan.get('sell_done'):
            raise PaperError('AUTOMATION_CHANGED', '双股父订单或卖出阶段未确认。')
        cached = paper._get(conn, 'minute_market', code+':1', '', {})
        bar, end = _bar_for_fill(cached, stamp, plan, order, now)
        if digest(bar) != expected_sha:
            raise PaperError('QUOTE_CHANGED', '原始分钟行情已变化，等待重新核验。')
        name = next((r.get('name', code) for r in plan['ranked'] if r['code'] == code), account['positions'].get(code, {}).get('name', code))
        if 'ST' in name.upper() or '退' in name:
            raise PaperError('QUANT_PRICE_LIMIT', '风险警示或退市证券暂停自动成交。')
        price = int((Decimal(str(bar['high'] if order['side'] == 'buy' else bar['low']))*100).to_integral_value(
                    rounding=ROUND_CEILING if order['side'] == 'buy' else ROUND_FLOOR))
        reference = plan['previous_close'].get(code)
        if not reference or abs(price/100/reference-1) >= price_change_limit(code):
            raise PaperError('QUANT_PRICE_LIMIT', '模型不利成交价靠近涨跌停边界，暂停成交。')
        if order['side'] == 'buy':
            peers = account['positions']
            if (len(peers) > 2 or code not in peers and len(peers) >= 2 or not plan['sectors'].get(code)
                    or any(c != code and plan['sectors'].get(c) == plan['sectors'][code] for c in peers)):
                raise PaperError('QUANT_RISK_LIMIT', '双股策略要求最多2只且每个一级行业最多1只。')
        usage = plan.setdefault('bar_usage', {})
        capacity = max(0, math.floor(float(bar['volume'])*.01)-usage.get(bar_id, 0))
        updated, updated_state, trade = fill_order(account, state, plan, order, price, end,
            capacity=capacity, partial=True, factor=None,
            source='AkShare / 新浪原始闭合分钟；双股1%参与率/不利价/20bp模型',
            fetched_at=cached['fetched_at'], bar_id=bar_id)
        if trade is None:
            return None
        trade.update(processed_at=now.isoformat(), bar_sha256=expected_sha, bar_end=end.isoformat(),
                     bar_volume_shares=bar['volume'], participation_limit=.01)
        usage[bar_id] = usage.get(bar_id, 0)+trade['quantity']
        plan['book_sequence'] = updated['sequence']
        legacy = paper._get(conn, 'peg_b_state', 'state', owner, None)
        if legacy is not None:
            from .buffered_peg import update_fill_state
            update_fill_state(legacy, account, updated, trade)
            paper._put(conn, 'peg_b_state', 'state', owner, legacy)
        for namespace, record_key, value in [('paper_account', 'account', updated),
                ('n2_state', 'state', updated_state), ('paper_trade', trade['request_id'], trade),
                ('quant_decision', key, plan)]:
            paper._put(conn, namespace, record_key, owner, value)
        return paper._public(trade)


async def _consume(service, owner, cfg, key, token, side):
    plan = await asyncio.to_thread(_progress, service, owner, cfg, key, token, side=side)
    covered = True
    queue = []
    skipped = dict(plan.get('skipped_targets', {}))
    for order in plan['orders']:
        if order['side'] != side or order['filled'] >= order['quantity']:
            continue
        code = order['code']
        try:
            raw = await service.fresh(code)
            quote = valid_quote(raw, service.clock())
            last = _stamp(quote['as_of'])
            if side == 'sell' and last.hour*60+last.minute < 606:
                covered = False
            for bar in raw['candles']:
                end = _stamp(bar['date'])
                low, high = (603, 606) if side == 'sell' else (608, 611)
                if (end.date().isoformat() == plan['date'] and low <= end.hour*60+end.minute <= high
                        and end <= service.clock().astimezone(SHANGHAI)
                        and _stamp(plan['created_at']) <= end-timedelta(minutes=1)):
                    queue.append((end, code, order['id'], bar['date'], digest(bar)))
        except (PaperError, ValueError, providers.ProviderError):
            covered = False
            skipped[code] = '有效分钟行情尚未就绪，保留未成交订单和实际持仓'
        if not await asyncio.to_thread(service.store.records.renew, 'quant:'+owner, token, 300):
            raise PaperError('QUANT_LEASE_LOST', '双股执行租约已失效。')
    for _, code, order_id, stamp, wanted in sorted(queue):
        try:
            await asyncio.to_thread(commit_bar, service, owner, cfg, key, token, order_id, code, stamp, wanted)
        except PaperError as error:
            if error.code in ('AUTOMATION_CHANGED', 'QUANT_LEASE_LOST', 'IDEMPOTENCY_CONFLICT', 'ACCOUNT_RESET', 'N2_STATE_INVALID'):
                raise
            skipped[code] = error.message
    return await asyncio.to_thread(_progress, service, owner, cfg, key, token, skipped_targets=skipped), covered


def _complete(service, owner, cfg, key, token):
    with service.store.locked(owner) as conn:
        plan, account, _ = _plan_guard(service, conn, owner, cfg, key, token)
        for order in plan['orders']:
            if order['filled'] < order['quantity']:
                plan['skipped_targets'].setdefault(order['code'], '执行窗口结束；未成交部分保留资金或持仓，下个交易日重新评估')
        plan.update(sell_done=True, buy_done=True)
        finish_plan(plan, account)
        service.paper.store._put(conn, 'quant_decision', key, owner, plan)
        return plan


def _late_skip(service, owner, cfg, key, token):
    with service.store.locked(owner) as conn:
        _guard(service, conn, owner, cfg, token)
        previous = service.paper.store._get(conn, 'quant_decision', key, owner, None)
        if previous:
            return previous
        day = service.clock().astimezone(SHANGHAI).date().isoformat()
        _expire_old(service, conn, owner, day)
        plan = dict(key=key, revision=cfg['revision'], strategy_id=N2_STRATEGY_ID, strategy_config={},
                    model_version=N2_VERSION, date=day, status='completed', target_weights={}, orders=[], results=[],
                    created_at=service.clock().isoformat(), execution_mode='live_minute',
                    reason='已错过双股策略今日10:02–10:11窗口；保留实际持仓，下一交易日重新评估。')
        service.paper.store._put(conn, 'quant_decision', key, owner, plan)
        return plan


async def run_n2(service, owner, cfg, key, token):
    now = service.clock().astimezone(SHANGHAI)
    minute = now.hour*60+now.minute
    if minute < 600:
        return
    plan = await asyncio.to_thread(service.store.records.get, 'quant_decision', key, owner, default=None)
    if plan is None:
        if minute >= 612:
            await asyncio.to_thread(_late_skip, service, owner, cfg, key, token)
            return
        inputs, meta = await asyncio.to_thread(service.n2_inputs.load, now.date().isoformat())
        await asyncio.to_thread(settle_owner_cash, service, owner, cfg, now, inputs)
        account, state, selection = await asyncio.to_thread(_latch_day, service, owner, cfg, token, inputs)
        needed = set(account['positions']) | set(selection['selected'])
        marks = {}
        from .buffered_execution import decision_mark
        for code in sorted(needed):
            board_rules(code)
            raw = await service.fresh(code)
            marks[code] = decision_mark(raw, service.clock())
            if not await asyncio.to_thread(service.store.records.renew, 'quant:'+owner, token, 300):
                raise PaperError('QUANT_LEASE_LOST', '双股计划租约已失效。')
        plan, planned_state = make_plan(account, state, inputs, marks, mode='live_minute', created_at=service.clock().isoformat())
        plan.update(key=key, revision=cfg['revision'], strategy_config={}, book_sequence=account['sequence'],
                    input_artifact_sha256=meta['sha256'], execution_note=EXECUTION_NOTE,
                    previous_close={c:inputs['histories'][c]['raw'][-1]['close'] for c in needed},
                    scores={r['code']:r['score'] for r in plan['ranked']})
        plan = await asyncio.to_thread(_save_plan, service, owner, cfg, key, token, plan, planned_state, account['sequence'])
    if plan.get('status') != 'running':
        return
    if plan.get('model_version') != N2_VERSION:
        raise ValueError('双股计划版本已改变，请暂停后重新启用')
    current = service.clock().astimezone(SHANGHAI)
    minute = current.hour*60+current.minute
    if minute > 612:
        await asyncio.to_thread(_complete, service, owner, cfg, key, token)
        return
    if minute >= 602 and not plan['sell_done']:
        plan, covered = await _consume(service, owner, cfg, key, token, 'sell')
        current = service.clock().astimezone(SHANGHAI)
        minute = current.hour*60+current.minute
        if minute >= 607 and (covered or minute >= 612):
            plan = await asyncio.to_thread(_progress, service, owner, cfg, key, token, sell_done=True)
    if minute >= 607 and plan['sell_done']:
        plan, _ = await _consume(service, owner, cfg, key, token, 'buy')
    current = service.clock().astimezone(SHANGHAI)
    minute = current.hour*60+current.minute
    if minute >= 612:
        await asyncio.to_thread(_complete, service, owner, cfg, key, token)
