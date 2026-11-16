"""Daily exchange ETF shares and cash-flow estimates for the market overview."""
import argparse
import asyncio
import csv
import logging
import math
import os
import sys
from datetime import date, datetime, time, timedelta
from statistics import median

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import APIRouter, Request

from .etf_funds_sources import collect_etf_rows
from .instruments import INDICES
from .news_cleaning import SHANGHAI
from .providers import ROOT, read_config
from .trading_calendar import is_trade_day, previous_trade_day

LOG = logging.getLogger(__name__)
router = APIRouter(prefix='/api/market')
NAMESPACE = 'etf_fund_day'
# Use fixed product IDs. Daily aggregates require every product in a group on
# both adjacent sessions, so a later listing cannot manufacture a flow spike.
GROUPS = {
    'sh000300': ('沪深300', {
        '510300': '沪深300ETF华泰柏瑞', '510310': '沪深300ETF易方达',
        '510330': '沪深300ETF华夏', '159919': '沪深300ETF嘉实',
    }),
    'sh000016': ('上证50', {'510050': '上证50ETF华夏'}),
    'sh000905': ('中证500', {'510500': '中证500ETF南方', '159922': '中证500ETF嘉实'}),
    'sh000852': ('中证1000', {'512100': '中证1000ETF南方', '159845': '中证1000ETF华夏'}),
}
SYMBOLS = {code: ('SZSE' if code.startswith('15') else 'SSE')
           for _, members in GROUPS.values() for code in members}
FIELDS = ('total_shares', 'unit_nav', 'turnover_cny')
SOURCE_FIELDS = ('shares_source', 'nav_source', 'turnover_source')
# Confirmed by fund notices and the effective change in the exchange share series.
# Keep the raw observations on conversion days and omit their derived flow.
SHARE_CONVERSIONS = {
    ('510500', '2015-04-14'), ('159919', '2019-01-11'), ('159922', '2022-02-17'),
    ('159845', '2022-08-01'), ('510500', '2022-08-26'), ('512100', '2022-09-02'),
    ('159845', '2023-02-28'), ('510310', '2024-09-20'), ('159922', '2024-11-29'),
}
EVENTS = [
    dict(date='2025-04-08', entity='全国社保基金理事会', title='披露近日已增持国内股票',
         url='https://www.ssf.gov.cn/portal/hndt/hndt/webinfo/2025/04/1745825603471701.htm'),
    dict(date='2025-04-07', entity='中央汇金', title='披露已再次增持 ETF',
         url='https://www.csrc.gov.cn/shanxi/c106408/c7555954/content.shtml'),
    dict(date='2024-02-06', entity='中央汇金', title='公告扩大 ETF 增持范围',
         url='https://www.csrc.gov.cn/csrc/c100028/c7462111/content.shtml'),
]


def date_keys(start, end):
    return [(start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)]


def expected_session(now):
    run_day = previous_trade_day(now.date(), include=now.time() >= time(9))
    return previous_trade_day(run_day) if run_day else None


def _positive(value, allow_zero=False):
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or (not allow_zero and not number):
        raise ValueError('Invalid ETF observation')
    return number


def _window_sum(values, size):
    window = values[-size:]
    return sum(window) if len(window) == size and all(value is not None for value in window) else None


def _flow(current, previous):
    if not current or not previous or (current['code'], current['trade_date']) in SHARE_CONVERSIONS:
        return None, None
    nav, previous_nav = current['unit_nav'], previous['unit_nav']
    if nav and previous_nav:
        # A large unit-NAV discontinuity can indicate a share conversion.
        # Retain both raw observations and leave the derived day unavailable.
        if not 0.75 < nav / previous_nav < 1.25:
            return None, None
    change = current['total_shares'] - previous['total_shares']
    return change, change * nav if nav is not None else None


def _flow_alert(rates, rate):
    if rate is None or len(rates) < 60 or any(value is None for value in rates):
        return None
    ordered = sorted(rates)
    if rate > max(0.0, ordered[56]):
        return 'in'
    if rate < min(0.0, ordered[3]):
        return 'out'
    return None


