"""Bounded Tushare-compatible news reads; credentials remain server-side."""
from datetime import datetime, timedelta
import json
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from .providers import ProviderError, read_config
from .news_cleaning import SHANGHAI, clean_text
from .hot_news_events import rule_event

NEWS_SOURCES = {
    'tushare_cls': {'name': '财联社快讯', 'url': 'https://www.cls.cn/telegraph', 'api': 'news', 'src': 'cls'},
    'tushare_sina': {'name': '新浪新闻快讯', 'url': 'https://finance.sina.com.cn/7x24/', 'api': 'news', 'src': 'sina'},
    'tushare_wallstreetcn': {'name': '华尔街见闻快讯', 'url': 'https://wallstreetcn.com/live/global', 'api': 'news', 'src': 'wallstreetcn'},
    'tushare_major': {'name': '新闻通讯', 'url': 'https://tushare.pro/document/2?doc_id=195', 'api': 'major_news', 'src': None},
}
PUBLISHER_URLS = {'新浪财经': 'https://finance.sina.com.cn/', '财联社': 'https://www.cls.cn/',
                  '华尔街见闻': 'https://wallstreetcn.com/', '新华网': 'https://www.news.cn/',
                  '第一财经': 'https://www.yicai.com/', '同花顺': 'https://news.10jqka.com.cn/',
                  '凤凰财经': 'https://finance.ifeng.com/', '中证网': 'https://www.cs.com.cn/',
                  '财新网': 'https://www.caixin.com/'}


def settings():
    cfg = read_config().get('tushare_news') or {}
    if isinstance(cfg, dict) and cfg.get('token'): return cfg
    private = (Path(__file__).resolve().parents[1] / 'config.local.json').resolve().parent / 'news-provider.local.json'
    try:
        cfg = json.loads(private.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def configured_sources():
    cfg = settings()
    if not cfg.get('token') or not cfg.get('base_url'): return {}
    return {key: {'name': row['name'], 'url': row['url'], 'kind': 'news'} for key, row in NEWS_SOURCES.items()}


def parse_news(payload, source, now):
    if not isinstance(payload, dict) or payload.get('code') != 0:
        code = payload.get('code') if isinstance(payload, dict) else None
        raise ProviderError('新闻服务权限不可用或订阅已到期，请检查服务端配置。' if code == 2002 else '新闻服务暂未返回有效数据，请稍后重试。')
    data = payload.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('fields'), list) or not isinstance(data.get('items'), list):
        raise ProviderError('新闻服务响应格式异常。')
    if not {'title', 'content'}.issubset(data['fields']): raise ProviderError('新闻服务缺少标题或正文。')
    rows = []
    for values in data['items'][:1500]:
        if not isinstance(values, list) or len(values) != len(data['fields']): continue
        raw = dict(zip(data['fields'], values))
        if not isinstance(raw.get('title'), str) or not isinstance(raw.get('content'), str): continue
        title, _ = clean_text(raw['title']); content, _ = clean_text(raw['content'])
        timestamp = raw.get('datetime') or raw.get('pub_time')
        try:
            published = datetime.fromisoformat(str(timestamp))
            published = published.astimezone(SHANGHAI) if published.tzinfo else published.replace(tzinfo=SHANGHAI)
        except (ValueError, TypeError): continue
        age = (now - published).total_seconds()
        if not title or not content or not -300 <= age < 86400: continue
        publisher = str(raw.get('src') or NEWS_SOURCES[source]['name'])[:80]
        source_url = PUBLISHER_URLS.get(publisher, NEWS_SOURCES[source]['url'])
        rows.append({'title': title[:200], 'summary': content[:1000], 'content': content[:6000],
                     'content_truncated': len(content) > 6000, 'rank': None, 'heat': None, 'kind': 'news',
                     'url': source_url, 'link_kind': 'provider_doc' if source_url.startswith('https://tushare.pro/') else 'source',
                     'publisher': publisher,
                     'published_at': published.isoformat(), 'published_timestamp': published.timestamp()})
    # Prefer concrete event candidates, then newest reports. This is not a heat ranking.
    return sorted(rows, key=lambda row: (not bool(rule_event(row)), -row['published_timestamp']))[:15]


async def fetch_news(source, now=None):
    now = now or datetime.now(SHANGHAI)
    cfg = settings(); url = str(cfg.get('base_url') or '').rstrip('/')
    address = urlsplit(url)
    if address.scheme not in ('http', 'https') or not address.netloc or address.username or address.password or address.query or address.fragment:
        raise ProviderError('新闻服务地址配置不合法。')
    if not cfg.get('token'): raise ProviderError('新闻服务未配置。')
    origin = NEWS_SOURCES[source]
    params = {'start_date': (now - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
              'end_date': now.strftime('%Y-%m-%d %H:%M:%S'), 'limit': 100}
    if origin['src']: params['src'] = origin['src']
    fields = 'datetime,title,content,channels' if origin['api'] == 'news' else 'title,content,pub_time,src'
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=6), follow_redirects=False) as client:
            response = await client.post(url + '/', json={'api_name': origin['api'], 'token': cfg['token'], 'params': params, 'fields': fields})
        if response.status_code != 200 or len(response.content) > 4_000_000:
            raise ProviderError('新闻服务暂不可用，请稍后重试。')
        return parse_news(response.json(), source, now)
    except (httpx.HTTPError, ValueError, ImportError):
        # Never echo upstream bodies, URLs with credentials, or request payloads.
        raise ProviderError('新闻服务网络或响应暂不可用。') from None
