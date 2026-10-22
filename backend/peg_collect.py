"""Isolated entrypoint for the pinned collection engine; no model/news calls."""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def main():
    engine = Path(__file__).resolve().parents[1] / 'integrations' / 'stock_analyzer'
    sys.path.insert(0,str(engine))
    import run_csi300
    original = run_csi300.analyze_with_retry

    def stamped(*args, **kwargs):
        record, logs = original(*args, **kwargs)
        record['采集时间'] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        return record, logs

    run_csi300.analyze_with_retry = stamped
    run_csi300.main()


if __name__ == '__main__':
    main()
