"""Operator-only, atomic import of a verified historical N2 paper book."""
import copy
import json
import math
import time
import uuid
from datetime import datetime

from ..news_cleaning import SHANGHAI
from ..paper_rules import PaperError, apply_trade, new_account
from .n2_book import digest, model_fees
from .n2_policy import N2_STRATEGY_ID, N2_VERSION
from .n2_corporate import apply_cash_event, cash_amounts, entitlement, cash_event_id

NAMESPACES = ('paper_account', 'paper_trade', 'quant_config', 'quant_decision',
              'quant_day', 'quant_runtime', 'quant_opening', 'peg_b_state',
              'n2_state', 'paper_replay', 'n2_daily_nav', 'paper_corporate')


def verify_replay(payload, *, now=None):
    """Independently rebuild integer money, lots, fees and realised PnL from fills."""
    if (payload.get('schema') != 1 or payload.get('strategy_id') != N2_STRATEGY_ID or
            payload.get('model_version') != N2_VERSION or
            payload.get('execution_mode') != 'daily_open_replay'):
        raise ValueError('历史账户的策略或成交口径不符')
    if payload.get('sha256') != digest({k:v for k,v in payload.items() if k != 'sha256'}):
        raise ValueError('历史回放文件校验失败')
    start, end = payload['start_date'], payload['end_date']
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    if start > end or end >= now.date().isoformat():
        raise ValueError('只允许导入已结束交易日的历史账户')
    account = new_account()
    seen = set()
    books = {}
    activities = [(t['executed_at'],1,t['sequence'],'trade',t) for t in payload['trades']]
    activities += [(e['effective_at'],0 if e['type']=='dividend_ex' else 2,e['corporate_revision'],'corporate',e)
                   for e in payload.get('corporate_events',[])]
    claims_seen = set()
    for _, _, _, kind, trade in sorted(activities,key=lambda x:x[:3]):
        if kind == 'corporate':
            event=trade;when=datetime.fromisoformat(event['effective_at']).astimezone(SHANGHAI)
            day=when.date().isoformat();paid=event['type']=='cash_dividend'
            gross,net=cash_amounts(event['quantity_entitled'],event['cash_per_share'])
            expected_at=event['pay_date' if paid else 'ex_date']+('T15:00:00+08:00' if paid else 'T09:00:00+08:00')
            key=(event['claim_id'],event['type'])
            if (key in claims_seen or event['event_id']!=cash_event_id(event['claim_id'],event['type'])
                    or event['effective_at']!=expected_at or not start<=day<=end
                    or not event['ann_date']<=event['record_date']<event['ex_date']<=event['pay_date']
                    or event['quantity_entitled']!=entitlement(payload['trades'],event['code'],event['record_date'])
                    or event['book_sequence']!=account['sequence'] or event['gross_fen']!=gross
                    or event['net_fen']!=net or event['tax_fen']!=gross-net or event['withholding_rate']!=.2
                    or event['cash_delta_fen']!=(net if paid else 0)
                    or event['receivable_delta_fen']!=(-net if paid else net)
                    or event['income_fen']!=(net if paid else 0)
                    or paid and (event['claim_id'],'dividend_ex') not in claims_seen):
                raise ValueError('历史分红权益或资金不符')
            account=apply_cash_event(account,event)
            if event.get('cash_after_fen')!=account['cash_fen']:
                raise ValueError('历史分红现金重建不一致')
            claims_seen.add(key);books[day]=copy.deepcopy(account)
            continue
        expected=account['sequence']+1
        when = datetime.fromisoformat(trade['executed_at']).astimezone(SHANGHAI)
        day = when.date().isoformat()
        request = str(uuid.UUID(trade['request_id']))
        if (request in seen or trade['sequence'] != expected or not start <= day <= end or
                trade.get('strategy_id') != N2_STRATEGY_ID or trade.get('model_version') != N2_VERSION or
                trade.get('execution_mode') != 'daily_open_replay' or trade.get('actor') != 'algorithm'):
            raise ValueError('历史成交编号、序号或时间不符')
        if expected > 1 and trade['executed_at'] < payload['trades'][expected-2]['executed_at']:
            raise ValueError('历史成交未按时间顺序排列')
        seen.add(request)
        costs = model_fees(trade['gross_fen'], trade['side'])
        if any(trade.get(k) != v for k,v in costs.items()):
            raise ValueError('历史成交20bp最低费用不符')
        quote = dict(price_fen=trade['price_fen'], as_of=trade['quote_as_of'],
                     fetched_at=trade['quote_fetched_at'], source=trade['source'])
        if quote['as_of'] != when.isoformat():
            raise ValueError('日线开盘回放时间与成交时间不符')
        account, actual = apply_trade(account, dict(code=trade['code'],name=trade['name']),
                                      trade['side'], trade['quantity'], quote, when,
                                      execution_costs=costs)
        for key in ('gross_fen', 'cash_after_fen', 'realized_pnl_fen'):
            if actual[key] != trade[key]:
                raise ValueError('历史账户资金重建不一致：'+key)
        account['sequence'] = expected
        if len(account['positions']) > 2:
            raise ValueError('历史成交中途超过双股名额')
        books[day] = copy.deepcopy(account)
    # Last-price fields are end-of-replay valuation references, not new trades.
    for code, position in account['positions'].items():
        close = payload['closing_prices'][code]
        if (type(close['price_fen']) is not int or close['price_fen'] <= 0 or
                close['as_of'][:10] > end):
            raise ValueError('历史期末估值不符')
        position.update(last_price_fen=close['price_fen'], last_price_as_of=close['as_of'])
    if account != payload['account']:
        raise ValueError('历史期末账户与重建账本不一致')
    state = payload['state']
    if (state['sequence'] != account['sequence'] or set(state['entries']) != set(account['positions']) or
            not set(state['pending']).issubset(account['positions']) or len(account['positions']) > 2):
        raise ValueError('历史持仓策略状态与账本不一致')
    for entry in state['entries'].values():
        if not start <= entry['date'] <= end or not math.isfinite(entry['adjusted_anchor_fen']) or entry['adjusted_anchor_fen'] <= 0:
            raise ValueError('历史首次建仓锚点无效')
    dates = [row['date'] for row in payload['daily_nav']]
    if not dates or dates != sorted(set(dates)) or dates[0] != start or dates[-1] != end:
        raise ValueError('历史日账本日期不完整')
    if {d['date'] for d in payload['decisions']} != set(dates):
        raise ValueError('历史决策与日账本不一致')
    from .n2_replay import trade_dates
    if dates != trade_dates(start, end):
        raise ValueError('历史日账本遗漏已核验交易日')
    book = new_account()
    for row in payload['daily_nav']:
        book = books.get(row['date'], book)
        positions = row['positions']
        if (row['cash_fen'] != book['cash_fen'] or row['sequence'] != book['sequence'] or row.get('receivable_fen',0)!=book.get('receivable_fen',0) or
                set(positions) != set(book['positions'])):
            raise ValueError('历史逐日资金与持仓重建不一致')
        for code, position in positions.items():
            if (position['quantity'] != book['positions'][code]['quantity'] or
                    position['cost_fen'] != book['positions'][code]['cost_fen'] or
                    type(position['close_fen']) is not int or position['close_fen'] <= 0 or
                    position['price_as_of'][:10] > row['date']):
                raise ValueError('历史逐日估值或数量无效')
        value = sum(p['quantity']*p['close_fen'] for p in positions.values())
        if row['market_value_fen'] != value or row['equity_fen'] != row['cash_fen']+value+row.get('receivable_fen',0):
            raise ValueError('历史逐日资产恒等式不成立')
    for decision in payload['decisions']:
        if decision['as_of'] >= decision['date'] or decision['model_version'] != N2_VERSION:
            raise ValueError('历史决策引用未来信号')
    return account


