import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import authorize_model_request, current_session, token_hash, require_admin, authorize_admin_write
from .briefing import build_briefing
from .catalog import DEFAULT_WATCHLIST
from .recommendations import TOPICS
from .providers import read_config

router = APIRouter(prefix='/api')


def service(request: Request):
    return request.app.state.research


def private(response):
    response.headers['Cache-Control'] = 'no-store'


@router.get('/market/{code}/bundle')
async def market_bundle(code: str, request: Request, response: Response,
                        refresh: bool = False, research=Depends(service)):
    private(response)
    stock = research.catalog.get(code)
    if not stock:
        raise HTTPException(404, '未找到已核验的股票，请先搜索该股票。')
    return await request.app.state.market_bundle.get(stock, force=refresh)


@router.get('/market/{code}/daily')
async def daily_market(code: str, response: Response, refresh: bool = False, research=Depends(service)):
    private(response)
    stock = research.catalog.get(code)
    if not stock:
        raise HTTPException(404, '未找到已核验的股票，请先搜索该股票。')
    request_state = await research.daily_market.snapshot(stock, force=refresh)
    dashboard = await asyncio.to_thread(research.dashboard, code)
    return {**dashboard, 'daily_request': request_state}


@router.get('/market/{code}/minutes')
async def minute_market(code: str, request: Request, response: Response,
                        period: int = Query(default=1, ge=1, le=60),
                        refresh: bool = False, research=Depends(service)):
    private(response)
    if period not in {1, 5, 15, 30, 60}:
        raise HTTPException(422, '分钟周期只支持 1、5、15、30、60。')
    stock = research.catalog.get(code)
    if not stock:
        raise HTTPException(404, '未找到已核验的股票，请先搜索该股票。')
    return await request.app.state.minute_market.snapshot(stock, period, force=refresh)


@router.get('/market/sentiment')
async def market_sentiment(request: Request, response: Response, refresh: bool = False, research=Depends(service)):
    private(response)
    sentiment_service = getattr(research, 'market_sentiment', None)
    if not sentiment_service:
        from .market_sentiment import MarketSentimentService
        sentiment_service = MarketSentimentService()
        research.market_sentiment = sentiment_service
    return await sentiment_service.get_sentiment(force=refresh)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    days: int = Field(default=30, ge=1, le=90)
    max_articles: int = Field(default=6, ge=1, le=8)
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
    enabled: bool = True
    email: str = Field(default='', max_length=254)
    email_enabled: bool = True
    wechat_enabled: bool = True
    max_items: int = Field(default=10, ge=3, le=20)
    lookback_days: int = Field(default=7, ge=1, le=30)
    interests: list[str] = Field(default_factory=list, max_length=4)
    # None preserves an existing secret; empty explicitly clears it.
    pushplus_token: str | None = Field(default=None, max_length=200)

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        if value and not re.fullmatch(r'[^\s<>@\r\n]+@[^\s<>@\r\n]+\.[^\s<>@\r\n]+', value):
            raise ValueError('Invalid email')
        return value

    @field_validator('interests')
    @classmethod
    def valid_interests(cls, value):
        if len(value) != len(set(value)) or any(item not in TOPICS for item in value):
            raise ValueError('Invalid topics')
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
        await asyncio.to_thread(research.store.put, 'watchlist', 'list', data.codes, user['id'])
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
        codes = await asyncio.to_thread(research.store.get, 'watchlist', 'list', owner, DEFAULT_WATCHLIST)
        if data.code in codes:
            active = research.active.get((owner, data.code))
            return {'codes': codes, 'stock': stock, 'added': False, 'job': await asyncio.to_thread(research.store.get, 'job', active, owner) if active else None}
        if len(codes) >= 20:
            raise HTTPException(422, '最多关注 20 只股票，请先移除不需要的标的。')
        if len(research.active) >= research.max_pending:
            raise HTTPException(429, '任务队列已满，请稍后添加。')
        await asyncio.to_thread(research.store.db.check_rate_limit, [(token_hash('research:' + owner), 12)])
        # Persist both records before opening the paid work gate.
        job = await research.launch(data.code, user, start=False)
        try:
            await asyncio.to_thread(research.store.put, 'watchlist', 'list', [*codes, data.code], owner)
            research.job_starts[job['id']].set()
        except Exception:
            # The gate stays closed if saving the preference failed.
            task = research.job_tasks.get(job['id'])
            if task:
                task.cancel()
            research.active.pop((owner, data.code), None)
            job.update(status='failed', stage='自选股保存失败，未开始采集')
            await asyncio.to_thread(research.store.put, 'job', job['id'], job, owner)
            raise
        return {'codes': [*codes, data.code], 'stock': stock, 'added': True, 'job': job}


