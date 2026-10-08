"""A-share paper fills and valuations; money is always integer RMB fen."""
import copy
import math
import re
from datetime import datetime, time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .news_cleaning import SHANGHAI
from .trading_calendar import market_state, previous_trade_day

INITIAL_CASH_FEN = 20_000_000
SOURCE = 'AkShare / 新浪分钟行情'


class PaperError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def new_account() -> dict:
    return {'version': 1, 'initial_cash_fen': INITIAL_CASH_FEN, 'cash_fen': INITIAL_CASH_FEN,
            'realized_pnl_fen': 0, 'total_fees_fen': 0, 'sequence': 0, 'positions': {}}


def board_rules(code: str) -> dict:
    if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
        raise PaperError('UNSUPPORTED_STOCK', '仅支持已核验的沪深 A 股。', 422)
    if code.startswith('688'):
        return {'board': '科创板', 'min_quantity': 200, 'quantity_step': 1, 'max_quantity': 50_000}
    if code.startswith(('300', '301')):
        return {'board': '创业板', 'min_quantity': 100, 'quantity_step': 100, 'max_quantity': 150_000}
    if code.startswith(('60', '000', '001', '002', '003')):
        return {'board': '主板', 'min_quantity': 100, 'quantity_step': 100, 'max_quantity': 1_000_000}
    raise PaperError('UNSUPPORTED_STOCK', '该证券不在本版模拟盘支持的沪深 A 股范围内。', 422)


def session_state(now: datetime) -> dict:
    now = now.astimezone(SHANGHAI)
    state = market_state(now)
    allowed = state['is_trading'] and now.time().replace(tzinfo=None) < time(14, 57)
    reason = None if allowed else 'CALENDAR_UNKNOWN' if state['is_trade_day'] is None else 'MARKET_CLOSED'
    return {**state, 'can_trade': allowed, 'reason_code': reason,
            'reason': None if allowed else '交易日历待更新，暂不能模拟成交。' if reason == 'CALENDAR_UNKNOWN'
            else '当前不在模拟盘连续交易时段（09:30–11:30、13:00–14:57）。'}


def sellable_quantity(position: dict, now: datetime) -> int:
    settled_day = previous_trade_day(now.astimezone(SHANGHAI).date(), include=True)
    if settled_day is None:
        return 0
    return sum(lot['quantity'] for lot in position.get('lots', []) if lot['date'] < settled_day.isoformat())


def _rounded(value: Decimal) -> int:
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def fees(gross_fen: int, side: str) -> dict:
    gross = Decimal(gross_fen)
    commission = max(500, _rounded(gross * Decimal('0.0003')))
    transfer = _rounded(gross * Decimal('0.00001'))
    stamp = _rounded(gross * Decimal('0.0005')) if side == 'sell' else 0
    return {'commission_fen': commission, 'transfer_fen': transfer, 'stamp_fen': stamp,
            'fees_fen': commission + transfer + stamp}


def _price(value) -> int:
    if isinstance(value, bool):
        raise ValueError('boolean price')
    decimal = Decimal(str(value))
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError('invalid price')
    result = _rounded(decimal * 100)
    if result <= 0:
        raise ValueError('price below one fen')
    return result


def cached_quote(cached: dict, allow_error=False) -> dict:
    try:
        if not isinstance(cached, dict):
            raise ValueError('invalid quote cache shape')
        if (cached.get('error') and not allow_error) or not cached.get('candles'):
            raise ValueError('missing or failed quote')
        bar = cached['candles'][-1]
        price = _price(bar['close'])
        # A malformed cached candle must not become an executable quote.
        values = [float(bar[key]) for key in ('open', 'close', 'high', 'low', 'volume')]
        if not all(math.isfinite(x) for x in values) or min(values[:4]) <= 0 or values[4] < 0:
            raise ValueError('invalid candle')
        if values[3] > min(values[0], values[1]) or values[2] < max(values[0], values[1]) or values[3] > values[2]:
            raise ValueError('invalid OHLC')
        stamp = datetime.fromisoformat(bar['date'])
        stamp = stamp.replace(tzinfo=SHANGHAI) if stamp.tzinfo is None else stamp.astimezone(SHANGHAI)
        clock = stamp.time().replace(tzinfo=None)
        if not (time(9, 30) <= clock <= time(11, 30) or time(13) <= clock <= time(15)):
            raise ValueError('bar outside exchange session')
        fetched = datetime.fromisoformat(cached['fetched_at'])
        fetched = fetched.replace(tzinfo=SHANGHAI) if fetched.tzinfo is None else fetched.astimezone(SHANGHAI)
        return {'price_fen': price, 'as_of': stamp.isoformat(), 'fetched_at': fetched.isoformat(), 'source': SOURCE}
    except (KeyError, IndexError, TypeError, ValueError, InvalidOperation, OverflowError):
        raise PaperError('QUOTE_UNAVAILABLE', '没有有效的分钟报价，请刷新后再试。') from None


def valid_quote(cached: dict, now: datetime) -> dict:
    quote = cached_quote(cached)
    now = now.astimezone(SHANGHAI)
    stamp, fetched = datetime.fromisoformat(quote['as_of']), datetime.fromisoformat(quote['fetched_at'])
    if stamp.date() != now.date() or not -60 <= (now - stamp).total_seconds() <= 180 or not 0 <= (now - fetched).total_seconds() <= 90:
        raise PaperError('QUOTE_STALE', '分钟行情已延迟，等待有效报价后再成交。')
    return quote