class EtfFundsService:
    def __init__(self, store, daily_market=None, *, collector=None, clock=None):
        self.store = store
        self.daily_market = daily_market
        self.collector = collector or collect_etf_rows
        self.clock = clock or (lambda: datetime.now(SHANGHAI))
        self.scheduler = None

    def start(self):
        settings = read_config().get('scheduler', {})
        background = os.environ.get('FINPULSE_SCHEDULER_ENABLED', str(int(settings.get('enabled', False))))
        if os.environ.get('FINPULSE_ETF_FUNDS_ENABLED', background) != '1':
            return
        self.scheduler = AsyncIOScheduler(timezone=SHANGHAI)
        self.scheduler.add_job(self.run, 'cron', day_of_week='mon-fri', hour=9, minute=0,
                               id='etf_funds_daily', coalesce=True, max_instances=1, misfire_grace_time=3600)
        # Repeat once for staggered exchange publication. Completed dates are skipped.
        self.scheduler.add_job(self.run, 'cron', day_of_week='mon-fri', hour=9, minute=20,
                               id='etf_funds_retry', coalesce=True, max_instances=1, misfire_grace_time=3600)
        self.scheduler.add_job(self.run, id='etf_funds_catchup')
        self.scheduler.start()

    def close(self):
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def run(self):
        now = self.clock().astimezone(SHANGHAI)
        if now.time() < time(9) or is_trade_day(now.date()) is not True:
            return
        target = previous_trade_day(now.date())
        if target is None:
            return
        try:
            await asyncio.to_thread(self.collect, target, target, refresh=True, skip_complete=True)
        except Exception:
            LOG.exception('ETF funds update failed for %s', target)
        if self.daily_market:
            await asyncio.gather(*(self.daily_market.get(INDICES[index], force=False) for index in GROUPS),
                                 return_exceptions=True)

    def collect(self, start, end, *, refresh=False, skip_complete=False):
        keys = set(date_keys(start, end))
        if skip_complete:
            existing = self.store.get(NAMESPACE, start.isoformat(), default={})
            if existing.keys() == SYMBOLS.keys() and all(
                    row['unit_nav'] is not None and row['turnover_cny'] is not None for row in existing.values()):
                return {'days': 0, 'rows': 0, 'errors': {}}
        result = self.collector(SYMBOLS, start, end)
        now = self.clock().astimezone(SHANGHAI).isoformat(timespec='seconds')
        report = {}
        for source in result['rows']:
            day, code = source['trade_date'], source['code']
            if day not in keys or SYMBOLS.get(code) != source['exchange'] or code in report.get(day, {}):
                raise ValueError('ETF response date, exchange or identifier mismatch')
            values = {field: _positive(source.get(field), allow_zero=field == 'turnover_cny')
                      for field in FIELDS}
            if not values['total_shares']:
                raise ValueError('ETF response is missing official shares')
            report.setdefault(day, {})[code] = {**source, **values}
        for day, rows in report.items():
            self._save_day(day, rows, now, refresh)
        if result['errors']:
            LOG.warning('ETF funds partial collection %s to %s: %s', start, end, result['errors'])
        return {'days': len(report), 'rows': sum(map(len, report.values())), 'errors': result['errors']}

    def _save_day(self, day, rows, now, refresh):
        # Reread under a dated lease so a concurrent historical import cannot
        # overwrite a newly completed daily run or erase an optional field.
        key = 'etf-funds:' + day
        token = self.store.claim(key, 60)
        if not token:
            return
        try:
            existing = self.store.get(NAMESPACE, day, default={})
            updated = dict(existing)
            for code, source in rows.items():
                previous = existing.get(code, {})
                row = dict(previous, code=code, trade_date=day, exchange=source['exchange'])
                for field, source_field in zip(FIELDS, SOURCE_FIELDS):
                    value = source[field]
                    if value is None or (previous.get(field) is not None and not refresh):
                        row.setdefault(field, None)
                        continue
                    if value != previous.get(field) or source[source_field] != previous.get(source_field):
                        row.update({field: value, source_field: source[source_field],
                                    field + '_observed_at': now})
                updated[code] = row
            if updated != existing:
                self.store.put(NAMESPACE, day, updated)
        finally:
            self.store.release(key, token)

    def snapshot(self):
        now = self.clock().astimezone(SHANGHAI)
        expected = expected_session(now)
        expected_date = expected.isoformat() if expected else None
        end = expected or (now.date() - timedelta(days=1))
        keys = date_keys(end - timedelta(days=150), end)
        stored = self.store.get_many(NAMESPACE, keys)
        complete = [day for day, rows in stored.items() if rows.keys() == SYMBOLS.keys()]
        latest = max(complete, default=max(stored, default=None))
        status = 'not_ready'
        if latest:
            status = 'ok' if latest == expected_date and set(stored[latest]) == SYMBOLS.keys() else 'stale'
        days = [day for day in keys if latest and day <= latest
                and (day in stored or is_trade_day(date.fromisoformat(day)) is True)]
        index_data = self.store.get_many('daily_market', GROUPS) if days else {}
        groups = [self._group(index, name, members, days, stored, index_data.get(index, {}))
                  for index, (name, members) in GROUPS.items()] if days else []
        latest_records = stored.get(latest, {})
        available = [at for row in latest_records.values()
                     for field in FIELDS if (at := row.get(field + '_observed_at'))]
        return dict(status=status, latest_date=latest, expected_date=expected_date,
                    as_of=max(available, default=None), groups=groups, events=EVENTS)

    def _group(self, index, name, members, days, stored, index_data):
        rows, per_code = [], {}
        day = days[-1]
        for code, label in members.items():
            timeline = [stored.get(session, {}).get(code) for session in days]
            diffs = [_flow(current, previous) for previous, current in
                     zip([None, *timeline], timeline)]
            flows = [value for _, value in diffs]
            per_code[code] = flows
            latest = timeline[-1]
            if not latest:
                continue
            current_change = diffs[-1][0]
            rates = [change / previous['total_shares'] if change is not None else None
                     for previous, (change, _) in zip([None, *timeline], diffs)]
            turnover = latest['turnover_cny']
            prior_turnovers = [item.get('turnover_cny') if item else None for item in timeline[-61:-1]]
            typical = (median(prior_turnovers) if len(prior_turnovers) == 60
                       and all(value is not None for value in prior_turnovers) else None)
            ratio = turnover / typical if turnover is not None and typical else None
            rows.append(dict(code=code, name=label, trade_date=day, shares=latest['total_shares'],
                             share_change=current_change, net_flow=flows[-1], flow_5d=_window_sum(flows, 5),
                             flow_20d=_window_sum(flows, 20), turnover=turnover, volume_ratio=ratio,
                             volume_alert=ratio is not None and ratio >= 2, flow_alert=_flow_alert(rates[-61:-1], rates[-1])))

        prices = {bar['date']: bar['close'] for bar in index_data.get('candles', [])}
        history = []
        start = max(0, len(days) - 60)
        for offset in range(start, len(days)):
            net = [flows[offset] for flows in per_code.values()]
            history.append(dict(date=days[offset], net_flow=sum(net) if all(x is not None for x in net) else None,
                                index_close=prices.get(days[offset])))
        return dict(id=index, name=name, history=history, rows=rows)


