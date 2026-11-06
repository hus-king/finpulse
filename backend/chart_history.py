"""Chart-only adjusted daily history. Canonical quotes and money stay raw."""
import asyncio
from datetime import date, timedelta
import json
import math
import os
from pathlib import Path

import httpx

from .daily_history import HISTORY_START
from .instruments import is_index

VERSION='tushare-adj-factor-v1'


class AdjustmentError(ValueError):
    pass


def ts_code(code):
    if len(code)!=6 or not code.isdigit() or not code.startswith(('0','3','6')):
        raise AdjustmentError('仅支持沪深A股的复权日线')
    return code+('.SH' if code.startswith('6') else '.SZ')


def adjusted_bars(bars,rows,code,mode):
    expected=ts_code(code);end=bars[-1]['date'];factors={}
    for row in rows:
        try:
            day=date.fromisoformat(str(row['trade_date'])[:4]+'-'+str(row['trade_date'])[4:6]+'-'+str(row['trade_date'])[6:8]).isoformat()
            if day>end:continue
            value=float(row['adj_factor'])
            if row['ts_code']!=expected or day in factors or not math.isfinite(value) or value<=0:
                raise ValueError
            factors[day]=value
        except (KeyError,TypeError,ValueError,OverflowError):
            raise AdjustmentError('复权因子格式、证券身份或日期重复，暂不能计算') from None
    missing=[bar['date'] for bar in bars if bar['date'] not in factors]
    if missing:raise AdjustmentError('复权因子未覆盖全部价格日期，暂不能计算')
    if mode not in ('qfq','hfq'):raise AdjustmentError('未知复权方式')
    anchor=factors[end] if mode=='qfq' else 1.
    result=[]
    for bar in bars:
        ratio=factors[bar['date']]/anchor
        prices={key:bar[key]*ratio for key in ('open','high','low','close')}
        if not all(math.isfinite(v) and v>0 for v in prices.values()):
            raise AdjustmentError('复权价格无效，暂不能绘图')
        result.append({**bar,**prices})
    return result


class ChartHistoryService:
    def __init__(self,daily,*,fetcher=None,config=None):
        self.daily=daily;self.store=daily.store;self.fetcher=fetcher or self._fetch
        self.config=config;self.client=None;self.tasks={};self.closed=False
        self.capacity=asyncio.Semaphore(2);self.rate=asyncio.Lock();self.last_call=0.

    async def get(self,stock,mode='none'):
        if mode not in ('none','qfq','hfq'):raise AdjustmentError('未知复权方式')
        if mode!='none' and is_index(stock['code']):raise AdjustmentError('指数不适用个股复权方式')
        if mode!='none':ts_code(stock['code'])
        record=await asyncio.to_thread(self.daily.cached,stock['code'])
        bars=record.get('candles',[])
        base=dict(code=stock['code'],adjustment=mode,candles=[],from_date=bars[0]['date'] if bars else None,
                  to_date=bars[-1]['date'] if bars else None,anchor_date=bars[-1]['date'] if bars else None,
                  factor_through=None,source='原始历史日线' if mode=='none' else 'Tushare / adj_factor',
                  next_poll_seconds=2,message=None)
        if not bars:
            state=await self.daily.snapshot(stock)
            return dict(base,status='loading' if state.get('refreshing') else 'unavailable',message='正在准备历史日线' if state.get('refreshing') else '暂未取得历史日线')
        if mode=='none':return dict(base,status='ready',candles=bars)
        end=bars[-1]['date'];now=self.daily.clock().timestamp();key=stock['code']
        cached=await asyncio.to_thread(self.store.get,'chart_factors',key,default={})
        if cached.get('version')==VERSION and cached.get('through','')>=end:
            try:
                adjusted=adjusted_bars(bars,cached.get('rows',[]),key,mode)
                return dict(base,status='ready',candles=adjusted,factor_through=end)
            except AdjustmentError:
                pass  # A longer/changed raw history requires complete factor coverage.
        task_key=(key,end)
        if task_key in self.tasks:return dict(base,status='loading',message='正在准备复权因子')
        if cached.get('error_through')==end and now<cached.get('retry_at',0):
            return dict(base,status='unavailable',message=cached['error'],next_poll_seconds=60)
        if self.closed or len(self.tasks)>=16:
            return dict(base,status='unavailable',message='复权数据请求繁忙，请稍后重试')
        self.tasks[task_key]=asyncio.create_task(self._collect(key,end,bars))
        return dict(base,status='loading',message='正在准备复权因子')

    async def _collect(self,code,end,bars):
        key=(code,end)
        try:
            async with self.capacity:
                rows=await asyncio.wait_for(self.fetcher(code,HISTORY_START,end),90)
                # Check every actual price day, including the anchor; no forward filling.
                adjusted_bars(bars,rows,code,'qfq')
            record=dict(version=VERSION,through=end,rows=rows,collected_at=self.daily.clock().isoformat())
        except asyncio.CancelledError:raise
        except Exception as error:
            message=str(error) if isinstance(error,AdjustmentError) else '复权因子暂不可用，请稍后重试'
            record=dict(version=VERSION,error=message,error_through=end,retry_at=self.daily.clock().timestamp()+60)
        try:
            previous=await asyncio.to_thread(self.store.get,'chart_factors',code,default={})
            if previous.get('through','')<=end:
                if record.get('error'):record={**previous,**record}
                await asyncio.to_thread(self.store.put,'chart_factors',code,record)
        finally:self.tasks.pop(key,None)

    async def _fetch(self,code,start,end):
        config=self.config
        if config is None:
            path=os.environ.get('FINPULSE_N2_PROVIDER_CONFIG')
            if not path:raise AdjustmentError('复权数据源尚未配置')
            try:config=json.loads(Path(path).read_text())
            except (OSError,ValueError):raise AdjustmentError('复权数据源配置不可用') from None
        url=config.get('http_url') or config.get('base_url');token=config.get('token')
        if not url or not token:raise AdjustmentError('复权数据源尚未配置')
        if self.client is None:self.client=httpx.AsyncClient(timeout=35)
        expected=ts_code(code);first=date.fromisoformat(start);last=date.fromisoformat(end);rows=[]
        while first<=last:
            # Four calendar years stay below1000 actual sessions for this market.
            stop=min(last,date(first.year+3,12,31))
            async with self.rate:
                delay=.6-(asyncio.get_running_loop().time()-self.last_call)
                if delay>0:await asyncio.sleep(delay)
                self.last_call=asyncio.get_running_loop().time()
            response=await self.client.post(url,json=dict(api_name='adj_factor',token=token,
                params=dict(ts_code=expected,start_date=first.strftime('%Y%m%d'),end_date=stop.strftime('%Y%m%d')),
                fields='ts_code,trade_date,adj_factor'))
            response.raise_for_status();payload=response.json()
            if payload.get('code')!=0:raise AdjustmentError('复权因子数据源返回失败，请稍后重试')
            data=payload.get('data') or {};fields=data.get('fields',[]);items=data.get('items',[])
            if (set(fields)!={'ts_code','trade_date','adj_factor'} or not isinstance(items,list)
                    or len(items)>1461 or any(not isinstance(row,list) or len(row)!=len(fields) for row in items)):
                raise AdjustmentError('复权因子数据格式或日期覆盖异常')
            rows.extend(dict(zip(fields,row)) for row in items)
            first=stop+timedelta(days=1)
        return rows

    async def close(self):
        self.closed=True
        for task in list(self.tasks.values()):task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True);self.tasks.clear()
        if self.client is not None:await self.client.aclose()
