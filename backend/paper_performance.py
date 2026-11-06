"""Read-only, owner-scoped paper returns on a common completed-session basis."""
import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math

from . import providers
from .instruments import INDICES
from .news_cleaning import SHANGHAI
from .paper_rules import new_account
from .trading_calendar import is_trade_day, previous_trade_day


class PerformanceDataError(ValueError):
    pass


def _day(value):
    if not isinstance(value,str):
        raise PerformanceDataError('历史日期格式待核验')
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        raise PerformanceDataError('历史日期格式待核验') from None


def _stamp(value):
    try:
        stamp=datetime.fromisoformat(value)
        stamp=stamp.replace(tzinfo=SHANGHAI) if stamp.tzinfo is None else stamp.astimezone(SHANGHAI)
        return stamp
    except (ValueError,TypeError):
        raise PerformanceDataError('账本日期格式待核验') from None


def _trade_day(trade):
    return _stamp(trade.get('executed_at')).date().isoformat()


def _fen(value):
    try:
        value=Decimal(str(value))
        if not value.is_finite() or value<=0:
            raise ValueError
        return int((value*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    except (ValueError,ArithmeticError):
        raise PerformanceDataError('历史收盘价格待补齐') from None


def _integer(value, *, minimum=0):
    if type(value) is not int or value<minimum:
        raise PerformanceDataError('历史账本金额或数量待核验')
    return value


def _signature(snapshot, end):
    encoded=json.dumps({'snapshot':snapshot,'end':end},sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _corporate(snapshot):
    account=snapshot['account'];revision=_integer(account.get('corporate_revision',0))
    sequence=_integer(account.get('sequence'))
    events=sorted((e for e in snapshot.get('corporate',[]) if _integer(e.get('corporate_revision'),minimum=1)<=revision),
                  key=lambda e:e['corporate_revision'])
    if [e['corporate_revision'] for e in events]!=list(range(1,revision+1)):
        raise PerformanceDataError('现金分红记录尚未完整，暂不能计算历史收益')
    if len({e.get('event_id') for e in events})!=len(events):
        raise PerformanceDataError('现金分红记录编号重复')
    for event in events:
        if not event.get('event_id') or _integer(event.get('book_sequence'))>sequence:
            raise PerformanceDataError('现金分红记录与账户顺序不一致')
        _stamp(event.get('effective_at'))
        cash=_integer(event.get('cash_delta_fen'));income=_integer(event.get('income_fen'))
        receivable=event.get('receivable_delta_fen')
        if type(receivable) is not int:
            raise PerformanceDataError('应收现金分红金额待核验')
        if event.get('type')=='dividend_ex':
            valid=cash==0 and receivable>=0 and income==0
        elif event.get('type')=='cash_dividend':
            valid=receivable==-cash and income==cash
        else:valid=False
        if not valid:
            raise PerformanceDataError('现金分红及应收转款记录待核验')
    return events


def _activity(trades,corporate):
    rows=[(_stamp(t.get('executed_at')),1,t['sequence'],'trade',t) for t in trades]
    rows += [(_stamp(e.get('effective_at')),0 if e['type']=='dividend_ex' else 2,e['corporate_revision'],'corporate',e)
             for e in corporate]
    return sorted(rows,key=lambda row:row[:3])


def _journal(snapshot):
    """Validate the complete authoritative sequence, including cleared names."""
    account=snapshot['account'];initial=_integer(account.get('initial_cash_fen'),minimum=1)
    sequence=_integer(account.get('sequence'))
    trades=sorted((t for t in snapshot['trades'] if _integer(t.get('sequence'),minimum=1)<=sequence),key=lambda t:t['sequence'])
    if [t['sequence'] for t in trades] != list(range(1,sequence+1)):
        raise PerformanceDataError('成交账本尚未完整，暂不能计算历史收益')
    cash=initial;receivable=0;held=defaultdict(int);last_day=None
    for trade in trades:
        day=_trade_day(trade)
        if last_day and day<last_day:
            raise PerformanceDataError('成交时间与账本顺序不一致')
        last_day=day
    for _,_,_,kind,trade in _activity(trades,_corporate(snapshot)):
        if kind=='corporate':
            cash+=trade['cash_delta_fen'];receivable+=trade['receivable_delta_fen']
            if (cash<0 or receivable<0 or trade.get('cash_after_fen') is not None and trade['cash_after_fen']!=cash):
                raise PerformanceDataError('现金分红资金或应收余额待核验')
            continue
        quantity=_integer(trade.get('quantity'),minimum=1)
        gross=_integer(trade.get('gross_fen'),minimum=1)
        costs=_integer(trade.get('fees_fen'))
        price=_integer(trade.get('price_fen'),minimum=1)
        if gross!=quantity*price:
            raise PerformanceDataError('历史成交金额待核验')
        code=trade.get('code')
        if not isinstance(code,str) or len(code)!=6 or not code.isdigit():
            raise PerformanceDataError('历史成交股票代码待核验')
        if trade.get('side')=='buy':
            held[code]+=quantity;cash-=gross+costs
        elif trade.get('side')=='sell':
            held[code]-=quantity;cash+=gross-costs
        else:
            raise PerformanceDataError('历史成交方向待核验')
        if cash<0 or held[code]<0 or trade.get('cash_after_fen')!=cash:
            raise PerformanceDataError('历史成交资金或持仓待核验')
    actual={c:p['quantity'] for c,p in account.get('positions',{}).items()}
    if (cash!=account.get('cash_fen') or receivable!=_integer(account.get('receivable_fen',0))
            or {c:q for c,q in held.items() if q}!=actual):
        raise PerformanceDataError('成交记录与当前账户不一致')
    return initial,trades


def _sessions(start,end):
    current=date.fromisoformat(start);finish=date.fromisoformat(end);result=[]
    while current<=finish:
        known=is_trade_day(current)
        if known is None:
            raise PerformanceDataError('历史交易日历待补齐')
        if known:result.append(current.isoformat())
        current+=timedelta(days=1)
    return result


def _states(initial,trades,dates,corporate=()):
    cash=initial;receivable=0;held=defaultdict(int);cursor=0;sequence=0;states={}
    activities=_activity(trades,corporate)
    for day in dates:
        while cursor<len(activities) and activities[cursor][0].date().isoformat()<=day:
            _,_,_,kind,trade=activities[cursor];cursor+=1
            if kind=='corporate':
                cash+=trade['cash_delta_fen'];receivable+=trade['receivable_delta_fen'];continue
            code=trade['code'];quantity=trade['quantity']
            if trade['side']=='buy':
                held[code]+=quantity;cash-=trade['gross_fen']+trade['fees_fen']
            else:
                held[code]-=quantity;cash+=trade['gross_fen']-trade['fees_fen']
            sequence=trade['sequence']
        states[day]=dict(cash_fen=cash,receivable_fen=receivable,quantities={c:q for c,q in held.items() if q},sequence=sequence)
    return states


def _valid_nav(row,state):
    if (row.get('sequence')!=state['sequence'] or row.get('cash_fen')!=state['cash_fen']
            or row.get('receivable_fen',0)!=state.get('receivable_fen',0)):
        raise PerformanceDataError('已保存的逐日收益与成交账本不一致')
    positions=row.get('positions',{})
    if not isinstance(positions,dict) or {c:p.get('quantity') for c,p in positions.items()}!=state['quantities']:
        raise PerformanceDataError('已保存的逐日持仓与成交账本不一致')
    market=0
    for code,position in positions.items():
        mark=_integer(position.get('close_fen'),minimum=1)
        if _day(position.get('price_as_of',''))>row['date']:
            raise PerformanceDataError('逐日估值引用了后来的价格')
        market+=position['quantity']*mark
    equity=_integer(row.get('equity_fen'),minimum=1)
    if row.get('market_value_fen')!=market or equity!=state['cash_fen']+market+state.get('receivable_fen',0):
        raise PerformanceDataError('已保存的逐日资产待核验')
    return equity


def build_comparison(initial,start,dates,states,nav,stock_prices,index_prices,benchmark):
    """Include start-day open→close; truncate both paths at the same day."""
    anchor=index_prices.get(start,{}).get('open')
    if anchor is None or not math.isfinite(float(anchor)) or float(anchor)<=0:
        raise PerformanceDataError('基准指数缺少起始交易日开盘价，正在补齐')
    anchor=Decimal(str(anchor));equities=[];missing=None
    for day in dates:
        state=states[day]
        if day in nav:
            equity=_valid_nav(nav[day],state)
        else:
            absent=[c for c in state['quantities'] if stock_prices.get(c,{}).get(day,{}).get('close') is None]
            if absent:
                missing=dict(date=day,codes=absent);break
            equity=state['cash_fen']+state.get('receivable_fen',0)+sum(q*_fen(stock_prices[c][day]['close']) for c,q in state['quantities'].items())
        close=index_prices.get(day,{}).get('close')
        if close is None:
            raise PerformanceDataError('基准指数历史收盘数据不完整，正在补齐')
        equities.append((day,equity,Decimal(str(close))))
    points=[dict(date=start+'T09:30:00+08:00',trading_date=start,phase='open',
                 strategy_profit_fen=0,strategy_return_percent=0.,benchmark_profit_fen=0,
                 benchmark_return_percent=0.,excess_profit_fen=0,excess_return_percent=0.)]
    for day,equity,index_close in equities:
        strategy_profit=equity-initial;strategy_return=strategy_profit/initial*100
        benchmark_return=float((index_close/anchor-1)*100)
        benchmark_profit=int((Decimal(initial)*(index_close/anchor-1)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        points.append(dict(date=day+'T15:00:00+08:00',trading_date=day,phase='close',
            strategy_profit_fen=strategy_profit,strategy_return_percent=strategy_return,
            benchmark_profit_fen=benchmark_profit,benchmark_return_percent=benchmark_return,
            excess_profit_fen=strategy_profit-benchmark_profit,excess_return_percent=strategy_return-benchmark_return))
    return dict(points=points if equities else [],as_of=equities[-1][0] if equities else None,
                summary={k:v for k,v in points[-1].items() if k not in ('date','trading_date','phase')} if equities else None,
                missing=missing,benchmark_start_open=float(anchor),benchmark=benchmark)


class PaperPerformanceService:
    def __init__(self,paper, *, fetcher=None):
        self.paper=paper;self.records=paper.store.records;self.fetcher=fetcher or providers.akshare_worker
        self.capacity=asyncio.Semaphore(2);self.tasks={};self.price_tasks={};self.closed=False

    def _snapshot(self,owner):
        # One consistent SELECT; no money lock, writes, or paginated fill limit.
        namespaces=('paper_account','paper_trade','paper_replay','n2_daily_nav','paper_corporate')
        with self.paper.store.db.connection() as conn:
            rows=conn.execute('SELECT namespace,record_key,payload FROM research_records WHERE owner=? AND namespace IN (?,?,?,?,?)',
                              (owner,*namespaces)).fetchall()
        result=dict(account=new_account(),trades=[],corporate=[],replay={},nav={})
        for row in rows:
            value=json.loads(row['payload']);namespace=row['namespace']
            if namespace=='paper_account' and row['record_key']=='account':result['account']=value
            elif namespace=='paper_trade':result['trades'].append(value)
            elif namespace=='paper_replay' and row['record_key']=='active':result['replay']=value
            elif namespace=='n2_daily_nav':result['nav'][_day(value['date'])]=value
            elif namespace=='paper_corporate':result['corporate'].append(value)
        return result

    def _base(self,benchmark):
        return dict(benchmark={'code':benchmark,'name':INDICES[benchmark]['name']},
                    benchmark_options=[{'code':c,'name':v['name']} for c,v in INDICES.items()],
                    points=[],summary=None,start_date=None,as_of=None,initial_cash_fen=None)

    async def performance(self,owner,benchmark='sh000300'):
        if benchmark not in INDICES:
            raise PerformanceDataError('暂不支持该基准指数')
        result=self._base(benchmark)
        now=self.paper.clock().astimezone(SHANGHAI)
        end=previous_trade_day(now.date(),include=now.hour>=15)
        if end is None:
            return dict(result,status='waiting',reason='交易日历待更新')
        end=end.isoformat();snapshot=await asyncio.to_thread(self._snapshot,owner)
        signature=_signature(snapshot,end);key=(owner,benchmark)
        cached=await asyncio.to_thread(self.records.get,'paper_performance','curve:'+benchmark,owner,default={})
        if cached.get('signature')==signature and now.timestamp()-cached.get('computed_at',0)<60:
            return cached['result']
        previous=self.tasks.get(key)
        if previous and previous[0]!=signature:
            previous[1].cancel();self.tasks.pop(key,None);previous=None
        if not previous:
            if self.closed or len(self.tasks)>=16:
                return dict(result,status='waiting',reason='收益数据正在更新，请稍后刷新')
            task=asyncio.create_task(self._compute(owner,benchmark,snapshot,end,signature))
            self.tasks[key]=(signature,task)
        else:task=previous[1]
        try:
            return await asyncio.wait_for(asyncio.shield(task),.06)
        except asyncio.TimeoutError:
            return dict(result,status='loading',reason='正在补齐历史收盘数据',requested_as_of=end)

    @staticmethod
    def _bars(rows,start,end):
        result={}
        for row in rows if isinstance(rows,list) else []:
            try:
                day=_day(row['date']);close=float(row['close'])
                if not start<=day<=end or not math.isfinite(close) or close<=0:continue
                opening=float(row['open']) if row.get('open') is not None else None
                if opening is not None and (not math.isfinite(opening) or opening<=0):opening=None
                result[day]=dict(date=day,open=opening,close=close)
            except (ValueError,TypeError,KeyError,PerformanceDataError):continue
        return result

    @staticmethod
    def _closing_bars(cached,start,end,now):
        """Only a raw 15:00 bar fetched after its close can supplement daily data."""
        if cached.get('adjustment','none')!='none':return {}
        try:fetched=_stamp(cached.get('fetched_at'))
        except PerformanceDataError:return {}
        if fetched>now:return {}
        closes={};opens={}
        for row in cached.get('candles',[]):
            try:
                stamp=_stamp(row['date']);day=stamp.date().isoformat()
                if not start<=day<=end or stamp>now or stamp>fetched or is_trade_day(stamp.date()) is not True:continue
                clock=stamp.strftime('%H:%M:%S')
                if clock in ('09:30:00','09:31:00'):
                    opening=float(row['open'])
                    if math.isfinite(opening) and opening>0:
                        if day not in opens or stamp<opens[day][0]:opens[day]=(stamp,opening)
                if clock=='15:00:00' and stamp.microsecond==0:
                    close=float(row['close'])
                    if math.isfinite(close) and close>0:closes[day]=dict(date=day,close=close,open=None)
            except (PerformanceDataError,ValueError,KeyError,TypeError):continue
        for day,row in closes.items():
            if day in opens:row['open']=opens[day][1]
        return closes

    async def _minute_closes(self,code,start,end, *, refresh):
        now=self.paper.clock().astimezone(SHANGHAI);key=code+':1'
        cached=await asyncio.to_thread(self.records.get,'minute_market',key,default={})
        closes=self._closing_bars(cached,start,end,now)
        market=getattr(self.paper,'minute_market',None)
        if refresh and end not in closes and market is not None:
            await market.snapshot({'code':code,'name':code},1,force=True)
            task=market.tasks.get(key)
            if task:await asyncio.shield(task)
            cached=await asyncio.to_thread(self.records.get,'minute_market',key,default={})
            closes=self._closing_bars(cached,start,end,self.paper.clock().astimezone(SHANGHAI))
        return closes

    async def _prices(self,code,start,end, *, index=False, required=()):
        kind='index' if index else 'stock';key=kind+':'+code;now=self.paper.clock().timestamp()
        record=await asyncio.to_thread(self.records.get,'paper_performance_prices',key,default={})
        bars=self._bars(record.get('bars',[]),start,end)
        if not index:
            daily=await asyncio.to_thread(self.records.get,'daily_market',code,default={})
            if daily.get('quote',{}).get('adjustment','none')=='none':
                bars.update(self._bars(daily.get('candles',[]),start,end))
        missing=set(required)-bars.keys()
        if missing:
            closed=await self._minute_closes(code,start,end,refresh=end in missing)
            for day,row in closed.items():bars.setdefault(day,row)
        if not index:
            if set(required).issubset(bars):return bars
        elif (start in bars and bars[start].get('open') is not None and set(required).issubset(bars)):
            return bars
        if (record.get('requested_start','9999')<=start and record.get('requested_end','')>=end
                and now-record.get('attempted_at',0)<60):
            return bars
        task_key=(kind,code,start,end)
        task=self.price_tasks.get(task_key)
        if task is None:
            task=asyncio.create_task(self._fetch_prices(kind,code,start,end,record))
            self.price_tasks[task_key]=task
        try:
            fetched=await asyncio.shield(task)
            bars.update(fetched)
        finally:
            if task.done() and self.price_tasks.get(task_key) is task:self.price_tasks.pop(task_key,None)
        return bars

    async def _fetch_prices(self,kind,code,start,end,record):
        now=self.paper.clock().timestamp();error=None;rows=[]
        try:
            async with self.capacity:
                response=await asyncio.wait_for(self.fetcher('index_history' if kind=='index' else 'history',code,start,end),45)
            rows=response.get('rows',[]) if isinstance(response,dict) else []
            bars=self._bars(rows,start,end)
            if not bars:raise providers.ProviderError('empty history')
        except asyncio.CancelledError:raise
        except Exception:
            error='指数历史行情暂不可用' if kind=='index' else '持仓历史收盘价暂不可用'
            bars={}
        merged={row['date']:row for row in record.get('bars',[]) if isinstance(row,dict) and row.get('date')}
        merged.update(bars)
        await asyncio.to_thread(self.records.put,'paper_performance_prices',kind+':'+code,
            dict(bars=[merged[d] for d in sorted(merged)],requested_start=min(start,record.get('requested_start',start)),
                 requested_end=max(end,record.get('requested_end',end)),attempted_at=now,error=error))
        return bars

    async def _compute(self,owner,benchmark,snapshot,end,signature):
        result=self._base(benchmark);key=(owner,benchmark)
        try:
            initial,trades=_journal(snapshot)
            replay=snapshot['replay']
            if replay:
                count=replay.get('fills',0)
                valid=(type(count) is int and 0<=count<=len(trades) and replay.get('start_date') and replay.get('end_date'))
                if valid:
                    valid=all(replay['start_date']<=_trade_day(t)<=replay['end_date'] and t.get('execution_mode')=='daily_open_replay' for t in trades[:count])
                if not valid:replay={}
            if not replay:snapshot['nav']={}
            start=_day(replay['start_date']) if replay.get('start_date') else (_trade_day(trades[0]) if trades else None)
            if not start:
                result.update(status='empty',reason='首笔成交后开始展示收益对比')
            elif start>end:
                result.update(status='waiting',reason='首个交易日尚未收盘，收盘后开始对比',start_date=start,requested_as_of=end,initial_cash_fen=initial)
            else:
                dates=_sessions(start,end);states=_states(initial,trades,dates,_corporate(snapshot))
                index_prices=await self._prices(benchmark,start,end,index=True,required=dates)
                if start not in index_prices:
                    raise PerformanceDataError('基准指数未覆盖起始交易日，正在补齐')
                latest=max(index_prices)
                dates=[d for d in dates if d<=latest]
                if any(d not in index_prices for d in dates):
                    raise PerformanceDataError('基准指数历史收盘数据不完整，正在补齐')
                nav={d:row for d,row in snapshot['nav'].items() if start<=d<=dates[-1]}
                needed=defaultdict(list)
                for day in dates:
                    if day in nav:
                        _valid_nav(nav[day],states[day])
                    else:
                        for code in states[day]['quantities']:needed[code].append(day)
                stock_pairs=await asyncio.gather(*(self._stock_pair(c,start,dates[-1],required) for c,required in needed.items()))
                comparison=build_comparison(initial,start,dates,states,nav,dict(stock_pairs),index_prices,benchmark)
                missing=comparison.pop('missing')
                comparison.pop('benchmark')
                result.update(comparison,status='waiting' if missing else 'ready',start_date=start,
                              initial_cash_fen=initial,requested_as_of=end,
                              reason=(missing['date']+'持仓收盘价待补齐：'+'、'.join(missing['codes'])) if missing else None,
                              provenance=dict(basis='same_initial_cash_start_day_open',strategy='账户资金、收盘持仓及应收现金分红，包含成交费用',
                                  benchmark='价格指数；不含股息再投资或交易成本',
                                  replay_note=replay.get('note'),completed_sessions_only=True,
                                  cached_nav_days=len(nav),reconstructed_days=max(0,len(comparison['points'])-1-len(nav))))
                if not missing and result['as_of']<end:
                    result['reason']='最新收盘数据尚未齐全，对比统一截至'+result['as_of']
        except asyncio.CancelledError:raise
        except PerformanceDataError as error:
            result.update(status='waiting',reason=str(error),requested_as_of=end)
        except Exception:
            result.update(status='waiting',reason='收益数据暂不可用，请稍后刷新',requested_as_of=end)
        current=self.tasks.get(key)
        if current and current[0]==signature and current[1] is asyncio.current_task():
            try:
                await asyncio.to_thread(self.records.put,'paper_performance','curve:'+benchmark,
                                       dict(signature=signature,computed_at=self.paper.clock().timestamp(),result=result),owner)
            except Exception:
                pass  # A computed read may still be displayed if its cache write fails.
            self.tasks.pop(key,None)
        return result

    async def _stock_pair(self,code,start,end,required):
        return code,await self._prices(code,start,end,required=required)

    async def close(self):
        self.closed=True
        tasks={t[1] for t in self.tasks.values()}|set(self.price_tasks.values())
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        self.tasks.clear();self.price_tasks.clear()
