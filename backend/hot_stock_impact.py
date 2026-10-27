"""Current hot topics -> account watchlist business impact, with source retrieval."""
import asyncio
from collections import Counter
from datetime import datetime
import json
import re
import time

from fastapi import HTTPException

from .catalog import DEFAULT_WATCHLIST
from .hot_stock_scoring import SCORING_SYSTEM, validate_scores
from .hot_news_board import digest
from .hot_news_events import validate_event
from .news_cleaning import SHANGHAI, get_stock_aliases
from .providers import read_config
from .company_knowledge import curated_relationships

VERSION = 'hot-stock-price-impact-v2-score'
NAMESPACE = 'hot_stock_impact'
IMPACT_SYSTEM = SCORING_SYSTEM


def retrieve_business_cards(title, summary, stock):
    """Retrieve exact passages, never decide relevance from lexical similarity."""
    text = stock['business']['main_business']
    query = title + summary
    grams = lambda value: {value[i:i + 2] for i in range(len(value) - 1) if re.fullmatch(r'[\w\u4e00-\u9fff]{2}', value[i:i + 2])}
    terms = grams(query) - {'公司', '股份', '有限', '业务', '发展'}
    candidates = []
    for start in range(0, len(text), 300):
        quote = text[start:start + 340]
        overlap = sorted(terms & grams(quote))
        candidates.append({'id': digest([stock['code'], stock['business']['fetched_at'], start, quote]),
                           'quote': quote, 'start': start, 'matched_terms': overlap,
                           'source': stock['business']['source'], 'url': stock['business']['url']})
    return sorted(candidates, key=lambda row: (-len(row['matched_terms']), row['start']))[:3]


