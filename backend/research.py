"""Search -> extract -> auditable cleaning -> validated model analysis -> persistence."""
import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from fastapi import HTTPException

from . import providers
from .catalog import stock_by_code
from .news_cleaning import SHANGHAI, clean_report, clean_text, metadata_date, normalize_url
from .prompts import Analysis, CommunityAnalysis, COMMUNITY_SYSTEM, NEWS_SYSTEM, PROMPT_VERSION, parse_json


def now_iso():
    return datetime.now(SHANGHAI).isoformat()


def fingerprint(*parts):
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()


def forward_returns(news, candles):
    """Use next trading day's close after publication day, not an intraday assumption."""
    items = []
    for article in news:
        index = next((i for i, bar in enumerate(candles) if bar['date'] > article['time']), None)
        row = {'news_id': article['id'], 'title': article['title'], 'score': article.get('score'), 'publication_date': article['time'], 'base_date': candles[index]['date'] if index is not None else None, 'return_3d': None, 'return_5d': None}
        for days in (3, 5):
            if index is not None and index + days < len(candles):
                row[f'return_{days}d'] = round((candles[index + days]['close'] / candles[index]['close'] - 1) * 100, 2)
        items.append(row)
    return {'items': items, 'note': '按发布日之后首个交易日收盘价为基准；未复权，不含费用。这是事后价格观察，不是策略胜率或新闻因果证明。'}


