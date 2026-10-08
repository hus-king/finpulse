"""Bounded, durable industry metadata/search caches shared across companies."""
import asyncio
import hashlib
import time
from datetime import datetime

from . import providers
from .industry import PROFILE_NOTE, PROFILE_VERSION, valid_industry
from .news_cleaning import SHANGHAI


class IndustryNewsService:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.tasks = {}
        self.capacity = asyncio.Semaphore(2)
        self.closed = False

    async def _shared(self, namespace, key, fetcher, ttl):
        if self.closed:
            raise RuntimeError('Industry service is closed')
        task_key = (namespace, key)
        if task_key not in self.tasks:
            if len(self.tasks) >= 40:
                return {'error': '行业请求繁忙，请稍后重试', 'status': 'unavailable'}
            task = asyncio.create_task(self._refresh(namespace, key, fetcher, ttl))
            self.tasks[task_key] = task
            def done(finished):
                self.tasks.pop(task_key, None)
                if not finished.cancelled():
                    finished.exception()
            task.add_done_callback(done)
        return await asyncio.shield(self.tasks[task_key])

    def _fresh(self, record, ttl):
        return bool(record.get('data') is not None and not record.get('error') and self.clock() - record.get('fetched_at', 0) < ttl or self.clock() < record.get('retry_at', 0))

    async def _refresh(self, namespace, key, fetcher, ttl):
        token = None
        # MySQL research_records.record_key is VARCHAR(64); the search key
        # already occupies 64 characters. Hash namespace + key as one identity
        # so claim/release share a bounded, collision-resistant lease key.
        lease_key = hashlib.sha256((namespace + ':' + key).encode()).hexdigest()
        try:
            record = await asyncio.to_thread(self.store.get, namespace, key, default={})
            if self._fresh(record, ttl):
                return record
            async with self.capacity:
                # Other processes share the same DB lease and durable result.
                deadline = asyncio.get_running_loop().time() + 125
                while True:
                    record = await asyncio.to_thread(self.store.get, namespace, key, default={})
                    if self._fresh(record, ttl):
                        return record
                    token = await asyncio.to_thread(self.store.claim, lease_key, 120)
                    if token:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        return {**record, 'status': 'stale' if record.get('data') is not None else 'unavailable', 'error': '行业采集等待超时，保留上次结果'}
                    await asyncio.sleep(.1)
                # A prior owner may have completed between read and claim.
                record = await asyncio.to_thread(self.store.get, namespace, key, default={})
                if self._fresh(record, ttl):
                    return record
                try:
                    data = await asyncio.wait_for(fetcher(), 95)
                    record = {'data': data, 'fetched_at': self.clock(), 'attempted_at': self.clock(), 'retry_at': 0, 'error': None, 'status': 'ok'}
                except Exception as exc:
                    record = {**record, 'attempted_at': self.clock(), 'retry_at': self.clock() + 300,
                              'error': str(exc) if isinstance(exc, providers.ProviderError) else '行业数据源不可用，请稍后重试',
                              'status': 'stale' if record.get('data') is not None else 'unavailable'}
                await asyncio.to_thread(self.store.put, namespace, key, record)
                return record
        finally:
            if token:
                await asyncio.to_thread(self.store.release, lease_key, token)

    async def profile(self, stock):
        async def fetch():
            profile = await providers.stock_profile(stock)
            if profile.get('code') != stock['code'] or not valid_industry(profile.get('industry')):
                raise providers.ProviderError('行业资料身份或行业字段无法核验')
            return profile
        record = await self._shared('stock_industry', stock['code'], fetch, 7 * 86400)
        data = record.get('data') or {}
        return {**data, 'code': stock['code'], 'industry': data.get('industry'), 'status': record.get('status', 'unavailable'),
                'error': record.get('error'), 'version': PROFILE_VERSION,
                'fetched_at': datetime.fromtimestamp(record['fetched_at'], SHANGHAI).isoformat() if record.get('fetched_at') else None,
                'note': PROFILE_NOTE}

    async def search(self, topic, start, end):
        key = hashlib.sha256('\n'.join([PROFILE_VERSION, topic['industry'], topic['query'], start, end]).encode()).hexdigest()
        async def fetch():
            response = await providers.search_industry_news(topic['query'], start, end)
            if not isinstance(response.get('results'), list):
                raise providers.ProviderError('行业新闻响应格式异常')
            return {**response, 'results': response['results'][:8]}
        record = await self._shared('industry_search', key, fetch, 1800)
        return {'response': record.get('data') or {'results': []}, 'status': record.get('status', 'unavailable'),
                'error': record.get('error'), 'fetched_at': record.get('fetched_at'), 'topic': topic}

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
