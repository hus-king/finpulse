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
