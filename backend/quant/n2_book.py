"""One N2 decision kernel for historical-open replay and live minute execution.

The paper ledger remains integer fen. Only the execution adapter supplies prices;
ranking, exits, frozen targets and fill-state transitions are shared.
"""
import copy
import hashlib
import json
import math
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from ..news_cleaning import SHANGHAI
from ..paper_rules import apply_trade, board_rules, fees, sellable_quantity
from .n2_policy import (N2_STRATEGY_ID, N2_VERSION, N2_BAND, N2_COUNT,
                        allocation_weights, initial_state, price_exit_codes, rank_candidates,
                        select_portfolio)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def money(value):
    return int((Decimal(str(value))*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def model_fees(gross_fen, side):
    if type(gross_fen) is not int or gross_fen < 0 or side not in ('buy', 'sell'):
        raise ValueError('模型成交金额或方向无效')
    if not gross_fen:
        return dict(commission_fen=0, transfer_fen=0, stamp_fen=0, friction_fen=0, fees_fen=0)
    result = fees(gross_fen, side)
    floor = int((Decimal(gross_fen)*Decimal('.002')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    result['friction_fen'] = max(0, floor-result['fees_fen'])
    result['fees_fen'] += result['friction_fen']
    return result


def legal_quantity(code, quantity, *, available=None):
    rules = board_rules(code)
    quantity = min(max(0, int(quantity)), rules['max_quantity'])
    if available is not None:
        quantity = min(quantity, available)
        if quantity == available:
            return quantity
    quantity = quantity//rules['quantity_step']*rules['quantity_step']
    return quantity if quantity >= rules['min_quantity'] else 0


def seed_state(trades, account):
    state = initial_state()
    quantities = {}
    for trade in sorted(trades, key=lambda t:t['sequence']):
        code = trade['code']
        previous = quantities.get(code, 0)
        if trade['side'] == 'buy':
            if not previous:
                state['entries'][code] = dict(date=trade['executed_at'][:10], price_fen=trade['price_fen'])
            quantities[code] = previous+trade['quantity']
        else:
            quantities[code] = previous-trade['quantity']
            if quantities[code] < 0:
                raise ValueError('成交记录出现负持股')
            if not quantities[code]:
                state['entries'].pop(code, None)
    if {c:q for c,q in quantities.items() if q} != {c:p['quantity'] for c,p in account['positions'].items()}:
        raise ValueError('成交记录与持仓不一致，无法恢复首次建仓锚点')
    state['sequence'] = account['sequence']
    return state


def update_manual_state(state, before, after, trade):
    """Keep an existing N2 book coherent with other committed ledger fills."""
    code = trade['code']
    if trade['side'] == 'buy' and code not in before['positions']:
        state['entries'][code] = dict(date=trade['executed_at'][:10], price_fen=trade['price_fen'])
    if code not in after['positions']:
        if state['pending'].get(code, '').startswith('price_'):
            state['cooldowns'][code] = trade['executed_at'][:10]
        for mapping in ('entries', 'pending', 'streaks'):
            state[mapping].pop(code, None)
    state['sequence'] = after['sequence']


def refresh_entry_factors(state, histories, signal):
    """Resolve the entry day's factor once available, preserving its fill price."""
    result = copy.deepcopy(state)
    for code, entry in result['entries'].items():
        if entry['date'] > signal or ('adjusted_anchor_fen' in entry and not entry.get('factor_pending')):
            continue
        history = histories.get(code, {})
        raw = {b['date']:b for b in history.get('raw', []) if b['date'] <= signal}
        adj = {b['date']:b for b in history.get('adjusted', []) if b['date'] <= signal}
        day = entry['date']
        if day not in raw or day not in adj or raw[day]['close'] <= 0:
            raise ValueError(code+' 缺少原始建仓日复权参照')
        factor = float(adj[day]['close'])/float(raw[day]['close'])
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError('原始建仓日复权因子无效')
        entry.update(factor_at_entry=factor, adjusted_anchor_fen=entry['price_fen']*factor)
        entry.pop('factor_pending', None)
    return result


def make_plan(account, state, inputs, marks, *, mode, created_at):
    day, signal = inputs['date'], inputs['signal_date']
    if day <= signal or inputs['calendar'][-1] != signal:
        raise ValueError('双股策略信号日与交易日未隔离')
    ranked = rank_candidates(inputs['rows'], inputs['histories'], inputs['calendar'], signal)
    state = copy.deepcopy(state)
    if state['sequence'] != account['sequence']:
        raise ValueError('双股策略状态与账本序号不一致')
    held = account['positions']
    state['pending'].update(price_exit_codes(state, inputs['histories'], signal))
    calendar = [*inputs['calendar'], day]
    sectors = {r['code']: r.get('industry_l1') for r in ranked}
    sectors.update(inputs.get('sectors', {}))
    selection, state = select_portfolio(ranked, held, state, day, calendar, sectors)
    needed = set(held)|set(selection['selected'])
    if any(type(marks.get(c)) is not int or marks[c] <= 0 for c in needed):
        raise ValueError('双股策略10:00估值价格不完整')
    equity = account['cash_fen']+account.get('receivable_fen',0)+sum(p['quantity']*marks[c] for c,p in held.items())
    capital = max(0, equity-math.ceil(equity*.004)-3000)
    weights, allocation = allocation_weights(selection['selected'], inputs['histories'],
                                              inputs['calendar'], signal, selection['ranks'])
    targets = {c: int(capital*w) for c,w in weights.items()}
    band_keep = []
    sells = []
    for code,p in sorted(held.items()):
        value = p['quantity']*marks[code]
        sector_value = sum(q['quantity']*marks[c] for c,q in held.items()
                           if sectors.get(c) == sectors.get(code))
        if code in selection['exit_reasons']:
            quantity, reason = p['quantity'], selection['exit_reasons'][code]
        elif abs(value-targets.get(code, 0)) <= N2_BAND*equity and sector_value <= .6*equity:
            band_keep.append(code)
            continue
        else:
            quantity, reason = max(0, (value-targets.get(code, 0))//marks[code]), 'equalize'
        quantity = legal_quantity(code, quantity, available=sellable_quantity(p, datetime.fromisoformat(day).replace(tzinfo=SHANGHAI)))
        if quantity:
            sells.append(dict(code=code, side='sell', quantity=quantity, reason=reason))
    plan = dict(strategy_id=N2_STRATEGY_ID, model_version=N2_VERSION, date=day, as_of=signal,
                status='running', created_at=created_at, execution_mode=mode,
                input_sha256=digest(inputs['provenance']), ranked=ranked,
                active_candidates=selection['active_candidates'], ranks=selection['ranks'],
                selected=selection['selected'], sectors=sectors, exit_reasons=selection['exit_reasons'],
                marks_fen=marks, decision_equity_fen=equity, capital_fen=capital,
                target_weights=weights, target_values_fen=targets, allocation=allocation,
                band_keep=band_keep, orders=sells, results=[], fills=[], sell_done=False,
                buy_planned=False, buy_done=False, skipped_targets={},
                entry_factors={c: factor_on(inputs['histories'][c], signal) for c in needed})
    return plan, state


def factor_on(history, day):
    raw = {b['date']: b for b in history['raw'] if b['date'] <= day}
    adj = {b['date']: b for b in history['adjusted'] if b['date'] <= day}
    if not raw:
        raise ValueError('缺少建仓复权因子')
    date = max(raw)
    if date not in adj or raw[date]['close'] <= 0:
        raise ValueError('建仓复权参照不完整')
    return float(adj[date]['close'])/float(raw[date]['close'])


def buy_orders(account, plan):
    """Actual unfilled exits continue to occupy both a stock and an industry slot."""
    held = account['positions']
    occupied = set(held)
    sectors = plan['sectors']
    sold = {f['code'] for f in plan['fills'] if f['side'] == 'sell' and f['code'] not in held}
    orders = []
    candidates = sorted(plan['selected'], key=lambda c:(c in held, plan['ranks'].get(c, math.inf), c))
    for code in candidates:
        if code in plan['exit_reasons'] or code in plan['band_keep'] or code in sold:
            continue
        if any(o['code'] == code and o['side'] == 'sell' for o in plan['orders']):
            continue
        entry = code not in held
        if entry and (len(occupied) >= N2_COUNT or any(sectors.get(c) == sectors.get(code) for c in occupied)):
            plan['skipped_targets'][code] = '未成交退出持仓继续占用股票或行业名额'
            continue
        current = held.get(code, {}).get('quantity', 0)*plan['marks_fen'][code]
        quantity = legal_quantity(code, max(0, plan['target_values_fen'].get(code, 0)-current)//plan['marks_fen'][code])
        if quantity:
            orders.append(dict(code=code, side='buy', quantity=quantity, reason='entry' if entry else 'equalize'))
            occupied.add(code)
        elif entry:
            plan['skipped_targets'][code] = '目标金额不足一手，本轮不顺延改选'
    return orders


def prepare_orders(plan, owner_key, side, account):
    if side == 'buy' and not plan['buy_planned']:
        plan['orders'].extend(buy_orders(account, plan))
        plan['buy_planned'] = True
    for order in plan['orders']:
        if order['side'] != side or 'id' in order:
            continue
        order.update(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"finpulse:n2:{owner_key}:{plan['date']}:{side}:{order['code']}")),
                     filled=0, gross_fen=0, charges={k:0 for k in model_fees(0, side)})
    return plan


def fill_order(account, state, plan, order, price_fen, when, *, capacity=None,
               source, fetched_at, bar_id, partial=False, factor=None):
    """A deterministic fill on a validated real bar; no shared quote cache writes."""
    remaining = order['quantity']-order['filled']
    count = min(remaining, remaining if capacity is None else max(0, int(capacity)))
    code, side = order['code'], order['side']
    if side == 'sell':
        count = min(count, sellable_quantity(account['positions'].get(code, {}), when))
    if count <= 0:
        return account, state, None
    marks = plan['marks_fen']
    def charge(q):
        cumulative = model_fees(order['gross_fen']+q*price_fen, side)
        return {k:cumulative[k]-order['charges'][k] for k in cumulative}
    def affordable(q):
        costs = charge(q)['fees_fen']
        if q*price_fen+costs > account['cash_fen']:
            return False
        value = sum(p['quantity']*marks[c] for c,p in account['positions'].items())
        equity = account['cash_fen']+account.get('receivable_fen',0)+value
        after_equity = equity+q*(marks[code]-price_fen)-costs
        sector_value = sum(p['quantity']*marks[c] for c,p in account['positions'].items()
                           if plan['sectors'].get(c) == plan['sectors'].get(code))
        return (value+q*marks[code] <= after_equity and
                sector_value+q*marks[code] <= .6*after_equity)
    if side == 'buy':
        low, high = 0, count
        while low < high:
            middle = (low+high+1)//2
            if affordable(middle): low = middle
            else: high = middle-1
        count = low
    if not partial:
        count = legal_quantity(code, count, available=sellable_quantity(account['positions'].get(code, {}), when) if side == 'sell' else None)
    if not count:
        return account, state, None
    costs = charge(count)
    stock = dict(code=code, name=next((r.get('name') or code for r in plan['ranked'] if r['code'] == code),
                                     account['positions'].get(code, {}).get('name', code)))
    quote = dict(price_fen=price_fen, as_of=when.isoformat(), fetched_at=fetched_at, source=source)
    updated, trade = apply_trade(account, stock, side, count, quote, when,
                                 execution_costs=costs, partial_fill=partial)
    sequence = account['sequence']+1
    updated['sequence'] = sequence
    request = str(uuid.uuid5(uuid.UUID(order['id']), bar_id))
    trade.update(request_id=request, sequence=sequence,
                 intent=dict(code=code, side=side, quantity=count), actor='algorithm',
                 strategy_id=N2_STRATEGY_ID, model_version=N2_VERSION,
                 execution_mode=plan['execution_mode'], parent_order_id=order['id'],
                 decision_key=plan.get('key'), reason=order['reason'])
    state = copy.deepcopy(state)
    if side == 'buy':
        entry = state['entries'].get(code)
        if entry is None or entry.get('entry_order_id') == order['id']:
            entry = entry or dict(date=when.date().isoformat(), entry_order_id=order['id'],
                                  initial_gross_fen=0, initial_quantity=0,
                                  factor_at_entry=factor if factor is not None else plan['entry_factors'][code],
                                  factor_pending=factor is None)
            entry['initial_gross_fen'] += trade['gross_fen']
            entry['initial_quantity'] += count
            entry['price_fen'] = entry['initial_gross_fen']/entry['initial_quantity']
            entry['adjusted_anchor_fen'] = entry['price_fen']*entry['factor_at_entry']
            state['entries'][code] = entry
    elif code not in updated['positions']:
        if state['pending'].get(code, '').startswith('price_'):
            state['cooldowns'][code] = when.date().isoformat()
        for mapping in ('entries', 'pending', 'streaks'):
            state[mapping].pop(code, None)
    state['sequence'] = sequence
    order['filled'] += count
    order['gross_fen'] += trade['gross_fen']
    order['charges'] = model_fees(order['gross_fen'], side)
    plan['fills'].append(trade)
    plan['results'].append(dict(request_id=request, code=code, side=side, status='filled', sequence=sequence))
    return updated, state, trade


def finish_plan(plan, account):
    plan['status'] = 'completed'
    plan['spent_fen'] = sum(t['gross_fen'] for t in plan['fills'])
    plan['reason'] = '四因子排序；固定2股倒波动、前9保留、3次离榜确认、18%退出与20交易日冷却、5个百分点免微调。'
    plan['allocation_outcome'] = dict(note='按实际成交保留资金与持仓；未成交退出继续占用名额。',
                                    positions={c:dict(quantity=p['quantity']) for c,p in account['positions'].items()})
    return plan
