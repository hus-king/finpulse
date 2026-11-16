"""Read dated ETF shares, unit NAV and cash turnover for the monitored symbols."""

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json

import httpx

from .news_cleaning import SHANGHAI
from .trading_calendar import is_trade_day

SSE_QUERY = 'https://query.sse.com.cn/commonQuery.do'
SSE_QUOTE = 'https://yunhq.sse.com.cn:32042/v1/sh1/dayk'
SZSE_REPORT = 'https://www.szse.cn/api/report/ShowReport/data'
SZSE_FUND = 'https://fund.szse.cn/api/report/ShowReport/data'
EASTMONEY_NAV = 'https://api.fund.eastmoney.com/f10/lsjz'
SINA_QUOTE = 'https://finance.sina.com.cn/realstock/company'
SSE_SHARES_SOURCE = '上交所 ETF 规模'
SZSE_SHARES_SOURCE = '深交所基金规模'
SSE_TURNOVER_SOURCE = '上交所云行情日线'
SZSE_TURNOVER_SOURCE = '深交所 ETF 行情'
SZSE_NAV_SOURCE = '深交所基金净值'
EASTMONEY_NAV_SOURCE = '天天基金历史单位净值'
SINA_TURNOVER_SOURCE = '新浪财经 ETF 日线'


def _json(client, url, params, referer):
    response = client.get(url, params=params, headers={'Referer': referer})
    response.raise_for_status()
    return response.json(parse_float=Decimal)


def _day(value):
    text = str(value)
    if len(text) == 8 and text.isascii() and text.isdigit():
        text = f'{text[:4]}-{text[4:6]}-{text[6:]}'
    return date.fromisoformat(text)


def _number(value, multiplier=1, *, allow_zero=False):
    if value is None:
        return None
    try:
        number = Decimal(str(value).replace(',', '').strip())
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0 or (not allow_zero and number == 0):
        return None
    return number * multiplier


def _sse_shares(client, day, codes):
    page = 1
    result = {}
    while True:
        payload = _json(client, SSE_QUERY, {
            'isPagination': 'true', 'pageHelp.pageSize': 1000,
            'pageHelp.pageNo': page, 'pageHelp.beginPage': page,
            'pageHelp.endPage': page, 'pageHelp.cacheSize': 1,
            'sqlId': 'COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L',
            'STAT_DATE': day.isoformat(),
        }, 'https://www.sse.com.cn/')
        for row in payload['result']:
            code = row['SEC_CODE']
            if code not in codes or _day(row['STAT_DATE']) != day:
                continue
            value = _number(row['TOT_VOL'], 10000)
            if value is not None:
                result[code, day] = value
        if codes <= {code for code, _ in result} or page >= int(payload['pageHelp']['pageCount']):
            return result
        page += 1


def _szse_report(client, url, catalog, code, **params):
    payload = _json(client, url, {
        'SHOWTYPE': 'JSON', 'CATALOGID': catalog, 'TABKEY': 'tab1',
        'txtDm': code, **params,
    }, 'https://fund.szse.cn/' if url == SZSE_FUND else 'https://www.szse.cn/')
    report = payload[0]
    if report.get('error'):
        raise ValueError('深交所查询未成功')
    return report


def _szse_shares(client, code, start, end):
    result = {}
    window_start = start
    while window_start <= end:
        # The exchange accepts at most six months in a single query.
        window_end = min(end, window_start + timedelta(days=179))
        page = 1
        while True:
            report = _szse_report(client, SZSE_FUND, 'fund_jjgm', code,
                                  txtStart=window_start.isoformat(), txtEnd=window_end.isoformat(),
                                  PAGENO=page)
            for row in report['data']:
                day = _day(row['size_date'])
                if row['fund_code'] != code or not window_start <= day <= window_end:
                    continue
                value = _number(row['current_size'], 10000)
                if value is not None:
                    result[code, day] = value
            if page >= int(report['metadata']['pagecount']):
                break
            page += 1
        window_start = window_end + timedelta(days=1)
    return result


