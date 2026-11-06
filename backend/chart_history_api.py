from typing import Literal
from fastapi import APIRouter, Request, Response, HTTPException
from .chart_history import AdjustmentError

router=APIRouter(prefix='/api/market',tags=['chart history'])


@router.get('/{code}/history')
async def chart_history(code:str,request:Request,response:Response,
                        adjustment:Literal['none','qfq','hfq']='none'):
    response.headers['Cache-Control']='no-store'
    stock=request.app.state.research.catalog.get(code)
    if not stock:raise HTTPException(404,'未找到已核验的股票，请先搜索该股票。')
    try:return await request.app.state.chart_history.get(stock,adjustment)
    except AdjustmentError as error:raise HTTPException(422,str(error)) from None
