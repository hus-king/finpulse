"""Daily Stock Connect data with the August 2024 disclosure boundary."""
import asyncio
import logging
import math
from collections import deque
from datetime import date, datetime, time, timedelta
from time import monotonic

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from .news_cleaning import SHANGHAI
from .trading_calendar import previous_trade_day

router = APIRouter(prefix='/api/market')
LOG = logging.getLogger(__name__)
START = '2014-11-17'
DISCLOSURE_CHANGE = '2024-08-19'
FLOW_API = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
INDEX_API = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'


def parse_flows(rows, close_through):
    history = {}
    for row in rows:
        day = date.fromisoformat(row['TRADE_DATE'][:10]).isoformat()
        if day in history or day < START:
            raise ValueError('北向日期重复或超出历史范围')
        turnover, index = float(row['DEAL_AMT']), float(row['INDEX_CLOSE_PRICE'])
        if not all(math.isfinite(value) and value > 0 for value in (turnover, index)):
            raise ValueError('北向成交额或上证收盘无效')
        net = row['NET_DEAL_AMT'] if day < DISCLOSURE_CHANGE else None
        if net is not None:
            net = float(net)
            if not math.isfinite(net) or abs(net) > turnover + .02:
                raise ValueError('北向净买入与总成交额不一致')
        history[day] = (net, turnover, index)

    result, net_window, turnover_window = [], deque(maxlen=5), deque(maxlen=5)
    for day, (net, turnover, index) in sorted(history.items()):
        if day > close_through:
            break
        net_window.append(net)
        turnover_window.append(turnover)
        result.append(dict(
            date=day, net=None if net is None else round(net / 100, 4),
            net_5d=round(sum(net_window) / 100, 4) if len(net_window) == 5 and None not in net_window else None,
            turnover=round(turnover / 100, 4), sse_close=index,
            turnover_5d=round(sum(turnover_window) / 500, 4) if len(turnover_window) == 5 else None,
        ))
    return result


async def fetch_flows(client):
    params = dict(reportName='RPT_MUTUAL_DEAL_HISTORY', columns='TRADE_DATE,NET_DEAL_AMT,DEAL_AMT,INDEX_CLOSE_PRICE',
                  source='WEB', client='WEB', filter='(MUTUAL_TYPE="005")',
                  sortColumns='TRADE_DATE', sortTypes=-1, pageSize=2000)

    async def page(number):
        response = await client.get(FLOW_API, params={**params, 'pageNumber': number})
        response.raise_for_status()
        return response.json()['result']

    first = await page(1)
    pages = int(first['pages'])
    if not 1 <= pages <= 6:
        raise ValueError('北向数据页数异常')
    rows = first['data']
    for number in range(2, pages + 1):
        rows += (await page(number))['data']
    if len(rows) != first['count'] or not rows or rows[-1]['TRADE_DATE'][:10] != START:
        raise ValueError('北向历史数据不完整')
    return rows


async def fetch_benchmark(client, close_through):
    response = await client.get(INDEX_API, params=dict(secid='1.000300', klt=101, fqt=0,
                                beg=START.replace('-', ''), end=close_through.replace('-', ''), lmt=10000,
                                fields1='f1,f2,f3,f4,f5,f6', fields2='f51,f52,f53'))
    response.raise_for_status()
    benchmark = {}
    for row in response.json()['data']['klines']:
        day, _, price = row.split(',')
        close = float(price)
        if START <= day <= close_through and math.isfinite(close) and close > 0:
            benchmark[day] = close
    return benchmark


class NorthboundService:
    def __init__(self, store):
        self.store = store
        self.cached = None
        self.retry_after = 0.
        self.lock = asyncio.Lock()

    async def snapshot(self):
        async with self.lock:
            if self.cached is None:
                self.cached = await asyncio.to_thread(self.store.get, 'market_monitor', 'northbound')
            now = datetime.now(SHANGHAI)
            after_close = now.time() >= time(16, 30)
            expected = previous_trade_day(now.date(), include=after_close)
            latest_closed = now.date() if after_close else now.date() - timedelta(days=1)
            close_through = (expected or latest_closed).isoformat()
            if monotonic() >= self.retry_after and (not self.cached or self.cached['as_of'] < close_through):
                self.retry_after = monotonic() + 900
                try:
                    async with httpx.AsyncClient(timeout=6) as client:
                        flow_rows = await fetch_flows(client)
                        history = parse_flows(flow_rows, close_through)
                        if not history or (self.cached and history[-1]['date'] < self.cached['as_of']):
                            raise ValueError('北向行情回退')
                        try:
                            benchmark = await fetch_benchmark(client, history[-1]['date'])
                        except (httpx.HTTPError, ValueError, TypeError, KeyError):
                            LOG.warning('CSI 300 source unavailable for Northbound snapshot')
                            benchmark = {}
                    old = {row['date']: row for row in self.cached['history']} if self.cached else {}
                    for row in history:
                        row['csi300_close'] = benchmark.get(row['date']) or old.get(row['date'], {}).get('csi300_close')
                    snapshot = dict(as_of=history[-1]['date'], disclosure_change=DISCLOSURE_CHANGE, history=history)
                    await asyncio.to_thread(self.store.put, 'market_monitor', 'northbound', snapshot)
                    self.cached = snapshot
                except (httpx.HTTPError, ValueError, TypeError, KeyError):
                    LOG.warning('Northbound source unavailable', exc_info=True)
            if not self.cached:
                raise HTTPException(503, '北向资金数据暂未可用')
            return self.cached


@router.get('/northbound')
async def northbound(request: Request, response: Response):
    snapshot = await request.app.state.northbound.snapshot()
    response.headers['Cache-Control'] = 'public, max-age=300'
    return snapshot