class ResearchService:
    def __init__(self, store, completion):
        self.store, self.completion = store, completion
        self.locks = {}
        self.capacity = asyncio.Semaphore(2)
        self.tasks = set()
        self.active = {}

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def launch(self, code, user, days=30, max_articles=3, community=False):
        # A single process is intentional; concurrent refreshes of a stock reuse a job.
        key = (user['id'], code)
        if key in self.active:
            return self.store.get('job', self.active[key], user['id'])
        if len(self.active) >= 8:
            raise HTTPException(429, '任务队列已满，请稍后重试。')
        job = {'id': uuid.uuid4().hex, 'code': code, 'status': 'queued', 'stage': '等待处理', 'created_at': now_iso(), 'warnings': [], 'counts': {}}
        self.store.put('job', job['id'], job, user['id'])
        self.active[key] = job['id']
        task = asyncio.create_task(self._job(job, user['id'], days, max_articles, community))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return job

    async def _job(self, job, owner, days, maximum, community):
        try:
            async with self.capacity:
                async with self.locks.setdefault(job['code'], asyncio.Lock()):
                    job.update(status='running')
                    def progress(stage):
                        job['stage'] = stage
                        self.store.put('job', job['id'], job, owner)
                    result = await asyncio.wait_for(self.collect(job['code'], days, maximum, community, progress), timeout=1800)
                    job.update(status='partial' if result['pipeline']['warnings'] else 'completed', stage='处理完成', counts=result['pipeline']['counts'], warnings=result['pipeline']['warnings'])
        except asyncio.CancelledError:
            job.update(status='failed', stage='服务停止，任务已中断', warnings=['可重新发起采集'])
            raise
        except Exception as exc:
            job.update(status='failed', stage='处理失败', warnings=[str(exc.detail) if isinstance(exc, HTTPException) else '数据处理失败，请重试（' + type(exc).__name__ + '）'])
        finally:
            job['finished_at'] = now_iso()
            try:
                self.store.put('job', job['id'], job, owner)
            except Exception as exc:
                logging.getLogger(__name__).error('Unable to save research job state: %s', type(exc).__name__)
            finally:
                self.active.pop((owner, job['code']), None)

    async def analyze_document(self, stock, item):
        content = item['content'][:10000]
        key = fingerprint(stock['code'], item['title'], content, PROMPT_VERSION, providers.read_config().get('model', ''))
        cached = self.store.get('analysis', key)
        if cached:
            return {**cached, 'cached': True}
        payload = {'stock_name': stock['name'], 'stock_code': stock['code'], 'exchange': stock['exchange'], 'title': item['title'], 'content': content, 'category': item.get('tag'), 'published_date': item['time'], 'url': item['url'], 'text_source': item['text_source'], 'date_status': item['date_status']}
        messages = [{'role': 'system', 'content': NEWS_SYSTEM}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
        for attempt in range(2):
            result = await self.completion(messages, max_tokens=4096)
            try:
                analysis = parse_json(result['content'], Analysis)
                reply = {**result, 'analysis': analysis, 'prompt_version': PROMPT_VERSION, 'analyzed_at': now_iso(), 'cached': False}
                self.store.put('analysis', key, reply)
                return reply
            except (ValueError, TypeError):
                if attempt:
                    raise HTTPException(502, '模型输出两次均未通过结构化校验。') from None
                messages.append({'role': 'user', 'content': '上一轮输出未通过JSON校验。请严格按照规定的字段、整数评分和三项因果链重新生成，不添加额外字段。'})

    async def collect(self, code, days, maximum, include_community, progress=lambda stage: None):
        stock = stock_by_code(code)
        if not stock:
            raise HTTPException(404, '暂不支持该股票。')
        end = datetime.now(SHANGHAI).date()
        start = end - timedelta(days=days)
        warnings, searches, statuses = [], [], {}
        progress('检索真实新闻并获取历史行情')
        results = await asyncio.gather(providers.search_news(stock, str(start), str(end)), providers.akshare_news(stock, str(start), str(end)), providers.daily_market(stock, str(end)), return_exceptions=True)
        for name, response in zip(('tavily', 'akshare_news'), results[:2]):
            if isinstance(response, Exception):
                statuses[name] = 'error'
                warnings.append(str(response) if isinstance(response, providers.ProviderError) else f'{name} 数据源不可用')
            else:
                statuses[name] = 'ok' if response.get('results') else 'empty'
                searches.append({'stock': stock['name'], 'provider': name, 'response': response})
        previous = self.store.get('dashboard', code, default={})
        if isinstance(results[2], Exception):
            warnings.append(str(results[2]) if isinstance(results[2], providers.ProviderError) else '行情获取失败')
            market = {**previous.get('quote', {}), 'status': 'stale' if previous.get('candles') else 'unavailable', 'error': '本次行情采集失败'}
            candles = previous.get('candles', [])
        else:
            market = results[2]
            candles = market['candles']
        progress('提取正文与清洗去重')
        raw = {'date_range': [str(start), str(end)], 'searches': searches}
        urls = list(dict.fromkeys(normalize_url(row.get('url', '')) for group in searches for row in group['response'].get('results', [])))
        urls = [url for url in urls if url][:12]
        extracts = {}
        if urls:
            try:
                extracted = await providers.tavily('extract', {'urls': urls, 'extract_depth': 'basic', 'format': 'text'})
                extracts = {normalize_url(row['url']): row.get('raw_content', '') for row in extracted['results'] if row.get('raw_content')}
                statuses['extract'] = 'ok'
                if extracted.get('failed_results'):
                    warnings.append('部分正文提取失败，对应条目使用搜索片段并标注局限')
            except providers.ProviderError as exc:
                statuses['extract'] = 'error'
                warnings.append(str(exc) + '；仅使用搜索片段')
        cleaned = clean_report(raw, extracts)
        if any(row['text_stats'].get('extraction_mismatch') for row in cleaned['items']):
            warnings.append('部分页面正文与标题主题不匹配，已降级为搜索片段')
        items = sorted(cleaned['items'], key=lambda row: (row['effective_date'], row['tier'] == 'company', row.get('search_relevance') or 0), reverse=True)
        news = []
        for item in items:
            article = {'id': fingerprint(code, item['url'])[:32], 'title': item['title'], 'source': urlsplit(item['url']).hostname, 'url': item['url'], 'content': item['cleaned_text'][:12000], 'score': None, 'tag': item['category_label'], 'time': item['effective_date'], 'sources': item['sources'], 'text_source': item['text_source'], 'date_status': item['date_status'], 'analysis_status': 'pending', 'analysis': None}
            news.append(article)
        progress('生成并校验 AI 新闻研判')
        analyzed = 0
        for index, article in enumerate(news[:maximum]):
            progress(f'AI 新闻研判 {index + 1}/{min(maximum, len(news))}')
            try:
                reply = await self.analyze_document(stock, article)
                article.update(analysis=reply['analysis'], score=reply['analysis']['sentiment_score'], analysis_status='completed', model=reply['model'], analyzed_at=reply['analyzed_at'], cached=reply['cached'])
                analyzed += 1
            except HTTPException as exc:
                article.update(analysis_status='failed', analysis_error=exc.detail)
                warnings.append(exc.detail)
        # If both search services fail, retain the last successful dataset with a
        # visible stale status; a successful empty search really replaces old news.
        if not searches:
            news = previous.get('news', [])
            warnings.append('新闻源均失败，保留上次结果；请检查更新时间')
        community = previous.get('sentiment', {'status': 'not_collected', 'sample_count': 0, 'bull': None, 'bear': None, 'neutral': None, 'keywords': [], 'posts': []})
        if include_community:
            progress('检索社区样本并统计情绪')
            community = await self.community(stock, str(start), str(end))
            if community['status'] == 'error':
                warnings.append(community['error'])
        quote = {key: value for key, value in market.items() if key != 'candles'}
        result = {'stock': {**stock, 'price': quote.get('price'), 'change': quote.get('change')}, 'quote': quote, 'candles': candles, 'news': news, 'sentiment': community, 'as_of': now_iso(), 'data_source': 'live', 'pipeline': {'date_range': raw['date_range'], 'statuses': statuses, 'counts': {**cleaned['summary'], 'extracted': len(extracts), 'analyzed': analyzed}, 'warnings': list(dict.fromkeys(warnings)), 'job_status': 'partial' if warnings else 'completed'}, 'backtest': forward_returns(news, candles)}
        snapshot_key = uuid.uuid4().hex
        self.store.put('collection', snapshot_key, {'code': code, 'collected_at': result['as_of'], 'raw': raw, 'cleaning': cleaned, 'extract_urls': list(extracts), 'pipeline': result['pipeline']})
        result['pipeline']['collection_id'] = snapshot_key
        self.store.put('dashboard', code, result)
        return result

    async def community(self, stock, start, end):
        posts = []
        try:
            response = await providers.tavily('search', {'query': f'{stock["name"]} {stock["code"]} 股吧 讨论', 'topic': 'general', 'search_depth': 'advanced', 'max_results': 20, 'start_date': start, 'end_date': end, 'include_published_date': True, 'include_domains': ['guba.eastmoney.com', 'xueqiu.com'], 'include_answer': False})
            seen = set()
            for raw in response['results']:
                url = normalize_url(raw.get('url', ''))
                published = metadata_date(raw.get('published_date'))
                host = urlsplit(url).hostname or ''
                if host not in ('guba.eastmoney.com', 'xueqiu.com') or not published or not start <= str(published) <= end or url in seen:
                    continue
                text, _ = clean_text(raw.get('content', ''), raw.get('title', ''))
                if stock['name'] not in raw.get('title', '') + text and stock['code'] not in raw.get('title', '') + text:
                    continue
                # A board listing is not an individual post.
                if host == 'guba.eastmoney.com' and '/news,' not in url:
                    continue
                if host == 'xueqiu.com' and not re.search(r'/\d+/\d+', url):
                    continue
                seen.add(url)
                posts.append({'id': fingerprint(url)[:16], 'url': url, 'title': raw['title'], 'content': text[:1200], 'date': str(published), 'weight': 1})
            if not posts:
                return {'status': 'empty', 'sample_count': 0, 'bull': None, 'bear': None, 'neutral': None, 'keywords': [], 'posts': [], 'note': '本次检索未取得日期可核验的独立社区帖子，不生成比例。'}
            result = await self.completion([{'role': 'system', 'content': COMMUNITY_SYSTEM}, {'role': 'user', 'content': json.dumps({'stock': stock['name'], 'posts': posts}, ensure_ascii=False)}], max_tokens=2500)
            parsed = parse_json(result['content'], CommunityAnalysis)
            ids = [row['id'] for row in parsed['items']]
            if len(set(ids)) != len(posts) or set(ids) != {row['id'] for row in posts}:
                raise ValueError('Invalid classified IDs')
            mapping = {row['id']: row['stance'] for row in parsed['items']}
            for post in posts:
                post['stance'] = mapping[post['id']]
            ratios = {stance: round(sum(p['stance'] == stance for p in posts) / len(posts) * 100, 1) for stance in ('bull', 'bear')}
            ratios['neutral'] = round(100 - ratios['bull'] - ratios['bear'], 1)
            alert = '样本看多比例偏高，注意核验代表性' if ratios['bull'] > 85 else '样本看空比例偏高，注意核验代表性' if ratios['bear'] > 80 else '未触发样本极端情绪阈值'
            return {'status': 'ok', 'sample_count': len(posts), **ratios, 'keywords': parsed['keywords'], 'posts': posts, 'alert': alert, 'collected_at': now_iso(), 'note': 'Tavily 检索到的有限社区帖子；无阅读/回复量，采用等权，不能代表全部股民。'}
        except Exception as exc:
            return {'status': 'error', 'sample_count': len(posts), 'bull': None, 'bear': None, 'neutral': None, 'keywords': [], 'posts': posts, 'error': str(exc) if isinstance(exc, providers.ProviderError) else '社区采集或结构化分类失败', 'note': '未生成有效情绪比例'}

    def dashboard(self, code):
        stock = stock_by_code(code)
        if not stock:
            raise HTTPException(404, '暂不支持该股票。')
        return self.store.get('dashboard', code, default={'stock': {**stock, 'price': None, 'change': None}, 'quote': {'status': 'not_collected', 'is_realtime': False}, 'candles': [], 'news': [], 'sentiment': {'status': 'not_collected', 'sample_count': 0, 'bull': None, 'bear': None, 'neutral': None, 'keywords': [], 'posts': []}, 'as_of': None, 'data_source': 'live', 'pipeline': None, 'backtest': forward_returns([], [])})
