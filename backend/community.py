"""Bounded public stock-board collection; page scripts are never executed."""
import asyncio
import hashlib
import json
import re
from collections import Counter

import httpx

from . import providers
from .news_cleaning import clean_text, metadata_date, normalize_url

MAX_POSTS, MIN_POSTS = 12, 5
BOARD = 'https://guba.eastmoney.com'


def embedded_json(page, name):
    match = re.search(r'\b(?:var|let|const)\s+' + re.escape(name) + r'\s*=\s*', page)
    if not match:
        raise ValueError('Missing public page data')
    value, _ = json.JSONDecoder().raw_decode(page[match.end():].lstrip())
    if not isinstance(value, dict):
        raise ValueError('Invalid public page data')
    return value


def post_id(value):
    text = str(value)
    return text if re.fullmatch(r'[1-9]\d{0,17}', text) else None


def count(value):
    return value if type(value) is int and value >= 0 else None


async def public_page(client, url):
    # URLs come from validated IDs, never arbitrary page links. No redirects,
    # login, cookies supplied by the user, or JavaScript execution are needed.
    response = await client.get(url)
    if response.status_code != 200:
        raise providers.ProviderError(f'公开股吧请求失败（HTTP {response.status_code}）')
    if len(response.content) > 2_000_000:
        raise providers.ProviderError('公开股吧页面超出采集大小限制')
    return response.text


def candidates(page, code, start, end):
    data = embedded_json(page, 'article_list')
    if str(data.get('bar_code')) != code or not isinstance(data.get('re'), list):
        raise ValueError('Stock board does not match')
    rows, seen, filtered = [], set(), Counter()
    for row in data['re'][:100]:
        if not isinstance(row, dict):
            filtered['invalid_row'] += 1
            continue
        # Type 0 is an ordinary discussion: exclude news, announcements,
        # videos and cross-board promoted articles from opinion samples.
        if row.get('post_type') != 0:
            filtered['not_user_post'] += 1
            continue
        if str(row.get('stockbar_code')) != code:
            filtered['wrong_board'] += 1
            continue
        identifier = post_id(row.get('post_id'))
        published = metadata_date(row.get('post_publish_time'))
        if not identifier:
            filtered['invalid_id'] += 1
        elif identifier in seen:
            filtered['duplicate'] += 1
        elif not published or not start <= str(published) <= end:
            filtered['missing_or_outside_date'] += 1
        else:
            seen.add(identifier)
            rows.append(row)
    rows.sort(key=lambda row: row['post_publish_time'], reverse=True)
    return rows[:MAX_POSTS], {'listed': len(data['re']), 'eligible': len(rows), 'detail_attempted': min(MAX_POSTS, len(rows)), 'filtered': dict(filtered)}


def detail_post(page, row, code, start, end):
    data = embedded_json(page, 'post_article')
    identifier = post_id(row['post_id'])
    if post_id(data.get('post_id')) != identifier or str((data.get('post_guba') or {}).get('stockbar_code')) != code:
        raise ValueError('Post identity or board does not match')
    if data.get('post_type') != 0 or data.get('post_state') != 0:
        raise ValueError('Post is not a visible discussion')
    published = metadata_date(data.get('post_publish_time'))
    if not published or not start <= str(published) <= end or published != metadata_date(row.get('post_publish_time')):
        raise ValueError('Post publication date is invalid')
    raw = data.get('post_content') or ''
    if not isinstance(raw, str) or not isinstance(data.get('post_title'), str):
        raise ValueError('Post text is invalid')
    raw = re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>', '', raw, flags=re.I | re.S)
    raw = re.sub(r'</(?:p|div)>|<br\s*/?>', '\n', raw, flags=re.I)
    text, _ = clean_text(raw)
    title, _ = clean_text(data['post_title'])
    if len(text.strip()) < 2:
        raise ValueError('Post body is unavailable')
    return {'id': hashlib.sha256(f'{code}:{identifier}'.encode()).hexdigest()[:16], 'url': f'{BOARD}/news,{code},{identifier}.html', 'title': title[:250], 'content': text[:800], 'date': str(published), 'published_at': data['post_publish_time'], 'weight': 1, 'source': '东方财富股吧公开帖子', 'text_source': 'post_body', 'date_status': 'source_verified', 'board_code': code, 'views': count(data.get('post_click_count')), 'replies': count(data.get('post_comment_count')), 'likes': count(data.get('post_like_count'))}