def _quantity(quantity, rules: dict, side: str, available: int) -> None:
    if type(quantity) is not int or quantity <= 0 or quantity > rules['max_quantity']:
        raise PaperError('INVALID_QUANTITY', '请输入规则允许的正整数股数。', 422)
    if side == 'buy':
        valid = quantity >= rules['min_quantity'] and quantity % rules['quantity_step'] == 0
    elif rules['quantity_step'] == 1:
        valid = quantity >= 200 or (available < 200 and quantity == available)
    else:
        valid = quantity % 100 == 0 or (available % 100 > 0 and quantity % 100 == available % 100)
    if not valid:
        raise PaperError('INVALID_QUANTITY', f"{rules['board']}股数不符合申报规则，请检查最小股数和零股余额。", 422)


def apply_trade(account: dict, stock: dict, side: str, quantity: int, quote: dict, now: datetime) -> tuple[dict, dict]:
    now = now.astimezone(SHANGHAI)
    state = session_state(now)
    if not state['can_trade']:
        raise PaperError(state['reason_code'], state['reason'])
    if side not in ('buy', 'sell'):
        raise PaperError('INVALID_SIDE', '交易方向必须为买入或卖出。', 422)
    code = stock['code']
    rules = board_rules(code)
    position = account['positions'].get(code)
    available = sellable_quantity(position, now) if position else 0
    _quantity(quantity, rules, side, available)
    if side == 'sell':
        if not position or quantity > position['quantity']:
            raise PaperError('INSUFFICIENT_POSITION', '持仓数量不足，不能卖空。')
        if quantity > available:
            raise PaperError('T_PLUS_ONE', '当天买入的股份下一交易日才能卖出，请检查可卖数量。')
    price = quote['price_fen']
    if type(price) is not int or price <= 0:
        raise PaperError('QUOTE_UNAVAILABLE', '成交报价无效。')
    gross = quantity * price
    costs = fees(gross, side)
    net = gross + costs['fees_fen'] if side == 'buy' else gross - costs['fees_fen']
    cash = account['cash_fen'] - net if side == 'buy' else account['cash_fen'] + net
    if cash < 0:
        raise PaperError('INSUFFICIENT_CASH', '可用现金不足，股款和交易费用需一并计入。')
    updated = copy.deepcopy(account)
    realized = 0
    if side == 'buy':
        target = updated['positions'].setdefault(code, {'code': code, 'name': stock['name'],
            'quantity': 0, 'cost_fen': 0, 'lots': []})
        target['quantity'] += quantity
        target['cost_fen'] += net
        today = now.date().isoformat()
        lot = next((lot for lot in target['lots'] if lot['date'] == today), None)
        if lot:
            lot['quantity'] += quantity
        else:
            target['lots'].append({'date': today, 'quantity': quantity})
    else:
        target = updated['positions'][code]
        allocated = target['cost_fen'] if quantity == target['quantity'] else _rounded(Decimal(target['cost_fen']) * quantity / target['quantity'])
        realized = net - allocated
        remaining = quantity
        cutoff = previous_trade_day(now.date(), include=True).isoformat()
        for lot in sorted(target['lots'], key=lambda x: x['date']):
            if lot['date'] >= cutoff or not remaining:
                continue
            taken = min(remaining, lot['quantity'])
            lot['quantity'] -= taken
            remaining -= taken
        target['lots'] = [lot for lot in target['lots'] if lot['quantity']]
        target['quantity'] -= quantity
        target['cost_fen'] -= allocated
        if not target['quantity']:
            del updated['positions'][code]
    if code in updated['positions']:
        updated['positions'][code].update(last_price_fen=price, last_price_as_of=quote['as_of'])
    updated['cash_fen'] = cash
    updated['realized_pnl_fen'] += realized
    updated['total_fees_fen'] += costs['fees_fen']
    trade = {'code': code, 'name': stock['name'], 'side': side, 'quantity': quantity,
             'price_fen': price, 'gross_fen': gross, **costs, 'cash_after_fen': cash,
             'realized_pnl_fen': realized, 'executed_at': now.isoformat(),
             'quote_as_of': quote['as_of'], 'quote_fetched_at': quote['fetched_at'], 'source': quote['source']}
    return updated, trade


def value_account(account: dict, quotes: dict[str, dict], now: datetime) -> dict:
    positions = []
    for code, position in account['positions'].items():
        try:
            quote = cached_quote(quotes.get(code, {}), allow_error=True)
            status = 'fresh'
            try:
                valid_quote(quotes[code], now)
            except PaperError:
                status = 'stale'
        except PaperError:
            quote = {'price_fen': position['last_price_fen'], 'as_of': position['last_price_as_of']}
            status = 'estimated'
        quantity = position['quantity']
        market_value = quote['price_fen'] * quantity
        sellable = sellable_quantity(position, now)
        positions.append({'code': code, 'name': position['name'], 'quantity': quantity,
            'sellable': sellable, 'locked': quantity - sellable, 'cost_fen': position['cost_fen'],
            'average_cost_fen': _rounded(Decimal(position['cost_fen']) / quantity),
            'price_fen': quote['price_fen'], 'price_as_of': quote['as_of'], 'valuation_status': status,
            'market_value_fen': market_value, 'unrealized_pnl_fen': market_value - position['cost_fen']})
    market_value = sum(item['market_value_fen'] for item in positions)
    unrealized = sum(item['unrealized_pnl_fen'] for item in positions)
    equity = account['cash_fen'] + market_value
    pnl = equity - account['initial_cash_fen']
    return {'initial_cash_fen': account['initial_cash_fen'], 'cash_fen': account['cash_fen'],
            'market_value_fen': market_value, 'equity_fen': equity,
            'realized_pnl_fen': account['realized_pnl_fen'], 'unrealized_pnl_fen': unrealized,
            'total_pnl_fen': pnl, 'return_percent': round(pnl / account['initial_cash_fen'] * 100, 4),
            'total_fees_fen': account['total_fees_fen'], 'positions': positions, 'market': session_state(now)}