@router.post('/research/{code}/refresh', status_code=202)
async def refresh(code: str, data: RefreshRequest, response: Response, user=Depends(authorize_model_request), research=Depends(service)):
    private(response)
    if not research.catalog.get(code):
        raise HTTPException(404, '暂不支持该股票。')
    await asyncio.to_thread(research.store.db.check_rate_limit, [(token_hash('research:' + user['id']), 12)])
    return await research.launch(code, user, data.days, data.max_articles, data.include_community)


class AnalyzeAllRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


@router.post('/research/{code}/analyze-all', status_code=202)
async def analyze_all(code: str, data: AnalyzeAllRequest, response: Response, user=Depends(authorize_model_request), research=Depends(service)):
    private(response)
    if not research.catalog.get(code):
        raise HTTPException(404, '暂不支持该股票。')
    await asyncio.to_thread(research.store.db.check_rate_limit, [(token_hash('research:' + user['id']), 12)])
    return await research.launch(code, user, max_articles=0, analysis_only=True)


@router.get('/research/jobs/{job_id}')
def get_job(job_id: str, response: Response, session=Depends(current_session), research=Depends(service)):
    private(response)
    owner = session['user']['id']
    live = research.live_jobs.get(job_id)
    # Live progress avoids an extra remote DB read on every polling request.
    # Check the owner before exposing any live job or shared dashboard revision.
    if live and research.active.get((owner, live['code'])) == job_id:
        return {**live, 'data_revision': research.dashboard_revisions.get(live['code'])}
    job = research.store.get('job', job_id, owner)
    if not job:
        raise HTTPException(404, '任务不存在。')
    return job


class CommunityRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    days: int = Field(default=30, ge=1, le=90)


@router.post('/research/{code}/community', status_code=202)
async def refresh_community(code: str, data: CommunityRequest, response: Response, user=Depends(authorize_model_request), research=Depends(service)):
    private(response)
    if not research.catalog.get(code):
        raise HTTPException(404, '暂不支持该股票。')
    await asyncio.to_thread(research.store.db.check_rate_limit, [(token_hash('research:' + user['id']), 12)])
    return await research.launch(code, user, days=data.days, max_articles=0, community=True, community_only=True)


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
    await asyncio.to_thread(research.store.db.check_rate_limit, [(token_hash('analysis:' + user['id']), 30)])
    async with research.locks.setdefault(code, asyncio.Lock()):
        async with research.capacity:
            dashboard = await asyncio.to_thread(research.dashboard, code)
            item = next((row for row in dashboard['news'] if row['id'] == news_id), None)
            if not item:
                raise HTTPException(404, '新闻不存在或已更新，请刷新列表。')
            reply = await research.analyze_document({**dashboard['stock'], 'industry_profile': dashboard.get('industry_profile'), 'business_profile':dashboard.get('business_profile')}, item)
            item.update(analysis=reply['analysis'], score=reply['analysis']['sentiment_score'], analysis_status='completed', analyzed_at=reply['analyzed_at'], model=reply['model'], analysis_prompt_version=reply['prompt_version'], analysis_context_key=reply['context_key'])
            item.pop('analysis_error',None)
            if item.pop('refresh_pending',False):
                item['stale']=True
            from .research import forward_returns
            dashboard['backtest'] = forward_returns(dashboard['news'], dashboard['candles'])
            await asyncio.to_thread(research.store.put, 'dashboard', code, dashboard)
            return reply


@router.get('/briefing/preview')
def briefing_preview(response: Response, session=Depends(current_session), research=Depends(service)):
    private(response)
    return build_briefing(research.store, session['user']['id'])


@router.get('/briefing/history')
def briefing_history(response: Response, session=Depends(current_session), research=Depends(service)):
    private(response)
    rows = research.store.list('briefing', session['user']['id'], limit=100)
    return {'items': sorted([{'date': row['key'], 'generated_at': row['value']['generated_at'], 'count': len(row['value'].get('recommendations', []))} for row in rows if re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['key'])], key=lambda row: row['date'], reverse=True)}


@router.get('/briefing/latest')
def latest_briefing(response: Response, session=Depends(current_session), research=Depends(service)):
    private(response)
    return {'digest': research.store.get('briefing', 'latest', session['user']['id'])}


