"""Backfill dated Shanghai Exchange treasury ETF shares and fund unit NAV.

Run from the repository root using python -m scripts.backfill_treasury_etf.
"""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from backend.etf_funds_sources import (
    SHANGHAI,
    SSE_QUERY,
    SSE_SHARES_SOURCE,
    _day,
    _eastmoney_nav,
    _json,
    _number,
    _sse_shares,
)

# Listing dates, not fund contract dates. Fund formation and initial conversions
# precede these dates, so no pre-listing holdings count as net subscriptions.
LISTED = {
    '511010': date(2013, 3, 25),
    '511020': date(2019, 2, 22),
    '511090': date(2023, 6, 13),
    '511100': date(2023, 12, 25),
    '511130': date(2024, 3, 28),
    '511160': date(2025, 1, 6),
    '511260': date(2017, 8, 24),
}
GUOTAI_CODES = {'511010', '511260'}
GUOTAI_NAV_SOURCE = '国泰基金历史单位净值'
GUOTAI_NAV_API = 'https://e.gtfund.com/Etrade/Public/cochin/info.fund.nav_history'
FIELDS = ('date', 'code', 'shares', 'nav', 'split_ratio', 'source_shares', 'source_nav')


def _single_shares(client, day, code):
    details = _json(client, SSE_QUERY, {
        'isPagination': 'false',
        'sqlId': 'COMMON_SSE_ZQPZ_ETFZL_ETFJBXX_JJGM_SEARCH_L',
        'SEC_CODE': code,
        'STAT_DATE': day.isoformat(),
    }, 'https://www.sse.com.cn/')
    return {(code, day): amount for row in details['result']
            if row['SEC_CODE'] == code and _day(row['STAT_DATE']) == day
            if (amount := _number(row['TOT_VOL'], 10000)) is not None}


def _day_shares(client, day, codes):
    active = {code for code in codes if LISTED[code] <= day}
    if len(active) == 1:
        return _single_shares(client, day, next(iter(active)))
    return _sse_shares(client, day, active) if active else {}


def _guotai_nav(client, code, dates):
    years = defaultdict(set)
    for day in dates:
        years[day.year].add(day)
    result = {}
    for year_dates in years.values():
        params = {
            'fundCode': code,
            'start': f'{min(year_dates)}T00:00:00.000Z',
            'end': f'{max(year_dates)}T00:00:00.000Z',
            'dayinc': True,
            'order': 'asc',
        }
        response = client.post(GUOTAI_NAV_API, json={'api': 'info.fund.nav_history', 'params': params}, headers={
            'Referer': f'https://e.gtfund.com/Etrade/Jijin/view/id/{code}',
            'X-Requested-With': 'XMLHttpRequest',
        })
        response.raise_for_status()
        for row in response.json():
            day = datetime.fromisoformat(row['date'].replace('Z', '+00:00')).astimezone(SHANGHAI).date()
            if day in year_dates and (nav := _number(row['nav'])) is not None:
                result[day] = (nav, GUOTAI_NAV_SOURCE)
    return result


def _unit_nav(client, code, dates):
    if code not in GUOTAI_CODES:
        return _eastmoney_nav(client, code, dates)
    try:
        result = _guotai_nav(client, code, dates)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        result = {}
    if missing := dates - result.keys():
        result.update(_eastmoney_nav(client, code, missing))
    return result


def _cached_rows(path):
    if not path.exists():
        return {}
    with path.open(newline='', encoding='utf-8') as fp:
        return {(row['code'], _day(row['date'])): row for row in csv.DictReader(fp)}


def _write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', newline='', encoding='utf-8') as fp:
        writer = csv.DictWriter(fp, FIELDS)
        writer.writeheader()
        writer.writerows(rows[key] for key in sorted(rows, key=lambda key: (key[1], key[0])))
    tmp.replace(path)


