"""Customer submission contract and bounded request handling."""
import asyncio
import json

from fastapi import APIRouter, Depends, Path, Response
from pydantic import Field
from starlette.responses import JSONResponse

from ..auth import authorize_model_request,current_session
from .api import quant
from .rules import MAX_PACKAGE_BYTES, RulePackage, RuleStrategy, StrictModel, normalize_package, submission_schema

router=APIRouter(prefix='/api/paper',tags=['customer rule strategies'])


class StrategyBodyLimitMiddleware:
    def __init__(self,app):self.app=app

    async def __call__(self,scope,receive,send):
        path=scope.get('path','')
        applies=scope['type']=='http' and scope.get('method') in ('POST','PUT','DELETE') and (path=='/api/paper/strategies' or path.startswith('/api/paper/strategies/'))
        if not applies:return await self.app(scope,receive,send)
        chunks=[];size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            chunk=message.get('body',b'');size+=len(chunk)
            if size>MAX_PACKAGE_BYTES:
                return await JSONResponse({'detail':'策略提交请求不能超过64KiB。','code':'STRATEGY_TOO_LARGE'},status_code=413,headers={'Cache-Control':'no-store'})(scope,receive,send)
            chunks.append(chunk)
            if not message.get('more_body',False):break
        raw=b''.join(chunks)
        # Bound nesting before json.loads/Pydantic, respecting quoted braces.
        depth=0;quoted=False;escaped=False
        for value in raw:
            if quoted:
                if escaped:escaped=False
                elif value==92:escaped=True
                elif value==34:quoted=False
            elif value==34:quoted=True
            elif value in (91,123):
                depth+=1
                if depth>32:
                    return await JSONResponse({'detail':'JSON嵌套过深。','code':'STRATEGY_INVALID'},status_code=422,headers={'Cache-Control':'no-store'})(scope,receive,send)
            elif value in (93,125):depth-=1
        def unique_keys(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError('duplicate key')
                result[key]=value
            return result
        def invalid_constant(value):raise ValueError('non-finite JSON number')
        try:json.loads(raw,object_pairs_hook=unique_keys,parse_constant=invalid_constant)
        except (ValueError,UnicodeError,RecursionError):
            return await JSONResponse({'detail':'JSON格式无效；不允许重复字段、NaN或Infinity。','code':'STRATEGY_INVALID'},status_code=422,headers={'Cache-Control':'no-store'})(scope,receive,send)
        delivered=False
        async def buffered():
            nonlocal delivered
            if not delivered:delivered=True;return {'type':'http.request','body':raw,'more_body':False}
            return await receive()
        return await self.app(scope,buffered,send)


class Submission(StrictModel):
    strategy:RulePackage


class UpdateSubmission(Submission):
    expected_revision:int=Field(strict=True,ge=1)


class Revision(StrictModel):
    expected_revision:int=Field(strict=True,ge=1)


async def writer(user=Depends(authorize_model_request),service=Depends(quant)):
    await asyncio.to_thread(service.users.consume_write,user['id'])
    return user


@router.get('/strategy-schema')
async def schema(response:Response):
    response.headers['Cache-Control']='public, max-age=3600'
    return submission_schema()


@router.post('/strategies/validate')
async def validate(data:Submission,response:Response,user=Depends(writer)):
    response.headers['Cache-Control']='no-store'
    package,digest=normalize_package(data.strategy.model_dump(mode='json'))
    return {'valid':True,'strategy':package,'digest':digest,'summary':RuleStrategy(package,'preview',1,digest).metadata()}


@router.post('/strategies',status_code=201)
async def create(data:Submission,response:Response,user=Depends(writer),service=Depends(quant)):
    response.headers['Cache-Control']='no-store'
    return await asyncio.to_thread(service.users.create,user['id'],data.strategy.model_dump(mode='json'))


@router.get('/strategies/{strategy_id}')
async def get(response:Response,strategy_id:str=Path(max_length=80),session=Depends(current_session),service=Depends(quant)):
    response.headers['Cache-Control']='no-store'
    return await asyncio.to_thread(service.users.get,session['user']['id'],strategy_id)


@router.put('/strategies/{strategy_id}')
async def update(data:UpdateSubmission,response:Response,strategy_id:str=Path(max_length=80),user=Depends(writer),service=Depends(quant)):
    response.headers['Cache-Control']='no-store'
    return await asyncio.to_thread(service.users.update,user['id'],strategy_id,data.strategy.model_dump(mode='json'),data.expected_revision)


@router.delete('/strategies/{strategy_id}')
async def delete(data:Revision,response:Response,strategy_id:str=Path(max_length=80),user=Depends(writer),service=Depends(quant)):
    response.headers['Cache-Control']='no-store'
    return await asyncio.to_thread(service.users.delete,user['id'],strategy_id,data.expected_revision)
