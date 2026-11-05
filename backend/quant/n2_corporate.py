"""Cash dividend entitlements, using the frozen research 20% withholding rule."""
import copy
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from ..news_cleaning import SHANGHAI
from .n2_book import digest


def _day(value):
    value = str(value or '').replace('-', '')[:8]
    return datetime.strptime(value, '%Y%m%d').date().isoformat()


def entitlement(trades, code, record_date):
    return sum(t['quantity']*(1 if t['side'] == 'buy' else -1) for t in trades
               if t['code'] == code and t['executed_at'][:10] <= record_date)


def cash_amounts(quantity, per_share):
    amount = Decimal(str(per_share))
    if not amount.is_finite() or amount < 0:
        raise ValueError('现金分红金额无效')
    gross = int((amount*quantity*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    net = int((amount*quantity*100*Decimal('.8')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return gross, net


def apply_cash_event(account, event):
    result = copy.deepcopy(account)
    if event['corporate_revision'] != result.get('corporate_revision', 0)+1:
        raise ValueError('公司现金权益版本不连续')
    result['cash_fen'] += event['cash_delta_fen']
    result['receivable_fen'] = result.get('receivable_fen', 0)+event['receivable_delta_fen']
    result['realized_pnl_fen'] += event['income_fen']
    result['corporate_revision'] = event['corporate_revision']
    if min(result['cash_fen'], result['receivable_fen']) < 0:
        raise ValueError('分红资金或应收余额无效')
    return result


def settle_cash_actions(account, state, trades, dividends, now):
    """Register record-close holdings, activate ex-date claims, pay after close.

    Cash is unavailable for the payment day's morning orders, matching the
    original minute engine. Sold positions retain their already earned claims.
    """
    now = now.astimezone(SHANGHAI)
    day = now.date().isoformat()
    result, updated = copy.deepcopy(account), copy.deepcopy(state)
    claims = updated.setdefault('cash_claims', {})
    for code, rows in dividends.items():
        for row in rows:
            if row.get('div_proc') != '实施' or not row.get('record_date'):
                continue
            record = _day(row['record_date'])
            if record > day or record == day and now.hour < 15:
                continue
            if not row.get('ann_date') or _day(row['ann_date']) > day:
                continue
            quantity = entitlement(trades, code, record)
            if quantity <= 0:
                continue
            # Non-cash corporate changes need their own lot/cost transitions.
            if float(row.get('stk_div') or 0) != 0:
                raise ValueError(code+' 送转权益需核验，暂停新增交易')
            amount = row.get('cash_div_tax')
            if amount is None:
                raise ValueError(code+' 现金分红金额缺失')
            if Decimal(str(amount)) == 0:
                continue
            ex, pay = _day(row.get('ex_date')), _day(row.get('pay_date'))
            if not record < ex <= pay:
                raise ValueError(code+' 分红登记、除息或支付日期无效')
            ident = digest(dict(code=code, record_date=record, ex_date=ex, period=row.get('end_date')))
            gross, net = cash_amounts(quantity, amount)
            claim = dict(claim_id=ident, code=code, record_date=record, ex_date=ex, pay_date=pay,
                         quantity_entitled=quantity, cash_per_share=str(amount),
                         gross_fen=gross, net_fen=net, tax_fen=gross-net, withholding_rate=.2,
                         ann_date=_day(row['ann_date']), active=False, paid=False)
            if ident in claims:
                old = claims[ident]
                if any(old[k] != claim[k] for k in claim if k not in ('active', 'paid')):
                    raise ValueError(code+' 已登记的分红权益发生修订，需人工核验')
            else:
                claims[ident] = claim
    pending = []
    latest_trade = max((datetime.fromisoformat(t['executed_at']).astimezone(SHANGHAI)
                        for t in trades), default=None)
    for claim in claims.values():
        for kind, flag, clock in (('dividend_ex', 'active', '09:00:00'), ('cash_dividend', 'paid', '15:00:00')):
            scheduled = claim['ex_date' if kind == 'dividend_ex' else 'pay_date']+'T'+clock+'+08:00'
            due = datetime.fromisoformat(scheduled)
            if not claim[flag] and due <= now:
                # A late posting must follow the fills already reflected in this
                # book; otherwise their recorded cash balances become untrue.
                effective = max(due, latest_trade+timedelta(microseconds=1)) if latest_trade else due
                pending.append((effective.isoformat(), 0 if kind == 'dividend_ex' else 1,
                                kind, flag, claim, scheduled))
    events = []
    for effective, _, kind, flag, claim, scheduled in sorted(pending, key=lambda x:x[:2]):
        net = claim['net_fen']; paid = kind == 'cash_dividend'
        event = {k:v for k,v in claim.items() if k not in ('active', 'paid')}
        event.update(event_id=claim['claim_id']+':'+kind, type=kind, effective_at=effective,
                     cash_delta_fen=net if paid else 0, receivable_delta_fen=-net if paid else net,
                     income_fen=net if paid else 0, corporate_revision=result.get('corporate_revision',0)+1,
                     book_sequence=result['sequence'])
        if effective != scheduled:
            event['scheduled_at'] = scheduled
        result = apply_cash_event(result, event)
        event['cash_after_fen'] = result['cash_fen']
        events.append(event)
        claim[flag] = True
    return result, updated, events
