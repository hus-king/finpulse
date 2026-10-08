"""Local-only UI fixture. No test clock or synthetic quote routes ship in backend."""
import argparse
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
from backend.auth import AuthStore, AuthError, auth_error_handler, router as auth_router
from backend.minute_market import MinuteMarketService
from backend.news_cleaning import SHANGHAI
from backend.paper_api import router as paper_router, paper_error_handler
from backend.paper_rules import PaperError
from backend.paper_store import PaperStore
from backend.paper_trading import PaperTradingService
from backend.research import ResearchService
from backend.research_store import ResearchStore


def create_app(db_path: Path) -> FastAPI:
    fixture = FastAPI()
    auth = AuthStore(db_path)
    auth.initialize()
    records = ResearchStore(auth)
    records.initialize()
    research = ResearchService(records, None)
    clock = SimpleNamespace(now=datetime(2026, 10, 8, 10, tzinfo=SHANGHAI))
    prices = {'000001': 10, '600519': 1338, '300750': 300, '688981': 80}
    minute = MinuteMarketService(records, clock=lambda: clock.now)
    minute.closed = True
    def seed():
        for code, price in prices.items():
            records.put('minute_market', code + ':1', {'candles': [{'date': clock.now.strftime('%Y-%m-%d %H:%M:%S'),
                'open':price,'close':price,'high':price,'low':price,'volume':100}], 'fetched_at':clock.now.isoformat(),'error':None})
    seed()
    fixture.state.auth_store = auth
    fixture.state.paper_trading = PaperTradingService(PaperStore(records),research.catalog,minute,clock=lambda:clock.now)
    fixture.add_exception_handler(AuthError,auth_error_handler)
    fixture.add_exception_handler(PaperError,paper_error_handler)
    fixture.include_router(auth_router)
    fixture.include_router(paper_router)

    @fixture.post('/__test/clock')
    def set_clock(body: dict):
        clock.now = datetime.fromisoformat(body['now']).astimezone(SHANGHAI)
        prices.update(body.get('prices', {}))
        seed()
        return {'ok':True}

    @fixture.get('/api/stocks')
    def stocks(q: str = '', codes: str | None = None, limit: int = 30):
        return research.catalog.search(q,limit,codes.split(',') if codes else None)

    @fixture.get('/api/health')
    def health():
        return {'status':'ok','configured':False,'model':None,'provider':None,'tavily_configured':False,'scheduler_enabled':False}

    @fixture.get('/api/watchlist')
    def watchlist():
        return {'codes':list(prices),'items':[research.catalog.get(code) for code in prices],'jobs':[]}

    @fixture.get('/api/dashboard/{code}')
    def dashboard(code: str):
        return research.dashboard(code)

    @fixture.get('/api/market/{code}/bundle')
    async def bundle(code: str):
        snap = await minute.snapshot(research.catalog.get(code),1,schedule=False)
        return {'dashboard':research.dashboard(code),'minutes':{str(period):{**snap,'period':period} for period in [1,5,15,30,60]},
                'status':'ok','errors':{},'next_poll_seconds':30,'fetched_at':clock.now.isoformat()}

    fixture.mount('/',StaticFiles(directory=ROOT/'dist',html=True))
    return fixture


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8012)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='finpulse-paper-ui-') as directory:
        uvicorn.run(create_app(Path(directory)/'ui.db'),host='127.0.0.1',port=args.port,log_level='warning')
