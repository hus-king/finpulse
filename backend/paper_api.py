import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import authorize_model_request, current_session

router = APIRouter(prefix='/api/paper', tags=['paper trading'])


def paper_service(request: Request):
    return request.app.state.paper_trading


async def paper_error_handler(request, exc):
    return JSONResponse({'detail': exc.message, 'code': exc.code}, status_code=exc.status,
                        headers={'Cache-Control': 'no-store'})


class PaperTradeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    code: str = Field(pattern=r'^\d{6}$')
    side: Literal['buy', 'sell']
    quantity: int = Field(strict=True, gt=0, le=1_000_000)


@router.get('/account')
async def account(response: Response, session=Depends(current_session), service=Depends(paper_service)):
    response.headers['Cache-Control'] = 'no-store'
    return await asyncio.to_thread(service.account, session['user']['id'])


@router.get('/quote/{code}')
async def quote(code: str, response: Response, refresh: bool = False,
                session=Depends(current_session), service=Depends(paper_service)):
    response.headers['Cache-Control'] = 'no-store'
    return await service.quote(code, refresh)


@router.get('/trades')
async def trades(response: Response, limit: int = Query(default=20, ge=1, le=100),
                 before_seq: int | None = Query(default=None, ge=1),
                 session=Depends(current_session), service=Depends(paper_service)):
    response.headers['Cache-Control'] = 'no-store'
    return await asyncio.to_thread(service.trades, session['user']['id'], limit, before_seq)


@router.post('/trades')
async def submit(data: PaperTradeRequest, response: Response,
                 user=Depends(authorize_model_request), service=Depends(paper_service)):
    response.headers['Cache-Control'] = 'no-store'
    return await service.submit(user['id'], str(data.request_id), data.code, data.side, data.quantity)