@router.post('/briefing/generate')
def generate_briefing(response: Response, user=Depends(authorize_model_request), research=Depends(service)):
    private(response)
    research.store.db.check_rate_limit([(token_hash('briefing:' + user['id']), 30)])
    return research.briefing.generate(user['id'])


@router.post('/briefing/test-delivery')
async def test_delivery(response: Response, user=Depends(authorize_model_request), research=Depends(service)):
    private(response)
    await asyncio.to_thread(research.store.db.check_rate_limit, [(token_hash('delivery-test:' + user['id']), 5)])
    subscription = await asyncio.to_thread(research.store.get, 'subscription', 'settings', user['id'], {})
    if not ((subscription.get('email') and subscription.get('email_enabled', True)) or (subscription.get('pushplus_token') and subscription.get('wechat_enabled', True))):
        raise HTTPException(422, '请先保存并开启至少一个推送渠道。')
    digest = await asyncio.to_thread(build_briefing, research.store, user['id'])
    return public_delivery(await research.briefing.deliver_digest(user['id'], digest, test=True))


@router.get('/briefing/history/{date}')
def saved_briefing(date: str, response: Response, session=Depends(current_session), research=Depends(service)):
    private(response)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        raise HTTPException(422, '日期格式应为 YYYY-MM-DD。')
    result = research.store.get('briefing', date, session['user']['id'])
    if not result:
        raise HTTPException(404, '当天尚未生成你的晨报。')
    return result


def public_delivery(record):
    if not record:
        return None
    return {key: value for key, value in record.items() if key != 'channels'} | {'channels': {channel: {key: state[key] for key in ('status', 'attempts', 'attempted_at') if key in state} for channel, state in record.get('channels', {}).items()}}


@router.get('/subscription')
def get_subscription(request: Request, response: Response, session=Depends(current_session), research=Depends(service)):
    private(response)
    value = research.store.get('subscription', 'settings', session['user']['id'], {})
    smtp = read_config().get('notifications', {}).get('smtp', {})
    schedule = research.briefing.status()
    return {'enabled': value.get('enabled', True), 'email': value.get('email', ''), 'email_enabled': value.get('email_enabled', True), 'wechat_enabled': value.get('wechat_enabled', True), 'max_items': value.get('max_items', 10), 'lookback_days': value.get('lookback_days', 7), 'interests': value.get('interests', []), 'pushplus_configured': bool(value.get('pushplus_token')), 'smtp_configured': all(smtp.get(key) for key in ('host', 'username', 'password', 'from_email')), 'scheduler_running': schedule['running'] and schedule['enabled'], 'morning_time': schedule['morning_time'], 'every_day': schedule['every_day'], 'next_run': schedule['next_run'], 'delivery': public_delivery(research.store.get('delivery', 'latest', session['user']['id']))}


@router.post('/subscription')
def set_subscription(data: Subscription, response: Response, user=Depends(authorize_model_request), research=Depends(service)):
    private(response)
    previous = research.store.get('subscription', 'settings', user['id'], {})
    token = previous.get('pushplus_token', '') if data.pushplus_token is None else data.pushplus_token.strip()
    value = {**data.model_dump(), 'pushplus_token': token}
    research.store.put('subscription', 'settings', value, user['id'])
    return {'enabled': value['enabled'], 'email': value['email'], 'pushplus_configured': bool(token)}


class ScheduleSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    morning_time: str = Field(default='08:30', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    every_day: bool = True
    lookback_days: int = Field(default=7, ge=1, le=30)
    max_articles: int = Field(default=6, ge=1, le=8)


@router.get('/admin/briefing/status')
def schedule_status(response: Response, user=Depends(require_admin), research=Depends(service)):
    private(response)
    return research.briefing.status()


@router.post('/admin/briefing/settings')
async def schedule_settings(data: ScheduleSettings, request: Request, response: Response, user=Depends(authorize_admin_write), research=Depends(service)):
    private(response)
    await asyncio.to_thread(research.store.put, 'scheduler', 'settings', data.model_dump())
    await research.briefing.reload_schedule()
    return {'saved': True}


@router.post('/admin/briefing/run', status_code=202)
async def run_daily(response: Response, user=Depends(authorize_admin_write), research=Depends(service)):
    private(response)
    await asyncio.to_thread(research.store.db.check_rate_limit, [(token_hash('daily-run:' + user['id']), 6)])
    return await research.briefing.launch()