def collect_treasury_etf(sessions, codes, output, *, workers=6, refresh_nav=False):
    codes = set(codes)
    dates = sorted(set(sessions))
    rows = _cached_rows(output)
    wanted = {(code, day) for code in codes for day in dates if day >= LISTED[code]}
    missing_shares = {day for code, day in wanted if not rows.get((code, day), {}).get('shares')}
    missing_nav = {code: {day for item, day in wanted
                         if item == code and (refresh_nav or not rows.get((code, day), {}).get('nav'))}
                   for code in codes}
    errors = {}
    limits = httpx.Limits(max_connections=workers + len(codes), max_keepalive_connections=workers + len(codes))
    transport = httpx.HTTPTransport(retries=1, limits=limits)
    with httpx.Client(timeout=10, transport=transport, headers={'User-Agent': 'Mozilla/5.0'}) as client:
        # NAV pagination is independent from exchange share collection.
        with ThreadPoolExecutor(max_workers=workers + len(codes)) as pool:
            share_jobs = {pool.submit(_day_shares, client, day, codes): day for day in sorted(missing_shares)}
            nav_jobs = {pool.submit(_unit_nav, client, code, days): code
                        for code, days in missing_nav.items() if days}
            for future in as_completed(share_jobs):
                day = share_jobs[future]
                try:
                    result = future.result()
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    errors['shares', day] = type(exc).__name__
                    continue
                for (code, session), amount in result.items():
                    key = code, session
                    if key in wanted:
                        row = rows.setdefault(key, {'date':session.isoformat(), 'code':code})
                        row.update(shares=str(amount), source_shares=SSE_SHARES_SOURCE)
            for future in as_completed(nav_jobs):
                code = nav_jobs[future]
                try:
                    result = future.result()
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    errors['nav', code] = type(exc).__name__
                    continue
                for day, (amount, source) in result.items():
                    row = rows.setdefault((code, day), {'date':day.isoformat(), 'code':code})
                    row.update(nav=str(amount),source_nav=source)
    # Initial conversions precede listing. For later transitions require a
    # continuous NAV on the verified calendar before marking split_ratio=1.
    # A discontinuity needs independent confirmation before it is used as flow.
    for code in codes:
        previous_nav = None
        for offset, day in enumerate(dates):
            row = rows.get((code, day), {})
            nav = _number(row.get('nav'))
            if row:
                if nav is None or previous_nav is None:
                    if day == LISTED[code] and nav is not None:
                        row['split_ratio'] = '1'
                    elif offset or 'split_ratio' not in row:
                        row['split_ratio'] = ''
                else:
                    row['split_ratio'] = '1' if Decimal('0.80') <= nav / previous_nav <= Decimal('1.25') else ''
            previous_nav = nav
    _write_rows(output, rows)
    return rows, errors, wanted


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--calendar', type=Path, required=True, help='CSV with a date column of verified stock sessions')
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--start', type=date.fromisoformat)
    cli.add_argument('--end', type=date.fromisoformat)
    cli.add_argument('--code', choices=LISTED, action='append')
    cli.add_argument('--refresh-nav', action='store_true')
    args = cli.parse_args()
    with args.calendar.open(newline='', encoding='utf-8') as fp:
        sessions = [_day(row['date']) for row in csv.DictReader(fp)]
    sessions = [day for day in sessions if (not args.start or day >= args.start) and (not args.end or day <= args.end)]
    codes = args.code or ['511010']
    rows, errors, wanted = collect_treasury_etf(sessions, codes, args.output, refresh_nav=args.refresh_nav)
    for code in codes:
        dates = [day for item, day in wanted if item == code]
        joined = sorted(day for day in dates if all(rows.get((code, day), {}).get(key) for key in ('shares','nav')))
        print(f'{code}: {len(joined)}/{len(dates)} paired rows; {joined[0] if joined else "-"} to {joined[-1] if joined else "-"}')
    print(f'Source errors: {len(errors)}')
    if errors:
        print(list(errors.items())[:10])


if __name__ == '__main__':
    main()
