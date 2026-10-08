"""Search -> extract -> auditable cleaning -> validated model analysis -> persistence."""
import asyncio
import hashlib
import json
import logging
import math
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from fastapi import HTTPException

from . import providers
from .community import collect_posts, MIN_POSTS
from .market_sentiment import MarketSentimentService
from .stock_catalog import StockCatalog
from .daily_market import DailyMarketService, quote_order
from .industry import PROFILE_NOTE, PROFILE_VERSION, build_topics, select_analyses
from .industry_news import IndustryNewsService
from .news_cleaning import SHANGHAI, clean_report, clean_text, metadata_date, normalize_url, material_kind, REASONS
from .evidence import build_overview, rank_events, is_scored
from .prompts import EvidenceAnalysis, CommunityAnalysis, COMMUNITY_SYSTEM, NEWS_SYSTEM, PROMPT_VERSION, parse_json


def now_iso():
    return datetime.now(SHANGHAI).isoformat()


def fingerprint(*parts):
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()


def analysis_context(stock, item, profile, business):
    return {'news_scope':item.get('news_scope','company'), 'industry':item.get('industry'),
            'related_factors':item.get('related_factors',[]), 'relevance_reason':item.get('relevance_reason',''),
            'industry_profile':{key:profile.get(key) for key in ('industry','source','version','note')},
            'business_profile':{key:business.get(key) for key in ('main_business','revenue_segments','source','url','version','status','fetched_at','note')}}


def analysis_cache_key(stock, item, context):
    return fingerprint(stock['code'], item['title'], item['content'][:10000], item.get('time',''),
                       item.get('date_status',''),item.get('text_source',''),json.dumps(context,ensure_ascii=False,sort_keys=True),
                       PROMPT_VERSION,providers.read_config().get('model',''))


def news_documents(code, cleaned):
    items = sorted(cleaned['items'], key=lambda row: (row['effective_date'], row['tier'] == 'company', row.get('search_relevance') or 0), reverse=True)
    return [{'id': fingerprint(code, item['url'])[:32], 'title': item['title'], 'source': urlsplit(item['url']).hostname,
             'url': item['url'], 'content': item['cleaned_text'][:12000], 'score': None, 'tag': item['category_label'],
             'tier': item['tier'], 'search_relevance': item.get('search_relevance'), 'time': item['effective_date'],
             'sources': item['sources'], 'text_source': item['text_source'], 'date_status': item['date_status'],
             'analysis_status': 'pending', 'analysis': None,
             **{key: item[key] for key in ('news_scope', 'industry', 'related_factors', 'relevance_reason', 'stale')}} for item in items]


def forward_returns(news, candles):
    """Use next trading day's close after publication day, not an intraday assumption."""
    items = []
    for article in news:
        published=article.get('time')
        index = next((i for i, bar in enumerate(candles) if published and bar['date'] > published), None)
        row = {'news_id': article['id'], 'title': article.get('title','未保存标题'), 'score': article.get('score'), 'publication_date': published, 'base_date': candles[index]['date'] if index is not None else None, 'return_3d': None, 'return_5d': None}
        for days in (3, 5):
            if index is not None and index + days < len(candles):
                row[f'return_{days}d'] = round((candles[index + days]['close'] / candles[index]['close'] - 1) * 100, 2)
        items.append(row)
    return {'items': items, 'note': '按发布日之后首个交易日收盘价为基准；未复权，不含费用。这是事后价格观察，不是策略胜率或新闻因果证明。'}


