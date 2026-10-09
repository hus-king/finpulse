"""Durable rolling search windows, independent from the LLM judgment cache."""
import asyncio
import hashlib
import json
import time
from copy import deepcopy
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from . import providers
from .news_cleaning import SHANGHAI, metadata_date, normalize_url


SEARCH_VERSION = 'rolling-news-v2'
REUSE_SECONDS = 3600


def search_key(identity, days):
    # Recipes are versioned; endpoint changes invalidate results, key rotation does not.
    endpoint = providers.read_config().get('tavily_base_url', 'https://api.tavily.com')
    return hashlib.sha256(json.dumps([SEARCH_VERSION, endpoint, identity, days],
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def published_time(row):
    value = row.get('published_date') or row.get('published_at') or row.get('time')
    if not isinstance(value, str) or len(value.strip()) <= 10:
        return None  # A date alone must not be invented as midnight.
    try:
        result = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        try:
            result = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
    return result.replace(tzinfo=SHANGHAI) if result.tzinfo is None else result.astimezone(SHANGHAI)


def in_window(row, start, end):
    value = row.get('published_date') or row.get('published_at') or row.get('time')
    day = metadata_date(value)
    # Undated candidates remain subject to the existing正文日期核验 cleaning step.
    return not day or start <= str(day)[:10] <= end


def merged_results(previous, incoming, start, end):
    rows = {}
    for row in [*previous, *incoming]:
        url = normalize_url(row.get('url', ''))
        if url and in_window(row, start, end):
            # Preserve old full text when an overlapping result only has a snippet.
            old = rows.get(url, {})
            rows[url] = {**old, **row, 'url': url}
            if not row.get('raw_content') and old.get('raw_content'):
                rows[url]['raw_content'] = old['raw_content']
    return list(rows.values())


def extract_key(url, material=None):
    evidence = {field: (material or {}).get(field) for field in ('title', 'content', 'published_date', 'raw_content')}
    return hashlib.sha256(json.dumps([url, evidence], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class NewsSearchService:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.reuse_enabled = True
        self.capacity = asyncio.Semaphore(2)

    def reuse_recent_result(self, record, now, start, end):
        """Optional shortcut. Removing this call leaves incremental_search intact."""
        if not self.reuse_enabled or not record or record.get('error') or not record.get('cursor'):
            return None
        if not 0 <= now - record['cursor'] < REUSE_SECONDS:
            return None
        response = deepcopy(record['response'])
        response['results'] = merged_results(response['results'], [], start, end)
        response['search_cache'] = {**record['audit'], 'mode': 'reused',
                                    'last_search_at': datetime.fromtimestamp(record['cursor'], SHANGHAI).isoformat()}
        return response

    async def incremental_search(self, record, fetcher, now, start, end):
        """Fetch the uncovered interval, merge old in-range candidates, never reset on a read."""
        cursor = record.get('cursor')
        since = max(datetime.fromisoformat(start).replace(tzinfo=SHANGHAI).timestamp(), cursor or 0)
        query_start = datetime.fromtimestamp(since, SHANGHAI).date().isoformat()
        response = await fetcher(query_start, end)
        if not isinstance(response.get('results'), list):
            raise providers.ProviderError('新闻搜索响应格式异常')
        incoming = []
        for row in response['results']:
            timestamp = published_time(row)
            if in_window(row, query_start, end) and (timestamp is None or since <= timestamp.timestamp() <= now):
                incoming.append(row)
        old = record.get('response', {}).get('results', [])
        audit = {'mode': 'incremental' if cursor else 'full',
                 'interval': [datetime.fromtimestamp(since, SHANGHAI).isoformat(),
                              datetime.fromtimestamp(now, SHANGHAI).isoformat()],
                 'provider_date_range': [query_start, end], 'window': [start, end],
                 'date_precision': 'day_query_timestamp_filter', 'incoming': len(incoming)}
        response = {**response, 'results': merged_results(old, incoming, start, end)}
        # A partial company query must retry from the last fully successful cursor.
        error = bool(response.get('warnings'))
        next_cursor = cursor if error else now
        saved = {'response': response, 'cursor': next_cursor, 'error': error, 'audit': audit}
        response = deepcopy(response)
        response['search_cache'] = {**audit, 'last_search_at':
            datetime.fromtimestamp(next_cursor, SHANGHAI).isoformat() if next_cursor else None}
        return saved, response

    async def search(self, identity, days, start, end, fetcher):
        key = search_key(identity, days)
        lease = hashlib.sha256(('news_search:' + key).encode()).hexdigest()
        record = await asyncio.to_thread(self.store.get, 'news_search', key, default={})
        cached = self.reuse_recent_result(record, self.clock(), start, end)
        if cached is not None:
            return cached
        token = None
        async with self.capacity:
            deadline = asyncio.get_running_loop().time() + 125
            try:
                while token is None:
                    token = await asyncio.to_thread(self.store.claim, lease, 120)
                    if token is None:
                        if asyncio.get_running_loop().time() >= deadline:
                            raise providers.ProviderError('新闻搜索等待超时，请稍后重试')
                        await asyncio.sleep(.1)
                record = await asyncio.to_thread(self.store.get, 'news_search', key, default={})
                cached = self.reuse_recent_result(record, self.clock(), start, end)
                if cached is not None:
                    return cached
                # Cursor uses request START time, so publication during a slow request is not skipped.
                try:
                    saved, response = await asyncio.wait_for(
                        self.incremental_search(record, fetcher, self.clock(), start, end), 95)
                except (providers.ProviderError, asyncio.TimeoutError) as exc:
                    if not record.get('response'):
                        raise
                    await asyncio.to_thread(self.store.put, 'news_search', key, {**record, 'error': True})
                    response = deepcopy(record['response'])
                    response['results'] = merged_results(response['results'], [], start, end)
                    detail = str(exc) if isinstance(exc, providers.ProviderError) else '新闻源请求超时'
                    response['warnings'] = [detail + '；保留区间内的上次结果，下次重试未完成的时间段']
                    response['search_cache'] = {**record['audit'], 'mode': 'stale', 'last_search_at':
                        datetime.fromtimestamp(record['cursor'], SHANGHAI).isoformat() if record.get('cursor') else None}
                    return response
                await asyncio.to_thread(self.store.put, 'news_search', key, saved)
                return response
            finally:
                if token:
                    await asyncio.to_thread(self.store.release, lease, token)

    async def load_extracts(self, materials):
        keys = {url: extract_key(url, material) for url, material in materials.items()}
        records = await asyncio.to_thread(self.store.get_many, 'news_extract', list(keys.values()))
        return {url: records[key]['raw_content'] for url, key in keys.items() if records.get(key, {}).get('raw_content')}

    async def extract(self, urls, materials=None, cache_only=False):
        """Keep successful text for identical evidence; retry failures after an hour."""
        keys = {url: extract_key(url, (materials or {}).get(url)) for url in urls}
        records = await asyncio.to_thread(self.store.get_many, 'news_extract', list(keys.values()))
        result, missing, failed = [], [], []
        for url, key in keys.items():
            record = records.get(key, {})
            if record and (record.get('raw_content') or 0 <= self.clock() - record.get('fetched_at', 0) < REUSE_SECONDS):
                if record.get('raw_content'):
                    result.append({'url': url, 'raw_content': record['raw_content']})
                elif record.get('failed'):
                    failed.append({'url': url})
            else:
                missing.append(url)
        if missing and not cache_only:
            response = await providers.tavily('extract', {'urls': missing, 'extract_depth': 'basic', 'format': 'text'})
            failed.extend(response.get('failed_results', []))
            returned = set()
            for row in response['results']:
                url = normalize_url(row.get('url', ''))
                if url in keys and row.get('raw_content'):
                    await asyncio.to_thread(self.store.put, 'news_extract', keys[url],
                                            {'raw_content': row['raw_content'], 'fetched_at': self.clock()})
                    result.append(row)
                    returned.add(url)
            for url in set(missing) - returned:
                await asyncio.to_thread(self.store.put, 'news_extract', keys[url],
                                        {'raw_content': None, 'failed': any(row.get('url') == url for row in failed),
                                         'fetched_at': self.clock()})
        return {'results': result, 'failed_results': failed}
