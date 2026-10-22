"""Public cached reads. Collection is intentionally scheduler/CLI-only."""
import asyncio
import csv
import io
from fastapi import APIRouter, Request
from fastapi.responses import Response

router=APIRouter(prefix='/api/screener')


@router.get('/csi300')
async def leaderboard(request:Request):
    data=await asyncio.to_thread(request.app.state.peg.snapshot)
    return data


@router.get('/csi300/export.csv')
async def export(request:Request):
    data=await asyncio.to_thread(request.app.state.peg.snapshot)
    out=io.StringIO();writer=csv.writer(out)
    columns=[('code','代码'),('name','名称'),('price','最新价'),('peg','前瞻PEG'),('rank','组内排名'),
             ('group','比较组'),('status','筛选状态'),('rank_change','排名升降'),('forward_pe','前瞻PE'),
             ('growth','前瞻CAGR_%'),('industry','行业'),('confidence','可信度'),('risk_tags','风险标签'),
             ('institutions','机构数'),('forecast_end','预测报告截止日'),('collected_at','采集时间')]
    writer.writerow([label for _,label in columns])
    def safe(value):
        if isinstance(value,(int,float)):return value
        if isinstance(value,list):value='；'.join(value)
        value='' if value is None else str(value)
        return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
    for row in data['rows']:writer.writerow([safe(row.get(key)) for key,_ in columns])
    return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':f'attachment; filename="csi300-peg-{data.get("as_of_date") or "empty"}.csv"'})
