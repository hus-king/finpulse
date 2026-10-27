"""Account-private, persistent one-click news/watchlist matching jobs."""
import asyncio
from datetime import datetime
import logging
import time
import uuid
from fastapi import HTTPException
from .hot_news_board import digest, public_board
from .news_cleaning import SHANGHAI

NAMESPACE = 'hot_news_matches'
VERSION = 'news-watchlist-match-v1'
LEASE_TTL = 180
logger = logging.getLogger(__name__)


class HotNewsMatchService:
    def __init__(self, impacts, clock=time.time):
        self.impacts, self.board, self.store, self.clock = impacts, impacts.board, impacts.store, clock
        self.capacity = asyncio.Semaphore(2)
        self.tasks = {}
        self.submissions = asyncio.Lock()
        self.heartbeat_seconds = 30

    def lease_key(self, owner):
        return digest([VERSION, owner])

    def snapshot(self, owner):
        record = self.store.get(NAMESPACE, 'latest', owner)
        if not record: return {'job': None}
        result = {key: value for key, value in record.items() if not key.startswith('_')}
        if result['status'] in ('queued', 'running'):
            lease = self.store.get('lease', self.lease_key(owner), default={})
            if lease.get('token') != record.get('_token') or lease.get('expires', 0) <= self.clock():
                result.update(status='interrupted', message='匹配任务已中断，可重新分析；已完成判断会复用。')
        stocks = self.impacts.stocks(owner)
        codes = {row['code'] for row in stocks}
        result['watchlist_changed'] = sorted(codes) != sorted(record['_codes'])
        result['results'] = [{**row, 'items': [stock for stock in row['items'] if stock['code'] in codes]} for row in result['results']]
        groups = {}
        for row in result['results']:
            for stock in row['items']:
                analysis = stock.get('analysis')
                if not analysis or analysis['relation'] not in ('direct', 'indirect'): continue
                group = groups.setdefault(stock['code'], {'code': stock['code'], 'name': stock['name'], 'news': []})
                group['news'].append({'topic_id': row['topic_id'], 'title': row['title'], 'sources': row['sources'],
                                      'analysis': analysis, 'model': stock.get('model'), 'analyzed_at': stock.get('analyzed_at')})
        result['stocks'] = sorted(groups.values(), key=lambda stock: (-len(stock['news']), stock['code']))
        result['counts']['related'] = sum(len(row['news']) for row in result['stocks'])
        result['counts']['related_stocks'] = len(result['stocks'])
        return {'job': result}

    async def save(self, owner, record):
        record['updated_at'] = datetime.fromtimestamp(self.clock(), SHANGHAI).isoformat()
        def write():
            lease = self.store.get('lease', self.lease_key(owner), default={})
            if lease.get('token') != record['_token']:
                latest = self.store.get(NAMESPACE, 'latest', owner, default={})
                if lease.get('token') or latest.get('id') != record['id']: return
            self.store.put(NAMESPACE, 'latest', record, owner)
        await asyncio.to_thread(write)

    async def start(self, topic_ids, owner):
        async with self.submissions:
            return await self._start(topic_ids, owner)

    async def _start(self, topic_ids, owner):
        existing = await asyncio.to_thread(self.snapshot, owner)
        if existing['job'] and existing['job']['status'] in ('queued', 'running'): return existing
        if len(self.tasks) >= 8: raise HTTPException(429, '匹配队列已满，请稍后重试。')
        feed = await asyncio.to_thread(self.board.feed)
        visible = {row['id'] for row in public_board(feed)['items']}
        topics = {row['id']: row for row in feed['items'] if row['id'] in visible}
        ids = list(dict.fromkeys(topic_ids))
        if not ids or any(topic_id not in topics for topic_id in ids):
            raise HTTPException(422, '请选择当前榜单中保留的新闻；榜单已变化时请重新读取。')
        stocks = await asyncio.to_thread(self.impacts.stocks, owner)
        if not stocks: raise HTTPException(422, '请先添加自选股，再一键分析。')
        token = await asyncio.to_thread(self.store.claim, self.lease_key(owner), LEASE_TTL)
        if not token:
            existing = await asyncio.to_thread(self.snapshot, owner)
            if existing['job'] and existing['job']['status'] in ('queued', 'running'): return existing
            # The other instance may have acquired the lease but not saved its job yet.
            return {'job': {'id': 'starting', 'status': 'queued', 'stage': 'queued', 'message': '匹配任务正在启动，请稍后读取。',
                'started_at': datetime.fromtimestamp(self.clock(), SHANGHAI).isoformat(), 'topic_ids': ids,
                'topic_total': len(ids), 'stock_total': len(stocks), 'topics_completed': 0, 'watchlist_changed': False,
                'counts': {'total': len(ids) * len(stocks), 'completed': 0, 'failed': 0, 'uncertain': 0, 'related': 0, 'related_stocks': 0},
                'results': [], 'stocks': [], 'errors': []}}
        record = {'id': uuid.uuid4().hex, 'status': 'queued', 'stage': 'queued',
                  'message': '匹配任务已提交，正在排队。', 'started_at': datetime.fromtimestamp(self.clock(), SHANGHAI).isoformat(),
                  'topic_ids': ids, 'topic_total': len(ids), 'stock_total': len(stocks), 'topics_completed': 0,
                  'counts': {'total': len(ids) * len(stocks), 'completed': 0, 'failed': 0, 'uncertain': 0, 'related': 0, 'related_stocks': 0},
                  'results': [], 'errors': [], '_token': token, '_codes': [stock['code'] for stock in stocks]}
        try:
            await self.save(owner, record)
        except BaseException:
            await asyncio.to_thread(self.store.release, self.lease_key(owner), token)
            raise
        captured = [topics[topic_id] for topic_id in ids]
        task = asyncio.create_task(self.run(owner, record, captured, stocks))
        self.tasks[owner] = task
        def done(finished):
            if self.tasks.get(owner) is finished: self.tasks.pop(owner, None)
            if not finished.cancelled(): finished.exception()
        task.add_done_callback(done)
        return await asyncio.to_thread(self.snapshot, owner)

    async def run(self, owner, record, topics, stocks):
        parent = asyncio.current_task()
        async def heartbeat():
            while True:
                await asyncio.sleep(self.heartbeat_seconds)
                try:
                    renewed = await asyncio.to_thread(self.store.renew, self.lease_key(owner), record['_token'], LEASE_TTL)
                except Exception:
                    renewed = False
                if not renewed:
                    parent.cancel(); return
        pulse = asyncio.create_task(heartbeat())
        try:
            async with self.capacity:
                record.update(status='running', stage='profiles', message='正在准备我的自选股主营资料。')
                await self.save(owner, record)
                await asyncio.gather(*(self.impacts.research.industry_news.business(stock) for stock in stocks), return_exceptions=True)
                record.update(stage='matching', message='正在逐条匹配新闻与自选股。')
                await self.save(owner, record)
                for topic in topics:
                    current = await asyncio.to_thread(self.impacts.stocks, owner)
                    if sorted(stock['code'] for stock in current) != sorted(record['_codes']):
                        record.update(status='interrupted', message='自选股已变化，请重新分析；当前结果只展示仍在自选中的股票。')
                        break
                    try:
                        result = await self.impacts.analyze(topic['id'], owner, _topic=topic, _codes=record['_codes'], _refresh_business=False, _wait=True)
                        preview = {**result, 'items': [{key: value for key, value in stock.items() if key != 'business'} for stock in result['items']]}
                        record['results'].append(preview)
                        record['counts']['completed'] += result['counts']['completed']
                        record['counts']['failed'] += result['counts']['failed']
                        record['counts']['uncertain'] += sum(stock['analysis'] is not None and stock['analysis']['relation'] == 'uncertain' for stock in result['items'])
                    except HTTPException as error:
                        record['errors'].append({'topic_id': topic['id'], 'title': topic['title'], 'message': str(error.detail)})
                        record['counts']['failed'] += len(stocks)
                    record['topics_completed'] += 1
                    record['message'] = f"已匹配 {record['topics_completed']}/{len(topics)} 条新闻。"
                    await self.save(owner, record)
                else:
                    record.update(status='partial' if record['counts']['failed'] else 'completed', stage='ready',
                                  message='匹配完成；部分判断未完成，可点击重试。' if record['counts']['failed'] else '匹配完成，已按自选股汇总相关的新闻。')
                await self.save(owner, record)
        except asyncio.CancelledError:
            record.update(status='interrupted', message='匹配任务已中断，可重新分析；已完成结果会复用。')
            await self.save(owner, record)
            raise
        except Exception:
            logger.exception('News/watchlist matching failed')
            record.update(status='error', message='匹配暂未完成，可重新分析；已完成结果会复用。')
            await self.save(owner, record)
        finally:
            pulse.cancel(); await asyncio.gather(pulse, return_exceptions=True)
            await asyncio.to_thread(self.store.release, self.lease_key(owner), record['_token'])

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
