import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import authorize_model_request, current_session, token_hash
from .briefing import build_briefing
from .catalog import DEFAULT_WATCHLIST

router = APIRouter(prefix='/api')


def service(request: Request):
    return request.app.state.research


def private(response):
    response.headers['Cache-Control'] = 'no-store'


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    days: int = Field(default=30, ge=1, le=90)
    max_articles: int = Field(default=3, ge=1, le=8)
    include_community: bool = False


class WatchlistRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    codes: list[str] = Field(max_length=20)

    @field_validator('codes')
    @classmethod
    def supported(cls, value):
        if len(set(value)) != len(value) or any(not re.fullmatch(r'\d{6}', code) for code in value):
            raise ValueError('Unknown or duplicate stock codes')
        return value


class Subscription(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    email: str = Field(default='', max_length=254)
    # None preserves an existing secret; empty explicitly clears it.
    pushplus_token: str | None = Field(default=None, max_length=200)

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        if value and not re.fullmatch(r'[^\s<>@\r\n]+@[^\s<>@\r\n]+\.[^\s<>@\r\n]+', value):
            raise ValueError('Invalid email')
        return value


@router.get('/watchlist')
def watchlist(response: Response, session=Depends(current_session), research=Depends(service)):
    private(response)
    owner = session['user']['id']
    codes = research.store.get('watchlist', 'list', owner, DEFAULT_WATCHLIST)
    jobs = [research.store.get('job', job_id, owner) for (user_id, code), job_id in list(research.active.items()) if user_id == owner and code in codes]
    return {'codes': codes, 'items': research.catalog.search(codes=codes)['items'], 'jobs': [job for job in jobs if job]}


@router.post('/watchlist')
async def save_watchlist(data: WatchlistRequest, response: Response, user=Depends(authorize_model_request), research=Depends(service)):
    private(response)
    if any(not research.catalog.get(code) for code in data.codes):
        raise HTTPException(422, '股票代码不在已核验的股票库中，请先搜索该股票。')
    async with research.watch_locks.setdefault(user['id'], asyncio.Lock()):
        research.store.put('watchlist', 'list', data.codes, user['id'])
    return {'codes': data.codes}


class AddStockRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(pattern=r'^\d{6}$')


@router.post('/watchlist/add', status_code=202)
async def add_stock(data: AddStockRequest, response: Response, user=Depends(authorize_model_request), research=Depends(service)):
    private(response)
    if not research.catalog.get(data.code):
        await research.catalog.ensure()
    stock = research.catalog.get(data.code)
    if not stock:
        raise HTTPException(422, '未找到已核验的沪深 A 股。请先搜索，或等待股票列表数据源恢复。')
    owner = user['id']
    async with research.watch_locks.setdefault(owner, asyncio.Lock()):
        codes = research.store.get('watchlist', 'list', owner, DEFAULT_WATCHLIST)
        if data.code in codes:
            active = research.active.get((owner, data.code))
            return {'codes': codes, 'stock': stock, 'added': False, 'job': research.store.get('job', active, owner) if active else None}
        if len(codes) >= 20:
            raise HTTPException(422, '最多关注 20 只股票，请先移除不需要的标的。')
        if len(research.active) >= 8:
            raise HTTPException(429, '任务队列已满，请稍后添加。')
        research.store.db.check_rate_limit([(token_hash('research:' + owner), 12)])
        # Schedule paid work only after both synchronous database writes finish.
        job = research.launch(data.code, user)
        try:
            research.store.put('watchlist', 'list', [*codes, data.code], owner)
        except Exception:
            # launch schedules work on the next event-loop turn. Undo it here
            # before yielding if saving the preference failed.
            task = research.job_tasks.get(job['id'])
            if task:
                task.cancel()
            research.active.pop((owner, data.code), None)
            job.update(status='failed', stage='自选股保存失败，未开始采集')
            research.store.put('job', job['id'], job, owner)
            raise
        return {'codes': [*codes, data.code], 'stock': stock, 'added': True, 'job': job}


@router.post('/research/{code}/refresh', status_code=202)
async def refresh(code: str, data: RefreshRequest, response: Response, user=Depends(authorize_model_request), research=Depends(service)):
    private(response)
    if not research.catalog.get(code):
        raise HTTPException(404, '暂不支持该股票。')
    research.store.db.check_rate_limit([(token_hash('research:' + user['id']), 12)])
    return research.launch(code, user, data.days, data.max_articles, data.include_community)


@router.get('/research/jobs/{job_id}')
def get_job(job_id: str, response: Response, session=Depends(current_session), research=Depends(service)):
    private(response)
    job = research.store.get('job', job_id, session['user']['id'])
    if not job:
        raise HTTPException(404, '任务不存在。')
    return job


@router.get('/research/{code}/audit')
def cleaning_audit(code: str, response: Response, session=Depends(current_session), research=Depends(service)):
    private(response)
    dashboard = research.dashboard(code)
    collection = (dashboard.get('pipeline') or {}).get('collection_id')
    if not collection:
        return {'items': [], 'summary': {}, 'date_range': []}
    record = research.store.get('collection', collection)
    # Share reasons and cleaned articles, not raw provider response bodies.
    return record['cleaning']


@router.post('/news/{code}/{news_id}/analyze')
async def analyze_saved(code: str, news_id: str, response: Response, user=Depends(authorize_model_request), research=Depends(service)):
    private(response)
    research.store.db.check_rate_limit([(token_hash('analysis:' + user['id']), 30)])
    async with research.capacity:
        async with research.locks.setdefault(code, asyncio.Lock()):
            dashboard = research.dashboard(code)
            item = next((row for row in dashboard['news'] if row['id'] == news_id), None)
            if not item:
                raise HTTPException(404, '新闻不存在或已更新，请刷新列表。')
            reply = await research.analyze_document(dashboard['stock'], item)
            item.update(analysis=reply['analysis'], score=reply['analysis']['sentiment_score'], analysis_status='completed', analyzed_at=reply['analyzed_at'], model=reply['model'])
            from .research import forward_returns
            dashboard['backtest'] = forward_returns(dashboard['news'], dashboard['candles'])
            research.store.put('dashboard', code, dashboard)
            return reply


@router.get('/briefing/preview')
def briefing_preview(response: Response, session=Depends(current_session), research=Depends(service)):
    private(response)
    return build_briefing(research.store, session['user']['id'])


@router.get('/subscription')
def get_subscription(request: Request, response: Response, session=Depends(current_session), research=Depends(service)):
    private(response)
    value = research.store.get('subscription', 'settings', session['user']['id'], {})
    return {'enabled': value.get('enabled', False), 'email': value.get('email', ''), 'pushplus_configured': bool(value.get('pushplus_token')), 'scheduler_running': bool(request.app.state.scheduler), 'delivery': research.store.get('delivery', 'latest', session['user']['id'])}


@router.post('/subscription')
def set_subscription(data: Subscription, response: Response, user=Depends(authorize_model_request), research=Depends(service)):
    private(response)
    previous = research.store.get('subscription', 'settings', user['id'], {})
    token = previous.get('pushplus_token', '') if data.pushplus_token is None else data.pushplus_token.strip()
    if data.enabled and not data.email and not token:
        raise HTTPException(422, '启用订阅需要邮箱或 PushPlus token。')
    value = {**data.model_dump(), 'pushplus_token': token}
    research.store.put('subscription', 'settings', value, user['id'])
    return {'enabled': value['enabled'], 'email': value['email'], 'pushplus_configured': bool(token)}
