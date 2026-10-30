"""Public trend boards, without account cookies or signed browser sessions."""
from urllib.parse import quote
import httpx
from .hot_news_limits import PER_SOURCE_LIMIT
from .providers import ProviderError

PLATFORMS = {
    'bilibili': {'name': 'Bilibili热搜', 'url': 'https://search.bilibili.com/all',
                 'endpoint': 'https://api.bilibili.com/x/web-interface/wbi/search/square?limit=20'},
    'zhihu': {'name': '知乎热榜', 'url': 'https://www.zhihu.com/hot',
              'endpoint': 'https://api.zhihu.com/topstory/hot-lists/total?limit=20'},
    'toutiao': {'name': '今日头条热榜', 'url': 'https://www.toutiao.com/',
                'endpoint': 'https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc'},
    'douyin': {'name': '抖音热榜', 'url': 'https://www.douyin.com/hot',
               'endpoint': 'https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/'},
}


def parse_platform(source, payload):
    if not isinstance(payload, dict): raise ProviderError('热榜响应格式异常')
    if payload.get('code', payload.get('status_code', 0)) != 0:
        raise ProviderError('热榜接口暂不可用')
    try:
        rows = (payload['data']['trending']['list'] if source == 'bilibili' else
                payload['word_list'] if source == 'douyin' else payload['data'])
        if not isinstance(rows, list): raise TypeError()
    except (KeyError, TypeError):
        raise ProviderError('热榜数据结构已变化') from None
    result = []
    for rank, row in enumerate(rows, 1):
        if not isinstance(row, dict): continue
        target = row.get('target') or {}
        if not isinstance(target, dict): continue
        title = row.get('keyword') or row.get('Title') or row.get('word') or target.get('title')
        if not isinstance(title, str) or not title.strip(): continue
        title = title.strip()[:200]
        summary = target.get('excerpt') or ''
        if source == 'bilibili':
            url = 'https://search.bilibili.com/all?keyword=' + quote(title)
            heat = row.get('heat_score')
        elif source == 'zhihu':
            identity = str(target.get('id') or '').strip()
            if not identity.isdigit(): continue
            url = 'https://www.zhihu.com/question/' + identity
            heat = row.get('detail_text')
        elif source == 'toutiao':
            identity = str(row.get('ClusterIdStr') or row.get('ClusterId') or '').strip()
            if not identity.isdigit(): continue
            url = 'https://www.toutiao.com/trending/' + identity + '/'
            heat = row.get('HotValue')
        else:
            url = 'https://www.douyin.com/search/' + quote(title)
            heat = row.get('hot_value')
        result.append({'title': title, 'summary': summary[:1000] if isinstance(summary, str) else '',
                       'rank': rank, 'heat': str(heat)[:50] if heat is not None else None, 'url': url})
        if len(result) == PER_SOURCE_LIMIT: break
    if not result: raise ProviderError('热榜未返回可解析的话题')
    return result


async def fetch_platform(source):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=6), follow_redirects=False,
                                     headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}) as client:
            response = await client.get(PLATFORMS[source]['endpoint'])
        if response.status_code != 200:
            raise ProviderError(PLATFORMS[source]['name'] + f'暂不可读取（HTTP {response.status_code}）')
        if len(response.content) > 2_000_000: raise ProviderError('热榜响应过大')
        return parse_platform(source, response.json())
    except (httpx.HTTPError, ValueError):
        raise ProviderError(PLATFORMS[source]['name'] + '网络或响应暂不可用') from None
