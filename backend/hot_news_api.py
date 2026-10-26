"""Public filtered hot-news snapshots and authenticated collection."""
import asyncio
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from .auth import authorize_model_request, current_session, token_hash
from .hot_news_board import public_board

router = APIRouter(prefix='/api/hot-news', tags=['Hot news'])


def service(request: Request):
    return request.app.state.hot_news


def impact_service(request: Request):
    return request.app.state.hot_stock_impact


@router.get('')
async def feed(response: Response,
               decision: Literal['all', 'keep', 'review', 'exclude'] = 'keep',
               source: Literal['all', 'baidu', 'weibo', 'tushare_cls', 'tushare_sina', 'tushare_wallstreetcn', 'tushare_major'] = 'all',
               channel: Literal['all', 'trade', 'supply', 'cost', 'demand', 'regulation', 'funding', 'risk'] = 'all',
               q: str = Query(default='', max_length=80),
               sort: Literal['hot', 'latest'] = 'hot',
               limit: int = Query(default=100, ge=1, le=100),
               news=Depends(service)):
    response.headers['Cache-Control'] = 'no-store'
    def read():
        return public_board(news.feed(), source=source, channel=channel, q=q, sort=sort, limit=limit)
    return await asyncio.to_thread(read)


@router.post('/refresh')
async def refresh(response: Response, user=Depends(authorize_model_request), news=Depends(service)):
    response.headers['Cache-Control'] = 'no-store'
    await asyncio.to_thread(news.store.db.check_rate_limit, [(token_hash('hot-feed:' + user['id']), 8)])
    data = await news.request_refresh()
    response.status_code = 202 if data.get('refresh', {}).get('status') == 'running' else 200
    return public_board(data)


@router.get('/analysis-batch')
async def analysis_batch(response: Response, ids: str = Query(max_length=7000),
                         session=Depends(current_session), impacts=Depends(impact_service)):
    response.headers['Cache-Control'] = 'no-store'
    topic_ids = list(dict.fromkeys(ids.split(',')))
    if len(topic_ids) > 100 or any(not re.fullmatch(r'[\w-]{1,64}', value, flags=re.ASCII) for value in topic_ids):
        raise HTTPException(422, '请选择至多100条有效热点。')
    return await asyncio.to_thread(impacts.snapshots, topic_ids, session['user']['id'])


@router.get('/{article_id}/analysis')
async def analysis(article_id: str, response: Response, session=Depends(current_session), impacts=Depends(impact_service)):
    response.headers['Cache-Control'] = 'no-store'
    return await asyncio.to_thread(impacts.snapshot, article_id, session['user']['id'])


@router.post('/{article_id}/analyze')
async def analyze(article_id: str, response: Response, user=Depends(authorize_model_request), impacts=Depends(impact_service)):
    response.headers['Cache-Control'] = 'no-store'
    await asyncio.to_thread(impacts.store.db.check_rate_limit, [(token_hash('hot-impact:' + user['id']), 8)])
    return await impacts.analyze(article_id, user['id'])
