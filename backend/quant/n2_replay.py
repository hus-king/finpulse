"""Causal daily-open approximation for an explicitly requested historical book."""
import copy
import gc
import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from ..news_cleaning import SHANGHAI
from ..paper_rules import new_account, PaperError
from ..trading_calendar import is_trade_day
from .n2_book import (digest, factor_on, fill_order, finish_plan, make_plan, money,
                      prepare_orders, refresh_entry_factors)
from .n2_inputs import load_inputs
from .n2_corporate import settle_cash_actions
from .n2_policy import N2_STRATEGY_ID, N2_VERSION, initial_state


def trade_dates(start, end):
    day, last = date.fromisoformat(start), date.fromisoformat(end)
    dates = []
    while day <= last:
        known = is_trade_day(day)
        if known is None:
            raise ValueError('历史回放交易日历尚未核验')
        if known:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    if not dates:
        raise ValueError('历史回放区间没有交易日')
    return dates


def execution_bar(inputs, code, day):
    return inputs['daily'].get(code, {}).get(day)


def close_mark(inputs, code, day):
    bar = execution_bar(inputs, code, day)
    if bar:
        return dict(price_fen=money(bar['close']), as_of=day+'T15:00:00+08:00')
    history = inputs['histories'][code]['raw']
    if not history:
        raise ValueError('历史持仓缺少估值价格')
    last = history[-1]
    return dict(price_fen=money(last['close']), as_of=last['date']+'T15:00:00+08:00')


def replay(base, overlay, start, end, owner_key, *, input_loader=load_inputs, output=None):
    dates = trade_dates(start, end)
    now = datetime.now(SHANGHAI).isoformat()
    account, state = new_account(), initial_state()
    trades, decisions, nav, corporate, source_proofs = [], [], [], [], {}
    closing = {}
    for day in dates:
        inputs = input_loader(base, overlay, day, extra_codes=list(account['positions']))
        if inputs['date'] != day or inputs['signal_date'] >= day:
            raise ValueError('历史回放输入日期未对齐')
        account, state, events = settle_cash_actions(account, state, trades, inputs.get('dividends', {}),
            datetime.fromisoformat(day+'T09:30:00+08:00'))
        corporate.extend(events)
        state = refresh_entry_factors(state, inputs['histories'], inputs['signal_date'])
        # The approximation knows only t-1 signals at an actual day-t opening.
        # Later daily high/low/close never choose targets or size orders.
        marks = {}
        for code, history in inputs['histories'].items():
            bar = execution_bar(inputs, code, day)
            if bar and bar['open'] > 0:
                marks[code] = money(bar['open'])
            elif history['raw']:
                marks[code] = money(history['raw'][-1]['close'])
        plan, state = make_plan(account, state, inputs, marks,
                                mode='daily_open_replay', created_at=day+'T09:30:00+08:00')
        plan['key'] = day+':replay'
        plan['execution_note'] = '历史日线开盘价近似；未模拟分钟参与率、盘中路径和部分成交。'
        when = datetime.fromisoformat(day+'T09:30:00+08:00')
        for side in ('sell', 'buy'):
            prepare_orders(plan, owner_key, side, account)
            for order in plan['orders']:
                if order['side'] != side:
                    continue
                code = order['code']
                bar = execution_bar(inputs, code, day)
                if not bar or bar['volume'] <= 0:
                    plan['skipped_targets'][code] = '当日没有可核验的成交日线，保留未成交订单'
                    continue
                previous = inputs['histories'][code]['raw'][-1]['close']
                limit = .2 if code.startswith(('300', '301', '688')) else .1
                price = money(bar['open'])
                if (side == 'buy' and price >= money(previous*(1+limit)) or
                        side == 'sell' and price <= money(previous*(1-limit))):
                    plan['skipped_targets'][code] = '开盘触及通常涨跌停边界，未假定排队成交'
                    continue
                try:
                    account, state, trade = fill_order(account, state, plan, order, price, when,
                        source='历史回放 / Tushare日线开盘价近似', fetched_at=now,
                        bar_id=day+':daily-open', partial=False,
                        factor=bar.get('factor'))
                except PaperError as error:
                    plan['skipped_targets'][code] = error.message
                    continue
                if trade:
                    trades.append(trade)
                else:
                    plan['skipped_targets'][code] = '现金、整手、T+1或行业额度不足，未顺延换股'
            plan[side+'_done'] = True
        finish_plan(plan, account)
        # Preserve only the prices actually used in today's plan, not a second
        # copy of every constituent's daily history in each owner decision.
        used = set(account['positions'])|set(plan['selected'])|{f['code'] for f in plan['fills']}
        plan['marks_fen'] = {c:v for c,v in plan['marks_fen'].items() if c in used}
        decisions.append(plan)
        account, state, events = settle_cash_actions(account, state, trades, inputs.get('dividends', {}),
            datetime.fromisoformat(day+'T15:00:00+08:00'))
        corporate.extend(events)
        closing = {code: close_mark(inputs, code, day) for code in account['positions']}
        market = sum(position['quantity']*closing[code]['price_fen'] for code,position in account['positions'].items())
        nav.append(dict(date=day, cash_fen=account['cash_fen'], market_value_fen=market,
                        receivable_fen=account.get('receivable_fen',0),
                        equity_fen=account['cash_fen']+market+account.get('receivable_fen',0), sequence=account['sequence'],
                        positions={c:dict(quantity=p['quantity'], cost_fen=p['cost_fen'],
                                           close_fen=closing[c]['price_fen'], price_as_of=closing[c]['as_of'])
                                   for c,p in account['positions'].items()}))
        source_proofs[day] = inputs['provenance']
        if output:
            progress = dict(status='running', through=day, completed_days=len(nav), total_days=len(dates),
                            trades=len(trades), equity_fen=nav[-1]['equity_fen'])
            Path(output).with_suffix('.progress.json').write_text(json.dumps(progress,ensure_ascii=False)+'\n')
        print(json.dumps(dict(day=day, eligible=len(inputs['rows']), positions=list(account['positions']),
                              trades=len(trades), equity_fen=nav[-1]['equity_fen']),ensure_ascii=False),flush=True)
        del inputs
        gc.collect()
    for code, position in account['positions'].items():
        position.update(last_price_fen=closing[code]['price_fen'], last_price_as_of=closing[code]['as_of'])
    code_root = Path(__file__).parent
    sources = {n:hashlib.sha256((code_root/n).read_bytes()).hexdigest()
               for n in ('n2_policy.py','n2_book.py','n2_inputs.py','n2_replay.py','n2_corporate.py')}
    payload = dict(schema=1, strategy_id=N2_STRATEGY_ID, model_version=N2_VERSION,
                   execution_mode='daily_open_replay', start_date=dates[0], end_date=dates[-1],
                   generated_at=now, account=account, state=state, trades=trades, decisions=decisions,
                   daily_nav=nav, corporate_events=corporate, closing_prices=closing, input_provenance=source_proofs,
                   code_sha256=sources,
                   note='按当时研报/公告可见日期与上一交易日行情选股；历史成交采用当日开盘价近似，区别于2011分钟回测。')
    payload['sha256'] = digest(payload)
    return payload
