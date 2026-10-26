"""Current hot topics -> account watchlist business impact, with source retrieval."""
import asyncio
from collections import Counter
from datetime import datetime
import json
import re
import time

from fastapi import HTTPException

from .catalog import DEFAULT_WATCHLIST
from .hot_news import SYSTEM, validate_evidence
from .hot_news_board import digest
from .news_cleaning import SHANGHAI, get_stock_aliases
from .providers import read_config

VERSION = 'hot-stock-business-impact-v1'
NAMESPACE = 'hot_stock_impact'
IMPACT_SYSTEM = SYSTEM + '\n当前输入是热榜标题/摘要，不是已核实新闻正文；source链接为平台话题入口。检索卡片是主营原文，matched_terms只用于定位，不代表关联成立。只能用提供的业务资料判断角色，不能从相似词猜测合同、客户或实际敞口。不能仅凭股价涨跌或媒体语气判断业务利好利空；没有新的业务影响依据时给影响不明或待核实。只判断业务利好利空及依据。sector_hypotheses是板块层面的候选路径，需要逐股独立核对主营原文，不能照搬板块方向，也不能漏掉板块候选之外实际有关的股票。'


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
        if topic is None:
            raise HTTPException(404, '该热点已不在当前榜单，请更新热点后重试。')
        return topic

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
            result.append({'code': code, 'name': stock['name'], 'exchange': stock['exchange'],
                           'aliases': get_stock_aliases(stock['name'], {'code': code}),
                           'business': {'main_business': (data.get('main_business') or '')[:2400], 'source': data.get('source'),
                                        'url': data.get('url'), 'fetched_at': record.get('fetched_at'), 'status': status}})
        return result

    def article(self, topic):
        return {'id': topic['id'], 'title': topic['title'], 'content': topic.get('summary') or '',
                'text_source': 'topic_summary', 'date_status': 'unverified_topic', 'fragmented': not bool(topic.get('summary'))}

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
        topics = [row for row in self.board.feed()['items'] if row['id'] in requested]
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
                'note': '依据热榜标题/摘要与主营业务资料判断可能的业务影响；摘要尚未独立核实。'}

    def messages(self, topic, stocks):
        payload = {'news': self.article(topic), 'sources': [{'name': s['name'], 'url': s['url']} for s in topic['sources']],
                   'sector_hypotheses': self.sectors(topic),
                   'stocks': [{**stock, 'business_cards': retrieve_business_cards(topic['title'], topic.get('summary') or '', stock)} for stock in stocks]}
        return [{'role': 'system', 'content': IMPACT_SYSTEM}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]

    async def analyze(self, topic_id, owner):
        topic = await asyncio.to_thread(self.topic, topic_id)
        stocks = await asyncio.to_thread(self.stocks, owner)
        if not stocks: raise HTTPException(422, '请先添加自选股，再分析热点关联。')
        try:
            await asyncio.wait_for(self.requests.acquire(), timeout=.1)
        except TimeoutError:
            raise HTTPException(429, '热点关联分析繁忙，请稍后重试。') from None
        lease = digest([VERSION, owner, topic_id]); token = None
        try:
            token = await asyncio.to_thread(self.store.claim, lease, 600)
            if not token: raise HTTPException(409, '这条热点正在分析，请稍后读取结果。')
            await asyncio.gather(*(self.research.industry_news.business(stock) for stock in stocks), return_exceptions=True)
            stocks = await asyncio.to_thread(self.stocks, owner)
            pending = []
            for stock in stocks:
                key = self.key(topic, stock)
                record = await asyncio.to_thread(self.store.get, NAMESPACE, key, owner)
                if not record or record['status'] != 'ok': pending.append(stock)

            async def batch(group):
                async with self.capacity:
                    try:
                        reply = await self.research.completion(self.messages(topic, group), max_tokens=6000)
                        analyses = validate_evidence(reply['content'], self.article(topic), group)
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
            return await asyncio.to_thread(self.snapshot, topic_id, owner)
        finally:
            try:
                if token: await asyncio.to_thread(self.store.release, lease, token)
            finally:
                self.requests.release()
