"""Estimate unusual broad ETF subscriptions as a share of basket assets."""
import asyncio
import csv
from collections import deque
from datetime import date, timedelta
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Request

from .etf_funds import EVENTS, GROUPS, NAMESPACE, _flow, _flow_alert, date_keys, expected_session
from .providers import ROOT
from .trading_calendar import is_trade_day

router = APIRouter(prefix='/api/market')
Basket = Literal['wide', 'long']
BASKETS = {
    'wide': ('2021-03-31', tuple(tuple(members) for _, members in GROUPS.values())),
    'long': ('2013-03-25', (('510050',), ('510300', '510310', '510330'), ('510500',))),
}
ANNOUNCEMENTS = [
    dict(date='2015-07-09', entity='上海证监局', title='披露券商维稳资金已划拨证金公司',
         url='https://www.csrc.gov.cn/shanghai/c105566/c1274743/content.shtml'),
    dict(date='2023-10-23', entity='中央汇金', title='披露当日买入 ETF',
         url='https://www.news.cn/fortune/2023-10/23/c_1129933214.htm'),
    *EVENTS,
]


def flow_indicator(sessions, records, groups):
    prior = [deque(maxlen=60) for _ in groups]
    window = deque(maxlen=20)
    history = []

    for previous_day, day in zip(sessions, sessions[1:]):
        today, previous = records.get(day, {}), records.get(previous_day, {})
        group_flows, group_assets, alerts = [], [], []
        for group, rates in zip(groups, prior):
            flows = [_flow(today.get(code), previous.get(code))[1] for code in group]
            if any(flow is None for flow in flows) or any(not previous[code]['unit_nav'] for code in group):
                continue
            net = sum(flows)
            assets = sum(previous[code]['total_shares'] * previous[code]['unit_nav'] for code in group)
            rate = net / assets
            alerts.append(_flow_alert(list(rates), rate) if len(rates) == 60 else 'warming')
            group_flows.append(net)
            group_assets.append(assets)
            rates.append(rate)

        net = sum(group_flows) if len(group_flows) == len(groups) else None
        ready = net is not None and 'warming' not in alerts
        votes = None
        value, amount = None, None
        if ready:
            votes = alerts.count('in' if net > 0 else 'out') if net else 0
            window.append((net if votes >= 2 else 0, sum(group_assets)))
            if len(window) == 20:
                amount = sum(flow for flow, _ in window)
                value = amount * 100 / window[0][1]

        if value is not None or history:
            history.append(dict(date=day, score=round(value, 4) if value is not None else None,
                                amount=round(amount, 2) if amount is not None else None,
                                net=round(net, 2) if net is not None else None,
                                agreeing_groups=votes))
    return history


@lru_cache(maxsize=1)
def index_archive():
    with (ROOT / 'public/market-archives/sse-composite-close.csv').open() as source:
        return {row['date']: float(row['close']) for row in csv.DictReader(source)}


@lru_cache(maxsize=4)
def indicator_snapshot(store, basket, end, _cache_bucket):
    start, groups = BASKETS[basket]
    closes = dict(index_archive())
    current = store.get('daily_market', 'sh000001', default={})
    closes.update({bar['date']: bar['close'] for bar in current.get('candles', []) if bar['date'] not in closes})
    session_set = {day for day in closes if start <= day <= end.isoformat()}
    archive_end = date.fromisoformat(max(index_archive()))
    if archive_end < end:
        session_set.update(day for day in date_keys(archive_end + timedelta(days=1), end)
                           if is_trade_day(date.fromisoformat(day)) is True)
    sessions = sorted(session_set)
    stored = store.get_many(NAMESPACE, sessions)
    history = flow_indicator(sessions, stored, groups)
    for point in history:
        point['index_close'] = closes.get(point['date'])
    return dict(
        funds=[code for group in groups for code in group], group_count=len(groups),
        expected_date=end.isoformat(), history=history,
        announcements=sorted(ANNOUNCEMENTS, key=lambda event: event['date']),
    )


@router.get('/etf-flow-indicator')
async def etf_flow_indicator(request: Request, basket: Basket = 'wide'):
    service = request.app.state.etf_funds
    now = service.clock()
    end = expected_session(now) or now.date() - timedelta(days=1)
    return await asyncio.to_thread(indicator_snapshot, service.store, basket, end, int(now.timestamp()) // 300)
