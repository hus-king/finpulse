"""Dated public market preview for convertible-bond research."""

from datetime import date, datetime, time
import json
import math
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .news_cleaning import SHANGHAI
from .trading_calendar import is_trade_day, previous_trade_day


QUOTE_URL = 'https://16.push2.eastmoney.com/api/qt/clist/get'
TERMS_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
QUOTE_FIELDS = 'f2,f6,f12,f13,f14,f124,f229,f232,f234,f235'
TERM_FIELDS = (
    'SECUCODE,PAR_VALUE,RATING,EXPIRE_DATE,DELIST_DATE,'
    'REDEEM_TYPE,NOTICE_DATE_SH,EXECUTE_START_DATESH,TRANSFER_END_DATE'
)
SOURCES = [
    '东方财富行情中心 / 可转债比价与报价时间',
    '东方财富数据中心 / 可转债基本资料',
]
MAX_RESPONSE = 4_000_000


def _get_json(url, params):
    request = Request(
        url + '?' + urlencode(params),
        headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/'},
    )
    try:
        with urlopen(request, timeout=8) as response:
            body = response.read(MAX_RESPONSE + 1)
        if len(body) > MAX_RESPONSE:
            raise ValueError
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError
        return payload
    except (OSError, ValueError):
        raise ValueError('东方财富可转债数据暂不可用') from None


def _pages(fetch, url, params, page_key, result_key, rows_key, count_key):
    rows = []
    expected = None
    for page in range(1, 9):
        result = fetch(url, {**params, page_key: page}).get(result_key)
        if not isinstance(result, dict):
            break
        total = result.get(count_key)
        batch = result.get(rows_key)
        if expected is None:
            expected = total
        if (
            not isinstance(total, int) or total != expected or total <= 0
            or not isinstance(batch, list) or not batch
            or not all(isinstance(item, dict) for item in batch)
        ):
            break
        rows.extend(batch)
        if len(rows) == expected:
            return rows
        if len(rows) > expected:
            break
    raise ValueError('东方财富可转债列表不完整，请稍后重试')


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _timestamp(value):
    try:
        return datetime.fromtimestamp(int(value), SHANGHAI)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _risk(terms, as_of):
    announcement = _date(terms.get('NOTICE_DATE_SH'))
    redemption = _date(terms.get('EXECUTE_START_DATESH'))
    delisting = _date(terms.get('DELIST_DATE'))
    if str(terms.get('REDEEM_TYPE')) == '2' and announcement and announcement <= as_of and redemption:
        return 'call', f'行情源记录实施强赎，兑付日 {redemption}'
    if delisting:
        return 'exit', f'行情源记录摘牌日 {delisting}'
    return 'unknown', '尚未逐券核验强赎、信用与最后交易日公告'


def _normalize_quote(quote, terms, as_of, now, close_confirmed):
    observed = _timestamp(quote.get('f124'))
    valid_quote = (
        observed is not None and observed.date() == as_of and observed <= now
        and (not close_confirmed or observed.time() >= time(15))
    )
    price = _number(quote.get('f2')) if valid_quote else None
    stock_price = _number(quote.get('f229')) if valid_quote else None
    conversion_price = _number(quote.get('f235')) if valid_quote else None
    par = _number(terms.get('PAR_VALUE'))
    maturity = _date(terms.get('EXPIRE_DATE'))
    conversion_end = _date(terms.get('TRANSFER_END_DATE'))
    risk, risk_reason = _risk(terms, as_of)
    return {
        'name': str(quote.get('f14') or ''),
        'stock_code': str(quote.get('f232') or ''),
        'stock_name': str(quote.get('f234') or ''),
        'industry': None,
        'price': price,
        'quote_at': observed.isoformat() if valid_quote else None,
        'conversion_value': par * stock_price / conversion_price if par and stock_price and conversion_price else None,
        'stock_price': stock_price,
        'conversion_price': conversion_price,
        'rating': terms.get('RATING') or None,
        'maturity_date': maturity.isoformat() if maturity else None,
        'turnover_20d': None,
        'stock_return_20d': None,
        'industry_return_20d': None,
        'risk': risk,
        'risk_reason': risk_reason,
        'tradable': bool(price and _number(quote.get('f6'))),
        'last_conversion_date': conversion_end.isoformat() if conversion_end else None,
    }


def collect_snapshot(*, now=None, fetch=_get_json):
    """Return a dated preview. Missing risk and history inputs remain unknown."""
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    today, clock = now.date(), now.time()
    trading = is_trade_day(today)
    if trading is None:
        raise ValueError('沪深交易日历待更新，无法验证报价日期')
    as_of = today if trading and clock >= time(9, 30) else previous_trade_day(today)
    if as_of is None:
        raise ValueError('无法确认最近交易日')
    close_confirmed = as_of < today or clock >= time(15, 10)

    quotes = _pages(fetch, QUOTE_URL, {
        'pz': 100, 'po': 1, 'np': 1, 'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        'fltt': 2, 'invt': 2, 'fid': 'f12', 'fs': 'b:MK0354', 'fields': QUOTE_FIELDS,
    }, 'pn', 'data', 'diff', 'total')
    terms = _pages(fetch, TERMS_URL, {
        'pageSize': 500, 'reportName': 'RPT_BOND_CB_LIST', 'columns': TERM_FIELDS,
        'sortColumns': 'PUBLIC_START_DATE,SECURITY_CODE', 'sortTypes': '-1,1',
        'source': 'WEB', 'client': 'WEB',
    }, 'pageNumber', 'result', 'data', 'count')
    terms_by_code = {row.get('SECUCODE'): row for row in terms}

    rows = {}
    for quote in quotes:
        bond_code = str(quote.get('f12') or '')
        exchange = {0: 'SZ', 1: 'SH'}.get(quote.get('f13'))
        if (
            exchange is None or len(bond_code) != 6 or not bond_code.isdigit()
            or not bond_code.startswith(('110', '111', '113', '118', '123', '127', '128'))
        ):
            continue
        code = f'{bond_code}.{exchange}'
        if code in rows:
            raise ValueError('东方财富可转债报价出现重复代码，请稍后重试')
        rows[code] = {
            **_normalize_quote(quote, terms_by_code.get(code, {}), as_of, now, close_confirmed),
            'code': code,
            'source_url': f'https://data.eastmoney.com/kzz/detail/{bond_code}.html',
        }

    dated_count = sum(row['price'] is not None for row in rows.values())
    if not dated_count:
        kind = '收盘报价' if close_confirmed else '盘中报价'
        raise ValueError(f'未取得 {as_of} 的可核验{kind}')
    warnings = [
        '当前仅接入行情与基本条款。近20日成交额、正股与行业收益以及完整风险公告待补齐。',
    ]
    if not close_confirmed:
        warnings.append('当前为盘中报价快照，不用于生成周度收盘信号。')
    if dated_count < len(rows):
        warnings.append(f'{len(rows) - dated_count} 只转债缺有效报价或报价日期未对齐。')
    return {
        'as_of': as_of.isoformat(),
        'collected_at': now.isoformat(),
        'close_confirmed': close_confirmed,
        'sources': SOURCES.copy(),
        'warnings': warnings,
        'rows': list(rows.values()),
    }
