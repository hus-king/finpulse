"""Serve the frozen stock-bond research snapshot."""
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


router = APIRouter(prefix='/api/market')
ARCHIVES = Path(__file__).resolve().parents[1] / 'public/market-archives'


@router.get('/stock-bond', response_class=FileResponse)
def stock_bond_snapshot(basket: Literal['long','three'] = 'long'):
    archive = ARCHIVES / ('stock-bond.json' if basket == 'long' else 'stock-bond-three.json')
    if not archive.is_file():
        raise HTTPException(503, '股债历史数据暂未生成')
    return FileResponse(archive, media_type='application/json', headers={'Cache-Control': 'public, max-age=300'})