class HotStockImpactService:
    def __init__(self, research, board, clock=time.time):
        self.research, self.board, self.store, self.clock = research, board, research.store, clock
        self.capacity = asyncio.Semaphore(2)
        self.requests = asyncio.Semaphore(2)

    def topic(self, topic_id):
        topic = next((row for row in self.board.feed()['items'] if row['id'] == topic_id), None)
        if topic and not self.published(topic): topic = None
        if topic is None:
            raise HTTPException(404, '该热点已不在当前榜单，请更新热点后重试。')
        return topic

    @staticmethod
    def published(topic):
        try:
            if topic['screening']['decision'] != 'keep': return False
            validate_event(topic['screening'].get('event'), topic['title'] + '\n' + topic.get('summary', ''))
            return True
        except (ValueError, KeyError, TypeError):
            return False

    def stocks(self, owner):
        codes = self.store.get('watchlist', 'list', owner, DEFAULT_WATCHLIST)
        result = []
        for code in codes[:20]:
            stock = self.research.catalog.get(code)
            if stock and stock.get('asset_type') == 'index': continue
            stock = stock or {'code': code, 'name': code, 'exchange': '', 'unavailable': True}
            record = self.store.get('company_business', code, default={}); data = record.get('data') or {}
            status = record.get('status', 'unavailable')
            if data and self.clock() - record.get('fetched_at', 0) >= 7 * 86400: status = 'stale'
            relationships = self.store.get('company_relationships', code, default={})
            cards = curated_relationships(stock) + (relationships.get('data') or {}).get('cards', [])
            cards = list({card['id']: card for card in cards}.values())[:3]
            result.append({'code': code, 'name': stock['name'], 'exchange': stock['exchange'],
                           'relationships': cards,
                           'aliases': get_stock_aliases(stock['name'], {'code': code}),
                           'business': {'main_business': (data.get('main_business') or '')[:2400], 'source': data.get('source'),
                                        'url': data.get('url'), 'fetched_at': record.get('fetched_at'), 'status': status}})
        return result

    def article(self, topic):
        return {'id': topic['id'], 'title': topic['title'], 'content': topic.get('_content') or topic.get('summary') or '',
                'text_source': 'provider_news' if topic.get('_content') else 'topic_summary',
                'date_status': 'provider_timestamp' if topic.get('_content') else 'unverified_topic',
                'fragmented': topic.get('_content_truncated', False) if topic.get('_content') else not bool(topic.get('summary'))}

    def key(self, topic, stock, config=None):
        config = read_config() if config is None else config
        # Rank, heat and crawl time are not new event evidence; avoid repeat paid calls.
        evidence = {'article': self.article(topic), 'urls': sorted({s['url'] for s in topic['sources']}),
                    'sectors': self.sectors(topic)}
        return digest([VERSION, IMPACT_SYSTEM, evidence, stock, config.get('model'), config.get('base_url')])

    @staticmethod
    def sectors(topic):
        return [{key: row[key] for key in ('id', 'name', 'direction', 'reason', 'evidence')}
                for row in topic.get('sector_impacts', [])]

    def snapshot(self, topic_id, owner):
        topic = self.topic(topic_id); stocks = self.stocks(owner)
        config = read_config()
        records = self.store.get_many(NAMESPACE, [self.key(topic, stock, config) for stock in stocks], owner)
        return self._assemble(topic, stocks, records, config)

    def snapshots(self, topic_ids, owner):
        """One feed/watchlist read, no upstream collection or model invocation."""
        requested = set(topic_ids)
        topics = [row for row in self.board.feed()['items'] if row['id'] in requested and self.published(row)]
        stocks = self.stocks(owner); config = read_config()
        records = self.store.get_many(NAMESPACE, [self.key(topic, stock, config) for topic in topics for stock in stocks], owner)
        return {'items': [self._assemble(topic, stocks, records, config, preview=True) for topic in topics],
                'missing_ids': sorted(requested - {row['id'] for row in topics})}

    def _assemble(self, topic, stocks, records, config, preview=False):
        items = [{**records.get(self.key(topic, stock, config), {'status': 'pending', 'analysis': None}),
                  'code': stock['code'], 'name': stock['name'],
                  **({} if preview else {'business': stock['business']})} for stock in stocks]
        counts = Counter(row['status'] for row in items)
        return {'topic_id': topic['id'], 'title': topic['title'], 'summary': topic.get('summary') or '',
                'sector_impacts': topic.get('sector_impacts', []),
                'sources': topic['sources'], 'items': items, 'version': VERSION,
                'counts': {'total': len(items), 'completed': counts['ok'], 'pending': counts['pending'], 'failed': counts['failed'],
                           'related': sum(row['analysis'] is not None and row['analysis']['relation'] in ('direct', 'indirect') for row in items)},
                'note': '确认关联后给出−10至+10非零股价影响评分；为模型净影响判断，不代表涨跌幅或概率。'}

    def messages(self, topic, stocks):
        payload = {'news': self.article(topic), 'sources': [{'name': s['name'], 'url': s['url']} for s in topic['sources']],
                   'sector_hypotheses': self.sectors(topic), 'attention': topic.get('attention', {}),
                   'stocks': [{**stock, 'business_cards': retrieve_business_cards(topic['title'], topic.get('summary') or '', stock)} for stock in stocks]}
        return [{'role': 'system', 'content': IMPACT_SYSTEM}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]

    async def analyze(self, topic_id, owner, *, _topic=None, _codes=None, _refresh_business=True, _wait=False):
        topic = _topic if _topic is not None else await asyncio.to_thread(self.topic, topic_id)
        stocks = await asyncio.to_thread(self.stocks, owner)
        if not stocks: raise HTTPException(422, '请先添加自选股，再分析热点关联。')
        if _codes is not None and sorted(stock['code'] for stock in stocks) != sorted(_codes):
            raise HTTPException(409, '自选股已变化，请重新开始匹配。')
        try:
            if _wait: await self.requests.acquire()
            else: await asyncio.wait_for(self.requests.acquire(), timeout=.1)
        except TimeoutError:
            raise HTTPException(429, '热点关联分析繁忙，请稍后重试。') from None
        lease = digest([VERSION, owner, topic_id]); token = None
        try:
            token = await asyncio.to_thread(self.store.claim, lease, 600)
            if not token: raise HTTPException(409, '这条热点正在分析，请稍后读取结果。')
            if _refresh_business:
                await asyncio.gather(*(self.prepare_stock(stock) for stock in stocks), return_exceptions=True)
            stocks = await asyncio.to_thread(self.stocks, owner)
            if _codes is not None and sorted(stock['code'] for stock in stocks) != sorted(_codes):
                raise HTTPException(409, '自选股已变化，请重新开始匹配。')
            pending = []
            for stock in stocks:
                key = self.key(topic, stock)
                record = await asyncio.to_thread(self.store.get, NAMESPACE, key, owner)
                if not record or record['status'] != 'ok': pending.append(stock)

            async def batch(group):
                async with self.capacity:
                    try:
                        messages = self.messages(topic, group)
                        reply = await self.research.completion(messages, max_tokens=6000)
                        try:
                            analyses = validate_scores(reply['content'], self.article(topic), group)
                        except (ValueError, TypeError) as error:
                            # One repair of the output contract, never retries to obtain a desired score.
                            repair = messages + [{'role': 'assistant', 'content': reply['content']}, {'role': 'user', 'content':
                                '输出未通过校验：' + str(error)[:250] + '。请仅修复JSON字段、股票覆盖或逐字引文；逐条从输入中复制，不修改标点，不补充原文没有的词。分数仍由你独立判断，不以目标分数重试。'}]
                            reply = await self.research.completion(repair, max_tokens=6000)
                            analyses = validate_scores(reply['content'], self.article(topic), group)
                    except (HTTPException, ValueError, TypeError):
                        for stock in group:
                            await asyncio.to_thread(self.store.put, NAMESPACE, self.key(topic, stock), {
                                'status': 'failed', 'analysis': None, 'error': '模型或原文证据校验未完成，可重试；失败不代表无关。'}, owner)
                        return
                    for analysis in analyses:
                        stock = next(stock for stock in group if stock['code'] == analysis['code'])
                        await asyncio.to_thread(self.store.put, NAMESPACE, self.key(topic, stock), {
                            'status': 'ok', 'analysis': analysis, 'model': reply.get('model'),
                            'analyzed_at': datetime.fromtimestamp(self.clock(), SHANGHAI).isoformat()}, owner)
            await asyncio.gather(*(batch(pending[i:i + 5]) for i in range(0, len(pending), 5)))
            def result():
                config = read_config()
                records = self.store.get_many(NAMESPACE, [self.key(topic, stock, config) for stock in stocks], owner)
                return self._assemble(topic, stocks, records, config)
            return await asyncio.to_thread(result)
        finally:
            try:
                if token: await asyncio.to_thread(self.store.release, lease, token)
            finally:
                self.requests.release()

    async def prepare_stock(self, stock):
        await asyncio.gather(self.research.industry_news.business(stock),
                             self.research.industry_news.relationships(stock), return_exceptions=True)
