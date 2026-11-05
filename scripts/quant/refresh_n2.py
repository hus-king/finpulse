"""One bounded input worker; no process pools or further child processes."""
import argparse
import json
import os
from pathlib import Path
import resource
import sys
import uuid


def main():
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[key] = '1'
    # This worker is one process; its two HTTP threads share this address space.
    memory = 4 * 1024**3
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'overlay', 'config', 'artifact'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--decision-day', required=True)
    parser.add_argument('--extra-code', action='append', default=[])
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from datetime import date, timedelta
    from backend.trading_calendar import previous_trade_day
    from backend.quant.n2_inputs import refresh_archive, load_inputs
    signal = previous_trade_day(date.fromisoformat(args.decision_day))
    if signal is None:
        raise ValueError('交易日历待更新')
    # Non-trading calendar days still contain forecasts and announcements.
    # Monday therefore refreshes through Sunday, while prices end on Friday.
    cutoff = date.fromisoformat(args.decision_day)-timedelta(days=1)
    refresh_archive(args.base, args.overlay, json.loads(args.config.read_text()), cutoff.isoformat(), extra_codes=args.extra_code)
    value = load_inputs(args.base, args.overlay, args.decision_day, extra_codes=args.extra_code)
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.artifact.with_name(args.artifact.name+'.tmp-'+uuid.uuid4().hex)
    with temporary.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    temporary.replace(args.artifact)
    print(json.dumps(dict(status='complete', date=value['date'], signal_date=value['signal_date'],
                         eligible=len(value['rows']), memory_address_space_bytes=memory, processes=1)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(type(error).__name__+': bounded N2 input worker failed', file=sys.stderr)
        raise SystemExit(1)
