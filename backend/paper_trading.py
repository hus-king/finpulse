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

    def trades(self, owner: str, limit: int = 20, before_seq: int | None = None) -> dict:
        return self.store.list_trades(owner, limit, before_seq)

    async def quote(self, code: str, refresh: bool = False) -> dict:
        stock = self._stock(code)
        try:
            snapshot = await self.minute_market.snapshot(stock, 1, force=refresh)
        except (ValueError, TypeError, KeyError):
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

    async def submit(self, owner: str, request_id: str, code: str, side: str, quantity: int) -> dict:
        stock = self._stock(code)
        def apply(account, cached):
            now = self.clock()  # Recheck after the account lock, even across session boundaries.
            market = session_state(now)
            if not market['can_trade']:
                raise PaperError(market['reason_code'], market['reason'])
            return apply_trade(account, stock, side, quantity, valid_quote(cached, now), now)
        return await asyncio.to_thread(self.store.transact, owner, request_id,
            {'code': code, 'side': side, 'quantity': quantity}, apply)
