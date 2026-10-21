import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from ..auth import current_session, authorize_model_request

router=APIRouter(prefix='/api/paper',tags=['paper algorithms'])


def quant(request:Request): return request.app.state.quant


class AutomationRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:Literal['manual','algorithm']
    strategy_id:str | None=Field(default=None,max_length=80)
    expected_revision:int=Field(strict=True,ge=0)
    strategy_revision:int | None=Field(default=None,strict=True,ge=1)


@router.get('/strategies')
async def strategies(response:Response,session=Depends(current_session),service=Depends(quant)):
    response.headers['Cache-Control']='no-store'
    return {'items':await asyncio.to_thread(service.strategies,session['user']['id'])}


@router.get('/automation')
async def state(response:Response,session=Depends(current_session),service=Depends(quant)):
    response.headers['Cache-Control']='no-store'
    return await asyncio.to_thread(service.state,session['user']['id'])


@router.put('/automation')
async def configure(data:AutomationRequest,response:Response,user=Depends(authorize_model_request),service=Depends(quant)):
    response.headers['Cache-Control']='no-store'
    await asyncio.to_thread(service.configure,user['id'],data.mode,data.strategy_id,data.expected_revision,data.strategy_revision)
    return await asyncio.to_thread(service.state,user['id'])


@router.get('/automation/decisions')
async def decisions(response:Response,before:str | None=Query(default=None,max_length=80),limit:int=Query(default=20,ge=1,le=100),session=Depends(current_session),service=Depends(quant)):
    response.headers['Cache-Control']='no-store'
    return await asyncio.to_thread(service.store.decisions,session['user']['id'],before,limit)
