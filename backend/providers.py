"""Live data adapters. Failed providers never return demo data."""
import asyncio
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .news_cleaning import SHANGHAI

ROOT = Path(__file__).resolve().parents[1]


def read_config():
    try:
        return json.loads((ROOT / 'config.local.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


class ProviderError(Exception):
    pass


async def tavily(endpoint, payload):
    config = read_config()
    key = config.get('tavily_api_key', '')
    base = config.get('tavily_base_url', 'https://api.tavily.com').rstrip('/')
    parsed = urlsplit(base)
    if not key:
        raise ProviderError('Tavily 未配置密钥')
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderError('Tavily 地址配置不合法')
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(55, connect=10)) as client:
            response = await client.post(base + '/' + endpoint, headers={'Authorization': 'Bearer ' + key}, json=payload)
        if not response.is_success:
            raise ProviderError(f'Tavily 请求失败（HTTP {response.status_code}）')
        body = response.json()
        if not isinstance(body.get('results'), list):
            raise ProviderError('Tavily 响应格式异常')
        return body
    except (httpx.RequestError, ValueError):
        raise ProviderError('Tavily 网络请求失败或响应无法解析') from None


async def search_news(stock, start, end):
    return await tavily('search', {'query': f'{stock["name"]} {stock["code"]} 公司 公告 新闻', 'topic': 'news', 'search_depth': 'advanced', 'max_results': 12, 'start_date': start, 'end_date': end, 'include_raw_content': False, 'include_answer': False, 'include_usage': True})


async def search_industry_news(query, start, end):
    return await tavily('search', {'query': query, 'topic': 'news', 'search_depth': 'advanced', 'max_results': 8,
        'start_date': start, 'end_date': end, 'include_raw_content': False, 'include_answer': False, 'include_usage': True})


async def stock_profile(stock):
    from .industry import valid_industry
    try:
        result = await akshare_worker('profile', stock['code'], '', '')
        rows = {row.get('item'): row.get('value') for row in result.get('rows', [])}
        if str(rows.get('股票代码', '')).strip() != stock['code'] or not valid_industry(rows.get('行业')):
            raise ProviderError('个股资料未返回对应股票的有效行业')
        return {'code': stock['code'], 'name': rows.get('股票简称') or stock['name'],
                'industry': rows['行业'].strip(), 'source': 'AkShare / 东方财富个股资料'}
    except ProviderError:
        # Quote hosts may reject a server network while F10 remains available.
        # Read the provider classification; never infer it from the company name.
        exchange = 'SH' if stock['code'].startswith('6') else 'SZ'
        secucode = stock['code'] + '.' + exchange
        params = {'reportName': 'RPT_F10_ORG_BASICINFO',
                  'columns': 'SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME_1LEVEL,BOARD_NAME_2LEVEL,BOARD_NAME_3LEVEL',
                  'filter': f'(SECUCODE="{secucode}")', 'pageNumber': 1, 'pageSize': 1, 'source': 'HSF10', 'client': 'PC'}
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.get('https://datacenter.eastmoney.com/securities/api/data/v1/get', params=params)
                response.raise_for_status()
                payload = response.json()
            records = payload['result']['data']
            if payload.get('success') is not True or not isinstance(records, list) or len(records) != 1:
                raise ValueError('Invalid F10 records')
            row = records[0]
            if not isinstance(row, dict):
                raise ValueError('Invalid F10 row')
            industry = next((row.get(key) for key in ('BOARD_NAME_2LEVEL', 'BOARD_NAME_3LEVEL', 'BOARD_NAME_1LEVEL')
                             if valid_industry(row.get(key))), None)
            if row.get('SECUCODE') != secucode or row.get('SECURITY_CODE') != stock['code'] or not industry:
                raise ValueError('Invalid F10 identity or industry')
            return {'code': stock['code'], 'name': row.get('SECURITY_NAME_ABBR') or stock['name'],
                    'industry': industry.strip(), 'source': '东方财富 F10 公司资料（东方财富行业分类）'}
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise ProviderError('个股行业资料主源及 F10 备用源暂不可用') from None


async def akshare_worker(kind, code, start, end, retry=True):
    # Some AkShare methods do not expose timeouts. A disposable process bounds
    # the whole request without patching global requests state or leaking threads.
    process = await asyncio.create_subprocess_exec(sys.executable, '-X', 'utf8', '-m', 'backend.akshare_worker', kind, code, start, end, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=ROOT)
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout=45)
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.communicate()
        if asyncio.current_task().cancelling():
            raise
        raise ProviderError('AkShare 数据请求超时') from None
    try:
        result = json.loads(out.decode('utf-8-sig').strip())
        if result.get('error'):
            raise ProviderError('AkShare 数据源不可用（' + result['error'] + '）')
        return result
    except (ValueError, UnicodeError):
        # Private, sanitized diagnostics: no raw pages, process output or tokens.
        import logging
        logging.getLogger(__name__).warning('AkShare worker failed: kind=%s returncode=%s stdout_bytes=%s stderr_bytes=%s', kind, process.returncode, len(out), len(_))
        if retry and not out and process.returncode:
            return await akshare_worker(kind, code, start, end, retry=False)
        raise ProviderError('AkShare 数据响应格式异常') from None


async def akshare_news(stock, start, end):
    result = await akshare_worker('news', stock['code'], start, end)
    return {'results': [{'title': row.get('新闻标题', ''), 'content': row.get('新闻内容', ''), 'url': row.get('新闻链接', ''), 'published_date': row.get('发布时间'), 'provider': 'AkShare / 东方财富', 'source_name': row.get('文章来源', '')} for row in result['rows']]}


async def daily_market(stock, end):
    start = (date.fromisoformat(end) - timedelta(days=220)).isoformat()
    result = await akshare_worker('history', stock['code'], start, end)
    candles = []
    for row in result['rows']:
        try:
            bar = {key: float(row[key]) for key in ('open', 'close', 'high', 'low', 'volume')}
            bar['date'] = str(row['date'])[:10]
            if not start <= bar['date'] <= end or not all(math.isfinite(bar[key]) for key in ('open', 'close', 'high', 'low', 'volume')):
                continue
            if min(bar['open'], bar['close'], bar['low']) <= 0 or bar['low'] > min(bar['open'], bar['close']) or bar['high'] < max(bar['open'], bar['close']) or bar['volume'] < 0:
                continue
            candles.append(bar)
        except (ValueError, KeyError, TypeError):
            continue
    candles = sorted({bar['date']: bar for bar in candles}.values(), key=lambda bar: bar['date'])
    if not candles:
        raise ProviderError('行情源没有返回有效日线')
    last = candles[-1]
    previous = candles[-2] if len(candles) > 1 else None
    return {'status': 'ok', 'source': 'AkShare / 腾讯日线', 'is_realtime': False, 'adjustment': 'none', 'volume_unit': 'shares', 'as_of_date': last['date'], 'collected_at': datetime.now(SHANGHAI).isoformat(), 'price': last['close'], 'change': round((last['close'] / previous['close'] - 1) * 100, 2) if previous else None, 'candles': candles}