@router.get('/etf-funds')
async def etf_funds(request: Request):
    return await asyncio.to_thread(request.app.state.etf_funds.snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    parser.add_argument('--export', action='store_true', help='Read persisted raw history as CSV instead of collecting')
    args = parser.parse_args()
    if args.start > args.end or args.end >= datetime.now(SHANGHAI).date():
        parser.error('Date range must end before today')
    from .database import create_auth_store
    from .research_store import ResearchStore
    store = ResearchStore(create_auth_store(ROOT))
    store.initialize()
    if args.export:
        columns = ['code', 'exchange', 'trade_date', *FIELDS, *SOURCE_FIELDS,
                   *(field + '_observed_at' for field in FIELDS), 'share_conversion']
        writer = csv.DictWriter(sys.stdout, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        rows = store.get_many(NAMESPACE, date_keys(args.start, args.end))
        for day, items in sorted(rows.items()):
            writer.writerows(dict(items[code], share_conversion=(code, day) in SHARE_CONVERSIONS)
                             for code in sorted(items))
    else:
        service = EtfFundsService(store)
        start = args.start
        while start <= args.end:
            end = min(start + timedelta(days=89), args.end)
            print(start, end, service.collect(start, end), flush=True)
            start = end + timedelta(days=1)


if __name__ == '__main__':
    main()
