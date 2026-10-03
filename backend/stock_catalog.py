"""Searchable exchange listings, cached in the shared research database."""
import asyncio
import re
import time
from datetime import datetime

from pypinyin import Style, lazy_pinyin

from . import providers
from .catalog import CATALOG
from .news_cleaning import SHANGHAI, STOCK_ENTITIES

BOARDS = {'sh': 'SH', 'star': 'SH', 'sz': 'SZ'}


class StockCatalog:
    def __init__(self, store):
        self.store = store
        self.boards = {board: store.get('stock_catalog', board) if store else None for board in BOARDS}
        self.errors = {}
        self.lock = asyncio.Lock()
        self.last_attempt = 0
        self._index()

    def _index(self):
        self.items = {stock['code']: {**stock, 'price': None, 'change': None} for stock in CATALOG}
        for record in self.boards.values():
            if record:
                self.items.update({stock['code']: stock for stock in record['items']})

    def get(self, code):
        return self.items.get(code)

    @staticmethod
    def normalize(rows, exchange):
        seeds = {stock['code']: stock for stock in CATALOG}
        items = {}
        for row in rows:
            code, name = str(row.get('code', '')).strip(), str(row.get('name', '')).strip()
            if not re.fullmatch(r'\d{6}', code) or not name or name.lower() in ('none', 'nan'):
                continue
            if not code.startswith(('6',) if exchange == 'SH' else ('0', '3')):
                continue
            industry = row.get('industry')
            if not isinstance(industry, str) or not industry.strip():
                industry = seeds.get(code, {}).get('industry', 'A 股')
            items[code] = {'code': code, 'name': name, 'initials': ''.join(lazy_pinyin(name, style=Style.FIRST_LETTER)).upper(), 'exchange': exchange, 'industry': industry, 'price': None, 'change': None}
        if not items:
            raise providers.ProviderError('股票列表未返回有效股票')
        return list(items.values())

    async def ensure(self):
        # Refresh boards independently; a failed exchange cannot erase cache.
        async with self.lock:
            now = time.time()
            due = [board for board, record in self.boards.items() if not record or now - record['fetched_at'] > 86400]
            if not due or now - self.last_attempt < 300:
                return
            self.last_attempt = now
            results = await asyncio.gather(*(providers.akshare_worker('catalog_' + board, '', '', '', retry=False) for board in due), return_exceptions=True)
            for board, result in zip(due, results):
                try:
                    if isinstance(result, Exception):
                        raise result
                    record = {'items': self.normalize(result['rows'], BOARDS[board]), 'source': 'AkShare / ' + ('深圳证券交易所' if board == 'sz' else '上海证券交易所'), 'fetched_at': now, 'as_of': datetime.now(SHANGHAI).isoformat()}
                except Exception as exc:
                    self.errors[board] = str(exc) if isinstance(exc, providers.ProviderError) else '股票列表解析失败'
                    continue
                self.store.put('stock_catalog', board, record)
                self.boards[board] = record
                self.errors.pop(board, None)
            self._index()

    def search(self, query='', limit=30, codes=None):
        query = query.strip().casefold()
        if codes is not None:
            items = [self.items[code] for code in codes if code in self.items]
        else:
            items = [stock for stock in self.items.values() if query in (stock['name'] + stock['code'] + stock['initials']).casefold()]
            items.sort(key=lambda stock: (stock['code'] != query, stock['name'].casefold() != query, stock['code']))
            items = items[:limit]
        return {'items': items, 'total': len(self.items), 'data_source': 'live', 'scope': '沪深 A 股（含创业板、科创板）', 'sources': {board: {'source': record['source'], 'as_of': record['as_of'], 'count': len(record['items'])} for board, record in self.boards.items() if record}, 'warnings': [f'{board}: {error}；保留已缓存股票' for board, error in self.errors.items()]}

    def entities(self, stock):
        aliases = STOCK_ENTITIES.get(stock['name'], {}).get('aliases', [])
        return {stock['name']: {'code': stock['code'], 'aliases': list(dict.fromkeys([stock['name'], stock['code'], *aliases]))}}
