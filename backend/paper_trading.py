"""Paper account orchestration; external quote work never holds a money lock."""
import asyncio
from datetime import datetime

from .news_cleaning import SHANGHAI
from .paper_rules import PaperError, _price, apply_trade, board_rules, cached_quote, daily_performance, session_state, valid_quote, value_account
from .trading_calendar import previous_trade_day


class PaperTradingService:
    def __init__(self, store, catalog, minute_market, clock=None, daily_market=None):
        self.store, self.catalog, self.minute_market = store, catalog, minute_market
        self.clock = clock or (lambda: datetime.now(SHANGHAI))
        self.daily_market = daily_market

    def _stock(self, code):
        stock = self.catalog.get(code)
        if not stock:
            raise PaperError('UNKNOWN_STOCK', '未找到已核验的股票，请先搜索该股票。', 404)
        board_rules(code)
        return stock

    def _previous_closes(self, codes, day):
        records=self.store.records;prices={}
        history=records.get_many('quant_history',[code+':'+day for code in codes])
        daily=records.get_many('daily_market',codes)
        minutes=records.get_many('minute_market',[code+':1' for code in codes])
        for code in codes:
            sources=[(history.get(code+':'+day),'raw',False),(daily.get(code),'candles',False),(minutes.get(code+':1'),'candles',True)]
            for source,key,minute in sources:
                if not isinstance(source,dict):continue
                if key=='candles' and not minute:
                    quote=source.get('quote',{})
                    if not isinstance(quote,dict) or quote.get('adjustment','none')!='none':continue
                bars=source.get(key,[])
                if not isinstance(bars,list):continue
                for bar in reversed(bars):
                    try:
                        stamp=str(bar['date'])
                        if minute:
                            parsed=datetime.fromisoformat(stamp)
                            parsed=parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)
                            if parsed.date().isoformat()!=day or parsed.strftime('%H:%M:%S')!='15:00:00':continue
                        elif stamp!=day:continue
                        prices[code]=_price(bar['close']);break
                    except (ValueError,TypeError,KeyError,ArithmeticError):continue
                if code in prices:break
        return prices

    def account(self, owner: str, daily: bool = False) -> dict:
        account = self.store.get_account(owner)
        quotes = {code: self.store.records.get('minute_market', code + ':1', default={}) for code in account['positions']}
        now=self.clock();valued=value_account(account, quotes, now)
        if not daily:return valued
        day=previous_trade_day(now.astimezone(SHANGHAI).date(),include=True)
        reference=previous_trade_day(day) if day else None
        dates={'daily_pnl_date':day.isoformat() if day else None,'daily_reference_date':reference.isoformat() if reference else None}
        if reference is None:
            return {**valued,**dates,'daily_pnl_fen':None,'daily_return_percent':None,'daily_pnl_base_equity_fen':None,
                    'daily_pnl_status':'unavailable','daily_pnl_missing_codes':[],'daily_pnl_reason':'交易日历待更新'}
        trades=self.store.day_trades(owner,day.isoformat(),account['sequence'])
        codes=set(account['positions'])|{trade['code'] for trade in trades}
        closes=self._previous_closes(codes,reference.isoformat())
        return {**valued,**dates,**daily_performance(account,trades,closes,valued['equity_fen'])}

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
        account = await asyncio.to_thread(self.account, owner, daily=True)
        held = {position['code'] for position in account['positions']}
        refreshing = any(state.get('refreshing') for code, state in states.items() if code in held)
        errors = {code: state['error'] for code, state in states.items() if code in held and state.get('error')}
        if self.daily_market:
            for code in account['daily_pnl_missing_codes']:
                stock=self.catalog.get(code) or {'code':code,'name':code}
                try:state=await self.daily_market.snapshot(stock,force=True)
                except (ValueError,TypeError,KeyError,AttributeError):continue
                if state['refreshing']:
                    refreshing=True;account['daily_pnl_status']='pending';account['daily_pnl_reason']='正在补齐上一交易日收盘价'
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
