"""Explicit operator maintenance; no public reset route or synthetic fills."""
import hashlib
import json
import time
import uuid
from datetime import datetime, time as clock_time, timedelta

from ..news_cleaning import SHANGHAI
from ..paper_rules import PaperError, new_account
from ..trading_calendar import is_trade_day
from .peg_strategy import PEG_STRATEGY_ID

RESET_NAMESPACES = ('paper_account', 'paper_trade', 'quant_config', 'quant_decision',
                    'quant_day', 'quant_runtime', 'quant_opening', 'peg_b_state')


def opening_date(now):
    for offset in range(32):
        day = now.date() + timedelta(days=offset)
        trading = is_trade_day(day)
        if trading is None:
            break
        if trading and (offset or now.time().replace(tzinfo=None) < clock_time(14, 57)):
            return day.isoformat()
    raise PaperError('CALENDAR_UNKNOWN', '无法核验下一交易日，未重置账户。')


def reset_peg_account(service, owner, expected_revision, reset_id, now=None):
    """Archive and reset one idle account atomically; a UUID retry never resets twice."""
    reset_id = str(uuid.UUID(reset_id))
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    store, paper = service.store, service.paper.store
    with store.locked(owner) as conn:
        previous = paper._get(conn, 'paper_reset_archive', reset_id, owner, None)
        if previous:
            if previous['requested_revision'] != expected_revision:
                raise PaperError('IDEMPOTENCY_CONFLICT', '重置编号已用于另一版本的账户。')
            return previous['result']
        old = paper._get(conn, 'quant_config', 'config', owner, {})
        if old.get('revision', 0) != expected_revision:
            raise PaperError('AUTOMATION_CONFLICT', '托管配置已改变，未重置账户。')
        suffix = ' FOR UPDATE' if store.db.dialect == 'mysql' else ''
        lease = conn.execute("SELECT payload FROM research_records WHERE namespace='lease' AND record_key=? AND owner=''" + suffix,
                             ('quant:' + owner,)).fetchone()
        if lease and json.loads(lease['payload']).get('expires', 0) > time.time():
            raise PaperError('AUTOMATION_BUSY', '托管任务正在执行，请等待完成后重置。')
        placeholders = ','.join('?' for _ in RESET_NAMESPACES)
        rows = conn.execute('SELECT namespace,record_key,payload,created_at,updated_at FROM research_records '
                            f'WHERE owner=? AND namespace IN ({placeholders}) ORDER BY namespace,record_key',
                            (owner, *RESET_NAMESPACES)).fetchall()
        archived = [{**dict(row), 'payload': json.loads(row['payload'])} for row in rows]
        digest = hashlib.sha256(json.dumps(archived, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        day = opening_date(now)
        account = new_account()
        config = {**old, 'mode': 'algorithm', 'strategy_id': PEG_STRATEGY_ID,
                  'strategy_config': {'top_n': 5, 'group': 'all'}, 'revision': expected_revision + 1,
                  'peak_equity_fen': account['initial_cash_fen'], 'reason': None, 'updated_at': int(time.time())}
        result = {'reset_id': reset_id, 'revision': config['revision'], 'opening_date': day,
                  'cash_fen': account['cash_fen'], 'archived_records': len(archived), 'archive_sha256': digest}
        paper._put(conn, 'paper_reset_archive', reset_id, owner,
                   {'requested_revision': expected_revision, 'created_at': now.isoformat(),
                    'records': archived, 'sha256': digest, 'result': result})
        for row in archived:
            if row['namespace'] == 'paper_trade':
                paper._put(conn, 'paper_retired_trade', row['record_key'], owner,
                           {'reset_id': reset_id, 'sequence': row['payload']['sequence']})
        # Archive and active-ledger removal commit with the new account/config.
        conn.execute(f'DELETE FROM research_records WHERE owner=? AND namespace IN ({placeholders})',
                     (owner, *RESET_NAMESPACES))
        paper._put(conn, 'paper_account', 'account', owner, account)
        paper._put(conn, 'quant_config', 'config', owner, config)
        paper._put(conn, 'quant_opening', 'once', owner,
                   {'reset_id': reset_id, 'strategy_id': PEG_STRATEGY_ID, 'config_revision': config['revision'], 'trade_date': day})
    return result