def _dated_metrics(rows, *, code, dates, fields, source, multiplier=1, allow_zero=False):
    code_key, date_key, value_key = fields
    result = {}
    for row in rows:
        if code_key is not None and row[code_key] != code:
            continue
        day = _day(row[date_key])
        if day in dates:
            value = _number(row[value_key], multiplier, allow_zero=allow_zero)
            if value is not None:
                result[day] = (value, source)
    return result


def _eastmoney_nav(client, code, dates):
    result = {}
    page = 1
    while True:
        payload = _json(client, EASTMONEY_NAV, {
            'fundCode': code, 'pageIndex': page, 'pageSize': 100,
            'startDate': min(dates).isoformat(), 'endDate': max(dates).isoformat(),
        }, f'https://fundf10.eastmoney.com/jjjz_{code}.html')
        if payload['ErrCode'] != 0:
            raise ValueError('天天基金净值查询未成功')
        data = payload['Data']
        if data:
            result.update(_dated_metrics(data['LSJZList'], code=code, dates=dates,
                                         fields=(None, 'FSRQ', 'DWJZ'), source=EASTMONEY_NAV_SOURCE))
        if page * int(payload['PageSize']) >= int(payload['TotalCount']):
            return result
        page += 1


def _sina_turnover(client, code, dates):
    from akshare.stock.cons import hk_js_decode
    from py_mini_racer import MiniRacer

    symbol = f'sz{code}'
    response = client.get(f'{SINA_QUOTE}/{symbol}/hisdata_klc2/klc_kl.js',
                          headers={'Referer': 'https://finance.sina.com.cn/'})
    response.raise_for_status()
    variable, _, payload = response.text.partition('=')
    if variable.strip() != f'var KLC_K2_{symbol}':
        raise ValueError('新浪行情标的与请求不一致')
    # Only pass the encoded string to AkShare's bundled decoder.
    encoded, _ = json.JSONDecoder().raw_decode(payload.lstrip())
    if not isinstance(encoded, str):
        raise ValueError('新浪历史行情格式异常')
    decoder = MiniRacer()
    decoder.eval(hk_js_decode)
    result = {}
    for row in decoder.call('d', encoded):
        day = _day(row['date'][:10])
        if day in dates:
            amount = _number(row.get('amount'), allow_zero=True)
            if amount is not None:
                # The separate after-hours amount is part of exchange turnover.
                post_amount = _number(row.get('postAmt'), allow_zero=True)
                result[day] = (amount + (post_amount or 0), SINA_TURNOVER_SOURCE)
    return result


def _sse_turnover(client, code, dates):
    # The exchange quote API accepts offsets from the latest bar. Calendar-day
    # distance bounds the number of observations needed to reach the earliest date.
    days = max(2, (datetime.now(SHANGHAI).date() - min(dates)).days + 2)
    payload = _json(client, f'{SSE_QUOTE}/{code}', {
        'select': 'date,amount', 'begin': -days, 'end': -1,
    }, 'https://www.sse.com.cn/')
    if payload['code'] != code:
        return {}
    return _dated_metrics(payload['kline'], code=code, dates=dates, fields=(None, 0, 1),
                          source=SSE_TURNOVER_SOURCE, allow_zero=True)


def _fetch(errors, key, callback, *args):
    try:
        return callback(*args)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, ImportError) as exc:
        errors[key] = type(exc).__name__
        return {}


