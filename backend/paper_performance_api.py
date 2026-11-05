from typing import Literal

from fastapi import APIRouter, Depends, Request, Response

from .auth import current_session

router=APIRouter(prefix='/api/paper',tags=['paper trading'])


def performance_service(request:Request):
    return request.app.state.paper_performance


@router.get('/performance')
async def performance(response:Response,
    benchmark:Literal['sh000300','sh000001','sz399001','sz399006','sh000688','sh000905','sh000852','sh000016']='sh000300',
    session=Depends(current_session),service=Depends(performance_service)):
    response.headers['Cache-Control']='no-store'
    return await service.performance(session['user']['id'],benchmark)
