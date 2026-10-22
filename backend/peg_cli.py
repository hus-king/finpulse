"""SSH/operator entrypoint: publish fresh data or import a just-completed initial batch."""
import argparse
import asyncio
import json
from datetime import datetime,timedelta
from pathlib import Path
from .database import create_auth_store
from .research_store import ResearchStore
from .peg_service import PegService
from .providers import ROOT
from .news_cleaning import SHANGHAI


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--import-raw');parser.add_argument('--force',action='store_true')
    args=parser.parse_args();store=ResearchStore(create_auth_store(ROOT));store.initialize()
    collector=None
    if args.import_raw:
        source=Path(args.import_raw).resolve()
        records=[json.loads(line) for line in source.read_text().splitlines() if line.strip()]
        now=datetime.now(SHANGHAI)
        for row in records:
            at=datetime.fromisoformat(row.get('采集时间',''))
            if at.tzinfo is None or not timedelta(0)<=now-at<timedelta(hours=24):
                raise ValueError('仅允许导入最近24小时实际采集的原始批次')
        async def existing():return records
        collector=existing
    service=PegService(store,collector=collector)
    try:
        result=await service.run(force=args.force)
        print(json.dumps(result,ensure_ascii=False))
        if result['status'] not in ('completed','already_completed'):raise SystemExit(1)
    finally:await service.close()


if __name__=='__main__':asyncio.run(main())
