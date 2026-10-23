"""Paper account orchestration; external quote work never holds a money lock."""
import asyncio
from datetime import datetime

from .news_cleaning import SHANGHAI
from .paper_rules import PaperError, apply_trade, board_rules, cached_quote, session_state, valid_quote, value_account


class PaperTradingService:
    def __init__(self, store, catalog, minute_market, clock=None):
        self.store, self.catalog, self.minute_market = store, catalog, minute_market
        self.clock = clock or (lambda: datetime.now(SHANGHAI))

    def _stock(self, code):
        stock = self.catalog.get(code)
        if not stock:
            raise PaperError('UNKNOWN_STOCK', '未找到已核验的股票，请先搜索该股票。', 404)
        board_rules(code)
        return stock

    def account(self, owner: str) -> dict:
        account = self.store.get_account(owner)
        quotes = {code: self.store.records.get('minute_market', code + ':1', default={}) for code in account['positions']}
        return value_account(account, quotes, self.clock())

    async def snapshot(self, owner: str, refresh: bool = False) -> dict:
        """A portfolio read schedules all held quotes, independent of trading decisions."""
        book = await asyncio.to_thread(self.store.get_account, owner)
        async def schedule(code, position):
            try:
                board_rules(code)
                # A persisted, previously validated holding can outlive a catalogue cache.
                stock = self.catalog.get(code) or {'code': code, 'name': position['name']}
                return code, await self.minute_market.snapshot(stock, 1, force=refresh)
            except (PaperError, ValueError, TypeError, KeyError, AttributeError):
                return code, {'refreshing': False, 'error': '持仓行情暂不可用，保留缓存估值。'}
        states = dict(await asyncio.gather(*(schedule(code, position) for code, position in book['positions'].items())))
        # Re-read the live book: a source response cannot restore a sold position.
        account = await asyncio.to_thread(self.account, owner)
        held = {position['code'] for position in account['positions']}
        refreshing = any(state.get('refreshing') for code, state in states.items() if code in held)
        errors = {code: state['error'] for code, state in states.items() if code in held and state.get('error')}
        return {**account, 'valuation_refreshing': refreshing, 'valuation_errors': errors,
                'next_poll_seconds': 2 if refreshing else 30}

    def trades(self, owner: str, limit: int = 20, before_seq: int | None = None) -> dict:
        return self.store.list_trades(owner, limit, before_seq)

    async def quote(self, code: str, refresh: bool = False) -> dict:
        stock = self._stock(code)
        try:
            snapshot = await self.minute_market.snapshot(stock, 1, force=refresh)
        except (ValueError, TypeError, KeyError, AttributeError):
            # Bad cached data cannot be promoted to an executable quote.
            snapshot = {'refreshing': False, 'next_poll_seconds': 30}
        raw = await asyncio.to_thread(self.store.records.get, 'minute_market', code + ':1', default={})
        now = self.clock()
        market = session_state(now)
        details = {'price_fen': None, 'as_of': None, 'fetched_at': None, 'source': 'AkShare / 新浪分钟行情'}
        try:
            details = cached_quote(raw, allow_error=True)
        except PaperError:
            pass
        reason_code, reason = None, None
        try:
            valid_quote(raw, now)
        except PaperError as exc:
            reason_code, reason = exc.code, exc.message
        fresh = reason_code is None
        if not market['can_trade']:
            reason_code, reason = market['reason_code'], market['reason']
        return {'stock': stock, 'rules': board_rules(code), **details, 'market': market,
                'can_trade': bool(market['can_trade'] and fresh), 'reason_code': reason_code, 'reason': reason,
                'quote_status': 'fresh' if fresh else 'stale' if details['price_fen'] is not None else 'unavailable',
                'refreshing': snapshot.get('refreshing', False), 'next_poll_seconds': snapshot.get('next_poll_seconds', 30)}

    async def submit(self, owner: str, request_id: str, code: str, side: str, quantity: int, execution=None) -> dict:
        def apply(account, cached):
            stock = self._stock(code)  # Only validate a new order, after successful UUID replay.
            now = self.clock()  # Recheck after the account lock, even across session boundaries.
            market = session_state(now)
            if not market['can_trade']:
                raise PaperError(market['reason_code'], market['reason'])
            return apply_trade(account, stock, side, quantity, valid_quote(cached, now), now)
        args = (owner, request_id, {'code': code, 'side': side, 'quantity': quantity}, apply)
        if execution is not None:
            return await asyncio.to_thread(self.store.transact, *args, execution=execution, now=self.clock)
        return await asyncio.to_thread(self.store.transact, *args)