class ResearchService:
    def __init__(self, store, completion):
        self.store, self.completion = store, completion
        self.catalog = StockCatalog(store)
        if store is not None:
            store.catalog = self.catalog
        self.watch_locks = {}
        self.locks = {}
        self.capacity = asyncio.Semaphore(2)
        self.tasks = set()
        self.job_tasks = {}
        self.active = {}
        self.inflight = {}
        self.max_pending = 40
        self.live_jobs = {}
        self.job_starts = {}
        self.dashboard_revisions = {}
        self.daily_market = DailyMarketService(store) if store is not None else None
        self.industry_news = IndustryNewsService(store) if store is not None else None
        self.market_sentiment = MarketSentimentService()

    async def close(self):
        if self.industry_news:
            await self.industry_news.close()
        if self.daily_market:
            await self.daily_market.close()
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def launch(self, code, user, days=30, max_articles=6, community=False, start=True, community_only=False, analysis_only=False):
        # A single process is intentional; concurrent refreshes of a stock reuse a job.
        key = (user['id'], code)
        if key in self.active:
            return self.live_jobs[self.active[key]]
        if len(self.active) >= self.max_pending:
            raise HTTPException(429, '任务队列已满，请稍后重试。')
        job = {'id': uuid.uuid4().hex, 'code': code, 'kind': 'analysis_all' if analysis_only else 'community' if community_only else 'research', 'status': 'queued', 'stage': '等待处理', 'created_at': now_iso(), 'warnings': [], 'counts': {}}
        self.active[key] = job['id']
        self.live_jobs[job['id']] = job
        try:
            await asyncio.to_thread(self.store.put, 'job', job['id'], job, user['id'])
        except BaseException:
            self.active.pop(key, None)
            self.live_jobs.pop(job['id'], None)
            raise
        event = asyncio.Event()
        self.job_starts[job['id']] = event
        if start:
            event.set()
        task = asyncio.create_task(self._job(job, user['id'], days, max_articles, community, community_only, analysis_only))
        self.tasks.add(task)
        self.job_tasks[job['id']] = task
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(lambda finished: self.job_tasks.pop(job['id'], None))
        return job

    async def _job(self, job, owner, days, maximum, community, community_only=False, analysis_only=False):
        updates = asyncio.Queue()
        async def persist_progress():
            while True:
                snapshot = await updates.get()
                try:
                    await asyncio.to_thread(self.store.put, 'job', job['id'], snapshot, owner)
                except Exception:
                    logging.getLogger(__name__).warning('Could not save research progress')
                finally:
                    updates.task_done()
        writer = asyncio.create_task(persist_progress())
        try:
            await self.job_starts[job['id']].wait()
            def progress(stage):
                job.update(status='running', stage=stage)
                job['data_revision'] = self.dashboard_revisions.get(job['code'])
                updates.put_nowait(dict(job))
            result = await self.refresh_shared(job['code'], days, maximum, community, progress, community_only=community_only, analysis_only=analysis_only)
            job.update(status='partial' if result['pipeline']['warnings'] else 'completed', stage='处理完成', counts=result['pipeline']['counts'], warnings=result['pipeline']['warnings'])
        except asyncio.CancelledError:
            job.update(status='failed', stage='服务停止，任务已中断', warnings=['可重新发起采集'])
            raise
        except Exception as exc:
            job.update(status='failed', stage='处理失败', warnings=[str(exc.detail) if isinstance(exc, HTTPException) else '数据处理失败，请重试（' + type(exc).__name__ + '）'])
        finally:
            await updates.join()
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
            job['finished_at'] = now_iso()
            try:
                await asyncio.to_thread(self.store.put, 'job', job['id'], job, owner)
            except Exception as exc:
                logging.getLogger(__name__).error('Unable to save research job state: %s', type(exc).__name__)
            finally:
                self.active.pop((owner, job['code']), None)
                self.live_jobs.pop(job['id'], None)
                self.job_starts.pop(job['id'], None)

    async def refresh_shared(self, code, days=7, maximum=3, community=False, progress=lambda stage: None, community_only=False, analysis_only=False):
        """Share identical concurrent work; each account keeps its own job record."""
        key = (code, days, maximum, community, community_only, analysis_only)
        if key not in self.inflight:
            listeners = set()
            async def run():
                # Wait for the stock lock before reserving global capacity.
                async with self.locks.setdefault(code, asyncio.Lock()):
                    async with self.capacity:
                        def notify(stage):
                            for callback in list(listeners):
                                try:
                                    callback(stage)
                                except Exception:
                                    logging.getLogger(__name__).warning('Could not persist job progress')
                        work = self.analyze_all(code, notify) if analysis_only else self.collect_community(code, days, notify) if community_only else self.collect(code, days, maximum, community, notify)
                        return await asyncio.wait_for(work, 1800)
            task = asyncio.create_task(run())
            self.inflight[key] = (task, listeners)
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            def finish(done):
                self.inflight.pop(key, None)
                if not done.cancelled():
                    done.exception()  # Retrieve errors even if all observers disconnect.
            task.add_done_callback(finish)
        task, listeners = self.inflight[key]
        listeners.add(progress)
        try:
            return await asyncio.shield(task)
        finally:
            listeners.discard(progress)

    async def analyze_document(self, stock, item):
        content = item['content'][:10000]
        profile = stock.get('industry_profile') or (await asyncio.to_thread(self.store.get, 'stock_industry', stock['code'], default={})).get('data') or {}
        profile = {**profile, 'version': profile.get('version') or PROFILE_VERSION, 'note': profile.get('note') or PROFILE_NOTE}
        business = stock.get('business_profile') or await self.industry_news.business(stock)
        relation_context = analysis_context(stock,item,profile,business)
        key = analysis_cache_key(stock,item,relation_context)
        cached = await asyncio.to_thread(self.store.get, 'analysis', key)
        if cached:
            return {**cached, 'context_key':key, 'cached': True}
        payload = {'stock_name': stock['name'], 'stock_code': stock['code'], 'exchange': stock['exchange'], 'title': item['title'], 'content': content, 'category': item.get('tag'), 'published_date': item['time'], 'url': item['url'], 'text_source': item['text_source'], 'date_status': item['date_status'], 'analysis_time': now_iso(), **relation_context}
        messages = [{'role': 'system', 'content': NEWS_SYSTEM}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
        for attempt in range(2):
            try:
                result = await self.completion(messages, max_tokens=4096 if attempt == 0 else 8192)
            except HTTPException as exc:
                if attempt == 0 and exc.status_code == 502 and '长度上限' in str(exc.detail):
                    messages.append({'role':'user','content':'上一轮输出被截断。请精简文字，仍保留全部必要字段、三项因果链和每个列表最多三项，只返回完整JSON。'})
                    continue
                raise
            try:
                analysis = parse_json(result['content'], EvidenceAnalysis)
                analysis['version'] = PROMPT_VERSION
                reply = {**result, 'context_key':key, 'analysis': analysis, 'prompt_version': PROMPT_VERSION, 'analyzed_at': now_iso(), 'cached': False}
                await asyncio.to_thread(self.store.put, 'analysis', key, reply)
                return reply
            except (ValueError, TypeError):
                if attempt:
                    raise HTTPException(502, '模型输出两次均未通过结构化校验。') from None
                messages.append({'role': 'user', 'content': '上一轮输出未通过JSON校验。请严格按照规定的字段、assessment只允许positive/negative且与非零分数方向一致（禁止null或0，5的整数倍）、证据列表和三项因果链重新生成，不添加额外字段。'})

    async def analyze_all(self, code, progress=lambda stage: None):
        """Score every currently pending document; caller holds the shared stock lock."""
        result = await asyncio.to_thread(self.dashboard,code)
        candidates = [row for row in result['news'] if not is_scored(row)]
        profile = await self.industry_news.business(result['stock'])
        result['business_profile'] = profile
        pipeline = result.setdefault('pipeline',{}) or {}
        result['pipeline'] = pipeline
        pipeline.setdefault('counts',{}).update(analysis_total=len(candidates),analysis_finished=0,analyzed=0)
        pipeline.setdefault('stages',{})['analysis']='running' if candidates else 'completed'
        # Keep source restrictions; a previous model error is retried, not retained as a new failure.
        old_errors = {row.get('analysis_error') for row in candidates}
        warnings = [w for w in pipeline.get('warnings',[]) if w not in old_errors]
        publish_lock = asyncio.Lock()
        async def publish(stage):
            async with publish_lock:
                result.update(as_of=now_iso(),revision=uuid.uuid4().hex)
                pipeline['warnings'] = list(dict.fromkeys(warnings))
                pipeline['job_status'] = 'running' if pipeline['stages']['analysis']=='running' else 'partial' if warnings else 'completed'
                result['research_overview']=build_overview(result['news'])
                result['backtest']=forward_returns(result['news'],result.get('candles',[]))
                write=asyncio.create_task(asyncio.to_thread(self.store.put,'dashboard',code,deepcopy(result)))
                cancelled=False
                while True:
                    try:
                        await asyncio.shield(write)
                        break
                    except asyncio.CancelledError:
                        if write.cancelled():
                            raise
                        # Repeated cancellation cannot stop a DB thread. Finish
                        # the real write before any recovery snapshot can follow.
                        cancelled=True
                if cancelled:
                    raise asyncio.CancelledError
                self.dashboard_revisions[code]=result['revision']
                progress(stage)
        semaphore=asyncio.Semaphore(3)
        async def run(row):
            async with semaphore:
                try:
                    reply=await self.analyze_document({**result['stock'],'business_profile':profile,'industry_profile':result.get('industry_profile')},row)
                    row.update(analysis=reply['analysis'],score=reply['analysis']['sentiment_score'],analysis_status='completed',
                               analyzed_at=reply['analyzed_at'],model=reply['model'],analysis_prompt_version=reply['prompt_version'],analysis_context_key=reply['context_key'])
                    row.pop('analysis_error',None)
                    if row.pop('refresh_pending',False):
                        row['stale']=True
                    pipeline['counts']['analyzed']+=1
                except Exception as exc:
                    error=str(exc.detail) if isinstance(exc,HTTPException) else 'AI 新闻研判失败（'+type(exc).__name__+'）'
                    row.update(analysis=None,score=None,analysis_status='failed',analysis_error=error)
                    warnings.append(error)
                pipeline['counts']['analysis_finished']+=1
                if pipeline['counts']['analysis_finished']==len(candidates):
                    pipeline['stages']['analysis']='completed'
                await publish(f'全部研判：{pipeline["counts"]["analysis_finished"]}/{len(candidates)}')
        workers=[]
        try:
            for row in candidates:
                row.update(analysis_status='running',score=None)
            await publish(f'全部研判：0/{len(candidates)}')
            workers=[asyncio.create_task(run(row)) for row in candidates]
            await asyncio.gather(*workers)
        except BaseException:
            # A failed progress write must not leave sibling models running after
            # the caller releases the stock lock.
            for worker in workers:
                if not worker.done() and worker.cancelling()==0:
                    worker.cancel()
            draining=asyncio.gather(*workers,return_exceptions=True)
            while True:
                try:
                    await asyncio.shield(draining)
                    break
                except asyncio.CancelledError:
                    # Complete all writes before releasing the stock lock, even
                    # if shutdown sends another cancellation during cleanup.
                    continue
            for row in candidates:
                if row['analysis_status']=='running':
                    row.update(analysis_status='pending',analysis=None,score=None)
            pipeline['stages']['analysis']='failed'
            warnings.append('批量研判已中断，剩余新闻可重新研判')
            try:
                await publish('全部研判已中断')
            except Exception:
                logging.getLogger(__name__).warning('Could not save interrupted batch state')
            raise
        return result

    async def collect(self, code, days, maximum, include_community, progress=lambda stage: None):
        stock = self.catalog.get(code)
        if not stock:
            raise HTTPException(404, '暂不支持该股票。')
        end = datetime.now(SHANGHAI).date()
        start = end - timedelta(days=days)
        previous = await asyncio.to_thread(self.store.get, 'dashboard', code, default={})
        warnings, searches, statuses = [], [], {}
        raw = {'date_range': [str(start), str(end)], 'searches': searches}
        stages = {'market': 'pending', 'news': 'pending', 'industry': 'pending', 'analysis': 'pending', 'community': 'pending' if include_community else 'not_requested'}
        result = {'stock': {**stock, 'price': previous.get('quote', {}).get('price'), 'change': previous.get('quote', {}).get('change')}, 'quote': previous.get('quote', {'status': 'not_collected', 'is_realtime': False}), 'candles': previous.get('candles', []), 'news': previous.get('news', []), 'sentiment': previous.get('sentiment', {'status': 'not_collected', 'sample_count': 0, 'bull': None, 'bear': None, 'neutral': None, 'keywords': [], 'posts': []}), 'as_of': previous.get('as_of'), 'data_source': 'live', 'pipeline': {'date_range': raw['date_range'], 'statuses': statuses, 'counts': {'input': 0, 'retained': 0, 'merged': 0, 'analyzed': 0}, 'warnings': [], 'stages': stages, 'job_status': 'running'}}
        publish_lock = asyncio.Lock()
        extracts, attempted_extracts = {}, set()
        snapshot_key = uuid.uuid4().hex

        async def extract_documents(urls):
            urls = [url for url in urls if url not in attempted_extracts and url not in extracts][:max(0, 12 - len(attempted_extracts | set(extracts)))]
            if not urls:
                return
            attempted_extracts.update(urls)
            try:
                extracted = await providers.tavily('extract', {'urls': urls, 'extract_depth': 'basic', 'format': 'text'})
                extracts.update({normalize_url(row['url']): row.get('raw_content', '') for row in extracted['results'] if row.get('raw_content')})
                statuses['extract'] = 'partial' if statuses.get('extract') == 'error' else 'ok'
                if extracted.get('failed_results'):
                    warnings.append('部分正文提取失败，对应条目使用搜索片段并标注局限')
            except providers.ProviderError as exc:
                statuses['extract'] = 'partial' if extracts else 'error'
                warnings.append(str(exc) + '；仅使用搜索片段')

        async def publish(stage):
            # Serialize writes and copy the payload before handing it to a DB
            # thread: concurrent market/model results must never overwrite one
            # another with an older snapshot.
            async with publish_lock:
                result['as_of'], result['revision'] = now_iso(), uuid.uuid4().hex
                result['pipeline']['warnings'] = list(dict.fromkeys(warnings))
                result['backtest'] = forward_returns(result['news'], result['candles'])
                snapshot = deepcopy(result)
                await asyncio.to_thread(self.store.put, 'dashboard', code, snapshot)
                self.dashboard_revisions[code] = snapshot['revision']
                progress(stage)

        async def market_update():
            try:
                market = await self.daily_market.get(stock)
                result['quote'] = {key: value for key, value in market.items() if key != 'candles'}
                result['candles'] = market['candles']
                stages['market'], statuses['market'] = 'ready', 'ok'
            except Exception as exc:
                warnings.append(str(exc) if isinstance(exc, providers.ProviderError) else '行情获取失败')
                result['quote'] = {**previous.get('quote', {}), 'status': 'stale' if previous.get('candles') else 'unavailable', 'error': '本次行情采集失败'}
                stages['market'], statuses['market'] = 'failed', 'error'
            result['stock'].update(price=result['quote'].get('price'), change=result['quote'].get('change'))
            await publish('K 线已更新，新闻与研判继续处理' if stages['market'] == 'ready' else '行情更新失败，继续处理新闻')

        async def industry_update():
            profile = await self.industry_news.profile(stock)
            result['industry_profile'] = profile
            statuses['industry_profile'] = profile['status']
            if profile.get('industry'):
                result['stock']['industry'] = profile['industry']
            if profile.get('error'):
                warnings.append('行业资料：' + profile['error'])
            topics = build_topics(profile.get('industry'))
            if not topics:
                statuses['industry_news'] = 'unavailable'
                warnings.append('所属行业暂无法核验，当前仅更新公司新闻')
                return []
            responses = await asyncio.gather(*(self.industry_news.search(topic, str(start), str(end)) for topic in topics))
            statuses['industry_news'] = 'partial' if any(row.get('error') for row in responses) else 'ok' if any(row['response']['results'] for row in responses) else 'empty'
            groups = []
            for index, row in enumerate(responses):
                statuses['industry_topic_' + str(index + 1)] = row['status']
                if row.get('error'):
                    warnings.append('行业新闻：' + row['error'])
                if row['status'] in ('ok', 'stale'):
                    groups.append({'stock': stock['name'], 'provider': 'tavily_industry', 'news_scope': 'industry',
                                   'topic': row['topic'], 'cache_status': row['status'], 'response': row['response']})
            return sorted(groups, key=lambda group: group['cache_status'] == 'stale')

        async def business_update():
            result['business_profile'] = await self.industry_news.business(stock)
            statuses['business_profile'] = result['business_profile']['status']
            if result['business_profile'].get('error'):
                warnings.append('主营资料未更新；研判保留缺失条件')

        workers = [asyncio.create_task(providers.search_news(stock, str(start), str(end))), asyncio.create_task(providers.akshare_news(stock, str(start), str(end))), asyncio.create_task(market_update()), asyncio.create_task(industry_update()), asyncio.create_task(business_update())]
        try:
            await publish('获取历史行情并检索真实新闻')
            results = await asyncio.gather(*workers[:2], return_exceptions=True)
            for name, response in zip(('tavily', 'akshare_news'), results):
                if isinstance(response, Exception):
                    statuses[name] = 'error'
                    warnings.append(str(response) if isinstance(response, providers.ProviderError) else f'{name} 数据源不可用')
                else:
                    statuses[name] = 'partial' if response.get('warnings') else 'ok' if response.get('results') else 'empty'
                    warnings.extend(response.get('warnings', []))
                    searches.append({'stock': stock['name'], 'provider': name, 'response': response})
            if not workers[3].done():
                # Publish usable company documents independently of a cold or
                # stalled industry source, without spending extra model slots.
                for group in searches:
                    for row in rank_events(group['response'].get('results', []))[:8]:
                        if row.get('raw_content') and len(extracts) < 8:
                            extracts[normalize_url(row['url'])] = row['raw_content']
                company_urls = list(dict.fromkeys(normalize_url(row.get('url', '')) for group in searches for row in group['response'].get('results', [])))
                await extract_documents([url for url in company_urls if url][:8])
                early = clean_report(raw, extracts, self.catalog.entities(stock), industry_profile=result.get('industry_profile'))
                result['news'] = news_documents(code, early)
                company_failed = all(statuses.get(name) == 'error' for name in ('tavily', 'akshare_news'))
                known_urls = {row['url'] for row in result['news']}
                for old in previous.get('news', []):
                    scope = old.get('news_scope', 'company')
                    same_industry = not result.get('industry_profile', {}).get('industry') or old.get('industry') == result['industry_profile']['industry']
                    if (scope == 'industry' and same_industry or scope == 'company' and company_failed) and old.get('url') not in known_urls and str(start) <= old.get('time', '') <= str(end):
                        result['news'].append({**old, **({'refresh_pending': True} if scope == 'industry' else {'stale': True})})
                        known_urls.add(old.get('url'))
                result['pipeline']['counts'] = {**early['summary'], 'extracted': len(extracts), 'analyzed': 0}
                stages['news'] = 'ready' if searches else 'failed'
                await asyncio.to_thread(self.store.put, 'collection', snapshot_key, {'code': code, 'collected_at': now_iso(), 'raw': deepcopy(raw), 'cleaning': early, 'extract_urls': list(extracts), 'pipeline': deepcopy(result['pipeline'])})
                result['pipeline']['collection_id'] = snapshot_key
                await publish('公司新闻已就绪，行业新闻继续检索')
            try:
                searches.extend(await workers[3])
            except Exception:
                statuses['industry_news'] = 'unavailable'
                warnings.append('行业采集暂不可用，继续处理公司新闻')
            stages['industry'] = 'ready' if statuses.get('industry_news') in ('ok', 'empty') else 'failed'
            progress('提取正文与清洗去重')
            candidates = {}
            for group in searches:
                for row in group['response'].get('results', []):
                    url = normalize_url(row.get('url', ''))
                    if url and url not in candidates:
                        candidates[url] = {**row, 'id': url, 'news_scope': group.get('news_scope', 'company')}
            chosen = select_analyses(rank_events(list(candidates.values())), 12)
            for row in chosen:
                if row.get('raw_content') and (row['id'] in attempted_extracts | set(extracts) or len(attempted_extracts | set(extracts)) < 12):
                    extracts.setdefault(row['id'], row['raw_content'])
            urls = [row['id'] for row in chosen]
            await extract_documents(urls)
            cleaned = clean_report(raw, extracts, self.catalog.entities(stock), industry_profile=result.get('industry_profile'))
            if any(row['text_stats'].get('extraction_mismatch') for row in cleaned['items']):
                warnings.append('部分页面正文与标题主题不匹配，已降级为搜索片段')
            news = news_documents(code, cleaned)
            await workers[4]
            context_stock = {**stock,'industry_profile':result.get('industry_profile'),'business_profile':result.get('business_profile')}
            def current_context(article):
                return analysis_cache_key(stock,article,analysis_context(stock,article,result.get('industry_profile') or {},result.get('business_profile') or {}))
            old_by_url = {row.get('url'): row for row in previous.get('news', [])}
            for article in news:
                old = old_by_url.get(article['url'])
                if article['stale'] and old and old.get('analysis_prompt_version') == PROMPT_VERSION and old.get('analysis_context_key') == current_context(article) and all(old.get(key) == article.get(key) for key in ('content', 'time', 'news_scope', 'industry', 'related_factors', 'relevance_reason')):
                    article.update({key: old[key] for key in ('analysis', 'score', 'analysis_status', 'analyzed_at', 'model', 'analysis_prompt_version', 'analysis_context_key') if key in old})
            company_failed = all(statuses.get(name) == 'error' for name in ('tavily', 'akshare_news'))
            industry_failed = statuses.get('industry_news') in ('partial', 'unavailable')
            known_urls = {row['url'] for row in news}
            for old in previous.get('news', []):
                scope = old.get('news_scope', 'company')
                keep = company_failed if scope == 'company' else industry_failed
                # Never carry an old industry's events onto a newly classified stock.
                same_industry = not result.get('industry_profile', {}).get('industry') or old.get('industry') == result['industry_profile']['industry']
                if keep and (scope != 'industry' or same_industry) and old.get('url') not in known_urls and str(start) <= old.get('time', '') <= str(end):
                    news.append({**old, 'stale': True})
                    known_urls.add(old.get('url'))
            if company_failed:
                warnings.append('公司新闻源均失败，保留检索区间内的上次公司结果；请检查更新时间')
            if industry_failed:
                warnings.append('行业新闻更新不完整，保留检索区间内的上次行业结果并标记')
            result['news'] = news
            stages['news'] = 'ready' if searches else 'failed'
            for article in news:
                if article.get('stale') and article.get('analysis') and article.get('analysis_context_key') != current_context(article):
                    article.update(analysis=None,score=None,analysis_status='pending')
                    article.pop('analysis_context_key',None)
            selected = select_analyses(rank_events([row for row in news if not row.get('stale')]), maximum)
            stages['analysis'] = 'running' if selected else 'completed'
            for article in selected:
                article['analysis_status'] = 'running'
            result['pipeline']['counts'] = {**cleaned['summary'], 'extracted': len(extracts), 'analyzed': 0, 'analysis_total': len(selected), 'analysis_finished': 0}
            # Save the audit before exposing the cleaned news and audit button.
            await asyncio.to_thread(self.store.put, 'collection', snapshot_key, {'code': code, 'collected_at': now_iso(), 'raw': raw, 'cleaning': cleaned, 'extract_urls': list(extracts), 'pipeline': deepcopy(result['pipeline'])})
            result['pipeline']['collection_id'] = snapshot_key
            await publish('清洗后的新闻已就绪，AI 研判继续补充')

            analysis_capacity = asyncio.Semaphore(3)
            async def analyze(article):
                async with analysis_capacity:
                    try:
                        reply = await self.analyze_document(context_stock, article)
                        article.update(analysis=reply['analysis'], score=reply['analysis']['sentiment_score'], analysis_status='completed', model=reply['model'], analyzed_at=reply['analyzed_at'], cached=reply['cached'], analysis_prompt_version=PROMPT_VERSION, analysis_context_key=reply['context_key'])
                        result['pipeline']['counts']['analyzed'] += 1
                    except Exception as exc:
                        detail = exc.detail if isinstance(exc, HTTPException) else 'AI 新闻研判失败（' + type(exc).__name__ + '）'
                        article.update(analysis_status='failed', analysis_error=detail)
                        warnings.append(detail)
                    result['pipeline']['counts']['analysis_finished'] += 1
                    finished = result['pipeline']['counts']['analysis_finished']
                    if finished == len(selected):
                        stages['analysis'] = 'completed'
                    await publish(f'AI 新闻研判已完成 {finished}/{len(selected)}')

            analyses = [asyncio.create_task(analyze(article)) for article in selected]
            workers.extend(analyses)
            await asyncio.gather(*analyses)
            if include_community:
                progress('检索社区样本并统计情绪')
                result['sentiment'] = await self.community(stock, str(start), str(end))
                stages['community'] = 'failed' if result['sentiment']['status'] == 'error' else 'ready'
                if stages['community'] == 'failed':
                    warnings.append(result['sentiment']['error'])
            await workers[2]
            result['pipeline']['job_status'] = 'partial' if warnings else 'completed'
            await publish('处理完成')
            await asyncio.to_thread(self.store.put, 'collection', snapshot_key, {'code': code, 'collected_at': result['as_of'], 'raw': raw, 'cleaning': cleaned, 'extract_urls': list(extracts), 'pipeline': deepcopy(result['pipeline'])})
            return result
        except BaseException:
            for worker in workers:
                if not worker.done():
                    worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            result['pipeline']['job_status'] = 'failed'
            for section, state in stages.items():
                if state in ('pending', 'running'):
                    stages[section] = 'failed'
            for article in result['news']:
                if article.pop('refresh_pending', False):
                    article['stale'] = True
                if article.get('analysis_status') == 'running':
                    article.update(analysis_status='failed', analysis_error='自动研判已中断，可重新采集或单独研判')
            warnings.append('任务中断，已获取的结果已保留，可重新采集')
            try:
                await publish('任务中断，已保留阶段结果')
            except Exception:
                logging.getLogger(__name__).warning('Could not save interrupted research snapshot')
            raise

    async def collect_community(self, code, days, progress=lambda stage: None):
        """Refresh only community opinion, preserving quotes/news/model results."""
        result = await asyncio.to_thread(self.dashboard, code)
        end = datetime.now(SHANGHAI).date()
        start = end - timedelta(days=days)
        progress('直接获取股吧帖子与原始发布时间')
        result['sentiment'] = await self.community(result['stock'], str(start), str(end))
        pipeline = result.get('pipeline') or {'date_range': [str(start), str(end)], 'statuses': {}, 'counts': {'input': 0, 'retained': 0, 'merged': 0, 'analyzed': 0}, 'warnings': []}
        result['pipeline'] = pipeline
        pipeline.setdefault('stages', {})['community'] = 'ready' if result['sentiment']['status'] == 'ok' else 'failed'
        pipeline['statuses']['community'] = result['sentiment']['status']
        warnings = list(result['sentiment'].get('warnings', []))
        if result['sentiment']['status'] != 'ok':
            warnings.append(result['sentiment'].get('error') or result['sentiment']['note'])
        pipeline['warnings'] = list(dict.fromkeys([*pipeline.get('warnings', []), *warnings]))
        pipeline['job_status'] = 'partial' if pipeline['warnings'] else 'completed'
        result['as_of'], result['revision'] = now_iso(), uuid.uuid4().hex
        await asyncio.to_thread(self.store.put, 'dashboard', code, result)
        self.dashboard_revisions[code] = result['revision']
        progress('社区样本处理完成')
        return result

    async def community(self, stock, start, end):
        posts = []
        details = {}
        try:
            collected = await collect_posts(stock, start, end)
            posts = collected['posts']
            details = {key: collected[key] for key in ('source', 'diagnostics', 'warnings')}
            details['collected_at'] = now_iso()
            if len(posts) < MIN_POSTS:
                failed = not posts and all(value.get('status') == 'error' for key, value in collected['diagnostics'].items() if key in ('direct', 'tavily'))
                return {**details, 'status': 'error' if failed else 'insufficient' if posts else 'empty', 'sample_count': len(posts), 'bull': None, 'bear': None, 'neutral': None, 'keywords': [], 'posts': posts, 'error': '公开社区数据源暂不可用' if failed else None, 'note': f'本次取得 {len(posts)} 条有效帖子，至少需要 {MIN_POSTS} 条才统计比例；没有把列表页或缺失日期的结果当作样本。', 'alert_level': None, 'weighting_method': 'log_interaction'}
            result = await self.completion([{'role': 'system', 'content': COMMUNITY_SYSTEM}, {'role': 'user', 'content': json.dumps({'stock': stock['name'], 'stock_code': stock['code'], 'posts': posts}, ensure_ascii=False)}], max_tokens=4096)
            parsed = parse_json(result['content'], CommunityAnalysis)
            ids = [row['id'] for row in parsed['items']]
            if len(set(ids)) != len(posts) or set(ids) != {row['id'] for row in posts}:
                raise ValueError('Invalid classified IDs')
            mapping = {row['id']: row['stance'] for row in parsed['items']}
            for post in posts:
                post['stance'] = mapping[post['id']]
                views = post.get('views') or 0
                replies = post.get('replies') or 0
                # 对数互动加权：基础权重 1.0 + ln(views+1) + 2*ln(replies+1)
                weight = round(1.0 + math.log(max(views, 0) + 1) + 2.0 * math.log(max(replies, 0) + 1), 2)
                post['weight'] = weight

            total_weight = sum(p['weight'] for p in posts) or 1.0
            bull_weight = sum(p['weight'] for p in posts if p['stance'] == 'bull')
            bear_weight = sum(p['weight'] for p in posts if p['stance'] == 'bear')
            ratios = {
                'bull': round(bull_weight / total_weight * 100, 1),
                'bear': round(bear_weight / total_weight * 100, 1),
            }
            ratios['neutral'] = round(max(0.0, 100.0 - ratios['bull'] - ratios['bear']), 1)

            if ratios['bull'] > 85:
                alert_level = 'overheated'
                alert = f"样本看多情绪集中：加权看多达 {ratios['bull']}%，仅表示当前样本偏多，不构成价格反转判断"
            elif ratios['bear'] > 80:
                alert_level = 'frozen'
                alert = f"样本看空情绪集中：加权看空达 {ratios['bear']}%，仅表示当前样本偏空，不构成价格反转判断"
            else:
                alert_level = 'normal'
                alert = '样本情绪未达到集中阈值'

            return {**details, 'status': 'ok', 'sample_count': len(posts), **ratios, 'keywords': parsed['keywords'], 'posts': posts, 'alert': alert, 'alert_level': alert_level, 'weighting_method': 'log_interaction', 'note': '优先直接采集东方财富股吧公开帖子，Tavily 仅作备用；结合阅读量与回复数对数加权统计，有限样本仅供情绪参考。'}
        except Exception as exc:
            return {**details, 'status': 'error', 'sample_count': len(posts), 'bull': None, 'bear': None, 'neutral': None, 'keywords': [], 'posts': posts, 'error': str(exc.detail) if isinstance(exc, HTTPException) else str(exc) if isinstance(exc, providers.ProviderError) else '社区采集或结构化分类失败', 'note': '已取得的帖子仍可查看，未生成有效情绪比例。', 'alert_level': None, 'weighting_method': 'log_interaction'}

    def dashboard(self, code):
        stock = self.catalog.get(code)
        if not stock:
            raise HTTPException(404, '暂不支持该股票。')
        result = self.store.get('dashboard', code, default={'stock': {**stock, 'price': None, 'change': None}, 'quote': {'status': 'not_collected', 'is_realtime': False}, 'candles': [], 'news': [], 'sentiment': {'status': 'not_collected', 'sample_count': 0, 'bull': None, 'bear': None, 'neutral': None, 'keywords': [], 'posts': []}, 'as_of': None, 'data_source': 'live', 'pipeline': None, 'backtest': forward_returns([], [])})
        excluded={row['id']:row for row in result.get('excluded_news',[])}
        eligible=[]
        for row in result.get('news',[]):
            kind=material_kind(row)
            if kind:
                excluded[row['id']]={key:row.get(key) for key in ('id','title','url')}
                excluded[row['id']]['reason']=REASONS[kind]
            else:
                eligible.append(row)
        result['news']=eligible
        if excluded:
            result['excluded_news']=list(excluded.values())
            pipeline=result.get('pipeline')
            if pipeline:
                pipeline.setdefault('counts',{}).update(retained=len(eligible),excluded_materials=len(excluded))
        daily = self.store.get('daily_market', code, default={})
        if daily.get('candles') and (not result.get('candles') or quote_order(daily.get('quote', {})) >= quote_order(result.get('quote', {}))):
            result['quote'], result['candles'] = daily['quote'], daily['candles']
            result['stock'] = {**result.get('stock', stock), 'price': daily['quote'].get('price'), 'change': daily['quote'].get('change')}
            result['backtest'] = forward_returns(result.get('news', []), daily['candles'])
        result['backtest'] = forward_returns(result.get('news',[]),result.get('candles',[]))
        result['research_overview'] = build_overview(result.get('news', []))
        return result