def _quotes(client, exchange, code, dates):
    errors = {}
    if exchange == 'SSE':
        nav = _fetch(errors, f'nav:{code}', _eastmoney_nav, client, code, dates)
        turnover = _fetch(errors, f'turnover:{code}', _sse_turnover, client, code, dates)
        return nav, turnover, errors

    def recent(url, catalog, fields, source, multiplier=1):
        report = _szse_report(client, url, catalog, code, PAGENO=1)
        return _dated_metrics(report['data'], code=code, dates=dates, fields=fields, source=source,
                              multiplier=multiplier, allow_zero=multiplier != 1)

    nav = _fetch(errors, f'nav:SZSE:{code}', recent, SZSE_FUND, 'fund_jjjz',
                 ('fund_code', 'nav_date', 'nav_per_share'), SZSE_NAV_SOURCE)
    turnover = _fetch(errors, f'turnover:SZSE:{code}', recent, SZSE_REPORT, '1815_fund_child',
                      ('zqdm', 'jyrq', 'cjje'), SZSE_TURNOVER_SOURCE, 10000)
    if missing := dates - nav.keys():
        nav.update(_fetch(errors, f'nav:Eastmoney:{code}', _eastmoney_nav, client, code, missing))
    if missing := dates - turnover.keys():
        turnover.update(_fetch(errors, f'turnover:Sina:{code}', _sina_turnover,
                               client, code, missing))
    return nav, turnover, errors


def collect_etf_rows(symbols: Mapping[str, str], start: date, end: date, *,
                     sessions: Sequence[date] | None = None, shares_only: bool = False) -> dict:
    """Return official share rows joined to optional quotes by exchange, code and date.

    Shares and turnover are Decimal in individual units and yuan, respectively.
    The caller may provide date objects from a full verified market calendar.
    """
    if start > end:
        raise ValueError('ETF 采集开始日期晚于结束日期')
    for code, exchange in symbols.items():
        if exchange not in {'SSE', 'SZSE'} or len(code) != 6 or not code.isascii() or not code.isdigit():
            raise ValueError('ETF 采集标的代码或交易所无效')
    dates = {day for day in sessions if start <= day <= end} if sessions is not None else {
        day for offset in range((end - start).days + 1)
        if (day := start + timedelta(days=offset)).weekday() < 5 and is_trade_day(day) is not False
    }
    errors, shares, quotes = {}, {}, {}
    sse_codes = {code for code, exchange in symbols.items() if exchange == 'SSE'}
    with httpx.Client(timeout=10, headers={'User-Agent': 'Mozilla/5.0'}) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = {
                f'shares:SSE:{day.isoformat()}': pool.submit(_sse_shares, client, day, sse_codes)
                for day in sorted(dates) if sse_codes
            }
            jobs.update({
                f'shares:SZSE:{code}': pool.submit(_szse_shares, client, code, start, end)
                for code, exchange in symbols.items() if exchange == 'SZSE' and dates
            })
            for key, future in jobs.items():
                shares.update(_fetch(errors, key, future.result))
            dates_by_code = {}
            for code, day in shares:
                if day in dates:
                    dates_by_code.setdefault(code, set()).add(day)
            if not shares_only:
                for code, result in zip(dates_by_code, pool.map(
                    lambda code: _quotes(client, symbols[code], code, dates_by_code[code]), dates_by_code
                )):
                    nav, turnover, failures = result
                    quotes[code] = nav, turnover
                    errors.update(failures)

    rows = []
    for code, day in sorted(shares, key=lambda key: (key[1], key[0])):
        if day not in dates:
            continue
        exchange = symbols[code]
        nav, turnover = quotes.get(code, ({}, {}))
        unit_nav, nav_source = nav.get(day, (None, None))
        turnover_cny, turnover_source = turnover.get(day, (None, None))
        rows.append({
            'code': code, 'exchange': exchange, 'trade_date': day.isoformat(),
            'total_shares': shares[code, day], 'unit_nav': unit_nav, 'turnover_cny': turnover_cny,
            'shares_source': SSE_SHARES_SOURCE if exchange == 'SSE' else SZSE_SHARES_SOURCE,
            'nav_source': nav_source, 'turnover_source': turnover_source,
        })
    return {'rows': rows, 'errors': errors}