def import_n2_replay(service, owner, expected_revision, replay_id, payload, *, now=None):
    replay_id = str(uuid.UUID(replay_id))
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    account = verify_replay(payload, now=now)
    store, paper = service.store, service.paper.store
    with store.locked(owner) as conn:
        previous = paper._get(conn, 'paper_replay_archive', replay_id, owner, None)
        if previous:
            if previous['payload_sha256'] != payload['sha256'] or previous['requested_revision'] != expected_revision:
                raise PaperError('IDEMPOTENCY_CONFLICT', '历史回放编号已用于其他内容。')
            return previous['result']
        config = paper._get(conn, 'quant_config', 'config', owner, {})
        if config.get('revision', 0) != expected_revision:
            raise PaperError('AUTOMATION_CONFLICT', '托管配置已改变，未导入历史账户。')
        suffix = ' FOR UPDATE' if store.db.dialect == 'mysql' else ''
        lease = conn.execute("SELECT payload FROM research_records WHERE namespace=? AND record_key=? AND owner=?"+suffix,
                             ('lease', 'quant:'+owner, '')).fetchone()
        if lease and json.loads(lease['payload']).get('expires', 0) > time.time():
            raise PaperError('AUTOMATION_BUSY', '托管任务正在执行，请稍后重试同一导入编号。')
        placeholders = ','.join('?' for _ in NAMESPACES)
        rows = conn.execute('SELECT namespace,record_key,payload,created_at,updated_at FROM research_records '
                            f'WHERE owner=? AND namespace IN ({placeholders}) ORDER BY namespace,record_key',
                            (owner, *NAMESPACES)).fetchall()
        archived = [{**dict(row), 'payload': json.loads(row['payload'])} for row in rows]
        for trade in payload['trades']:
            if paper._get(conn, 'paper_retired_trade', trade['request_id'], owner, None):
                raise PaperError('IDEMPOTENCY_CONFLICT', '历史成交编号与已归档旧订单冲突。')
        revision = expected_revision+1
        peak = max([account['initial_cash_fen'], *[r['equity_fen'] for r in payload['daily_nav']]])
        new_config = dict(config, mode='algorithm', strategy_id=N2_STRATEGY_ID,
                          strategy_config={}, revision=revision, peak_equity_fen=peak,
                          reason=None, updated_at=int(time.time()))
        result = dict(replay_id=replay_id, revision=revision, start_date=payload['start_date'],
                      end_date=payload['end_date'], fills=len(payload['trades']),
                      trading_days=len(payload['daily_nav']), payload_sha256=payload['sha256'],
                      previous_records=len(archived), previous_sha256=digest(archived),
                      cash_dividends_fen=sum(e['income_fen'] for e in payload.get('corporate_events',[])))
        paper._put(conn, 'paper_replay_archive', replay_id, owner,
                   dict(records=archived, sha256=result['previous_sha256'], payload_sha256=payload['sha256'],
                        requested_revision=expected_revision, imported_at=now.isoformat(), result=result))
        for row in archived:
            if row['namespace'] == 'paper_trade':
                paper._put(conn, 'paper_retired_trade', row['record_key'], owner,
                           dict(replay_id=replay_id, sequence=row['payload']['sequence']))
        conn.execute(f'DELETE FROM research_records WHERE owner=? AND namespace IN ({placeholders})',
                     (owner, *NAMESPACES))
        paper._put(conn, 'paper_account', 'account', owner, account)
        paper._put(conn, 'quant_config', 'config', owner, new_config)
        paper._put(conn, 'n2_state', 'state', owner, payload['state'])
        paper._put(conn, 'paper_replay', 'active', owner,
                   dict(result, execution_mode='daily_open_replay', imported_at=now.isoformat(),
                        note='历史段按日线开盘价近似成交；未模拟原分钟模型的盘中价格路径、参与率和部分成交。'))
        for trade in payload['trades']:
            saved = copy.deepcopy(trade)
            saved['decision_key'] = saved['executed_at'][:10]+':'+str(revision).zfill(10)
            paper._put(conn, 'paper_trade', saved['request_id'], owner, saved)
        for decision in payload['decisions']:
            saved = copy.deepcopy(decision)
            saved.update(key=saved['date']+':'+str(revision).zfill(10), revision=revision)
            for fill in saved.get('fills',[]):
                fill['decision_key']=saved['key']
            paper._put(conn, 'quant_decision', saved['key'], owner, saved)
        for event in payload.get('corporate_events',[]):
            paper._put(conn, 'paper_corporate', event['event_id'], owner, event)
        for row in payload['daily_nav']:
            paper._put(conn, 'n2_daily_nav', row['date'], owner, row)
        # A migration after the execution window does not backdate a new live order.
        today = now.date().isoformat()
        if today > payload['end_date'] and now.hour*60+now.minute >= 612:
            key = today+':'+str(revision).zfill(10)
            paper._put(conn, 'quant_decision', key, owner,
                       dict(key=key, revision=revision, date=today, as_of=payload['end_date'],
                            strategy_id=N2_STRATEGY_ID, model_version=N2_VERSION, status='completed',
                            target_weights={}, orders=[], results=[], fills=[], execution_mode='handover',
                            reason='历史账户已导入；今日执行窗口已过，下一交易日按实时数据继续。'))
        paper._put(conn, 'quant_runtime', 'state', owner,
                   dict(status='ok', revision=revision, checked_at=now.isoformat(),
                        reason='历史日线开盘回放已导入，等待下一轮实时调仓。'))
    return result