async def direct_posts(stock, start, end):
    code = stock['code']
    if not re.fullmatch(r'\d{6}', code):
        raise ValueError('Invalid stock code')
    async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=5), follow_redirects=False, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html'}) as client:
        listing = await public_page(client, f'{BOARD}/list,{code}.html')
        rows, diagnostics = candidates(listing, code, start, end)
        capacity = asyncio.Semaphore(3)
        async def fetch(row):
            async with capacity:
                try:
                    page = await public_page(client, f"{BOARD}/news,{code},{post_id(row['post_id'])}.html")
                    return detail_post(page, row, code, start, end)
                except (providers.ProviderError, httpx.RequestError, ValueError, TypeError):
                    return None
        results = await asyncio.gather(*(fetch(row) for row in rows))
    posts = [post for post in results if post is not None]
    diagnostics.update(retained=len(posts), detail_failed=len(rows) - len(posts))
    return posts, diagnostics


async def search_posts(stock, start, end):
    response = await providers.tavily('search', {'query': f'{stock["name"]} {stock["code"]} 股吧 雪球 股民讨论', 'topic': 'general', 'search_depth': 'advanced', 'max_results': 20, 'start_date': start, 'end_date': end, 'include_domains': ['guba.eastmoney.com', 'xueqiu.com'], 'include_answer': False})
    posts, seen, filtered = [], set(), Counter()
    for row in response['results']:
        url = normalize_url(row.get('url', ''))
        guba = re.fullmatch(r'https?://guba\.eastmoney\.com/news,(\d{6}),([1-9]\d*)\.html', url)
        snowball = re.fullmatch(r'https?://xueqiu\.com/\d+/\d+', url)
        if not (guba and guba[1] == stock['code'] or snowball):
            filtered['not_individual_post'] += 1
            continue
        published = metadata_date(row.get('published_date'))
        if not published or not start <= str(published) <= end:
            filtered['missing_or_outside_date'] += 1
            continue
        text, _ = clean_text(row.get('content', ''), row.get('title', ''))
        related = stock['name'] in row.get('title', '') + text or stock['code'] in row.get('title', '') + text
        if not related or len(text) < 2:
            filtered['unrelated_or_empty'] += 1
            continue
        if url in seen:
            filtered['duplicate'] += 1
            continue
        seen.add(url)
        posts.append({'id': hashlib.sha256(url.encode()).hexdigest()[:16], 'url': url, 'title': row.get('title', ''), 'content': text[:800], 'date': str(published), 'weight': 1, 'source': 'Tavily 社区检索备用', 'text_source': 'search_fragments', 'date_status': 'metadata_only'})
    return posts[:MAX_POSTS], {'listed': len(response['results']), 'retained': len(posts[:MAX_POSTS]), 'filtered': dict(filtered)}


async def collect_posts(stock, start, end):
    posts, diagnostics, warnings = [], {}, []
    try:
        posts, diagnostics['direct'] = await direct_posts(stock, start, end)
    except (providers.ProviderError, httpx.RequestError, ValueError, TypeError) as exc:
        diagnostics['direct'] = {'status': 'error', 'error_type': type(exc).__name__, 'retained': 0}
        warnings.append('东方财富公开帖子采集失败，尝试备用检索')
    source = 'eastmoney_direct'
    if len(posts) < MIN_POSTS:
        try:
            fallback, diagnostics['tavily'] = await search_posts(stock, start, end)
            seen = {post['url'] for post in posts}
            extra = [post for post in fallback if post['url'] not in seen]
            posts = (posts + extra)[:MAX_POSTS]
            if extra:
                source = 'mixed' if seen else 'tavily_search'
        except (providers.ProviderError, httpx.RequestError, ValueError, TypeError) as exc:
            diagnostics['tavily'] = {'status': 'error', 'error_type': type(exc).__name__, 'retained': 0}
            warnings.append('备用社区检索失败')
    diagnostics.update(selected=len(posts), minimum=MIN_POSTS, maximum=MAX_POSTS)
    return {'posts': posts, 'source': source, 'diagnostics': diagnostics, 'warnings': warnings}
