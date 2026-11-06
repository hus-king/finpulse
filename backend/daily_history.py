"""Read a stock's unadjusted local daily archive; never scan minute files."""
import logging
import math
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

HISTORY_START = '2011-01-01'
_read_lock = threading.Lock()


def merge_daily_bars(*groups):
    """Later sources win by day. Reject invalid dates/prices without padding."""
    rows = {}
    for group in groups:
        for item in group:
            try:
                day = date.fromisoformat(item['date']).isoformat()
                bar = {name: float(item[name]) for name in ('open', 'high', 'low', 'close', 'volume')}
                if not all(math.isfinite(value) for value in bar.values()):
                    continue
                if min(bar['open'], bar['close'], bar['low']) <= 0 or bar['volume'] < 0:
                    continue
                if bar['low'] > min(bar['open'], bar['close']) or bar['high'] < max(bar['open'], bar['close']):
                    continue
                rows[day] = {**bar, 'date': day}
            except (KeyError, TypeError, ValueError):
                continue
    return [rows[day] for day in sorted(rows)]


def archive_symbol(code):
    if re.fullmatch(r'\d{6}', code):
        return code + ('.XSHG' if code.startswith('6') else '.XSHE')
    # An index must never alias the equity with the same six-digit code.
    from .instruments import is_index
    if is_index(code):
        return code[2:] + ('.XSHG' if code.startswith('sh') else '.XSHE')
    raise ValueError('Invalid instrument')


@lru_cache(maxsize=64)
def _read_archive(files, symbol, end):
    import duckdb
    lower = int(datetime.fromisoformat(HISTORY_START).replace(tzinfo=timezone.utc).timestamp() * 1000)
    upper = int(datetime.combine(date.fromisoformat(end) + timedelta(days=1), datetime.min.time(), timezone.utc).timestamp() * 1000)
    # Files are the daily directory only; projections and predicates execute in
    # the reader. One connection/thread and a small memory cap bound cold reads.
    with duckdb.connect(config={'threads': 1, 'memory_limit': '128MB'}) as conn:
        data = conn.execute('''SELECT ts, open, high, low, close, volume
            FROM read_parquet(?) WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts''',
            [[path for path, _, _ in files], symbol, lower, upper]).fetchall()
    return merge_daily_bars([{'date': datetime.fromtimestamp(row[0] / 1000, timezone.utc).date().isoformat(),
                             **dict(zip(('open', 'high', 'low', 'close', 'volume'), row[1:]))} for row in data])


class LocalDailyHistory:
    def __init__(self, directory=None):
        value = directory if directory is not None else os.environ.get('FINPULSE_DAILY_HISTORY_DIR')
        self.directory = Path(value) if value else None

    def read(self, code, end):
        if self.directory is None:
            return {'candles': [], 'configured': False}
        try:
            symbol = archive_symbol(code)
            if not self.directory.is_dir():
                raise OSError('Archive directory unavailable')
            files = []
            for path in sorted(self.directory.glob('*.parquet')):
                if re.fullmatch(r'\d{4}(?:-\d{2})?', path.stem) and HISTORY_START[:4] <= path.stem[:4] <= end[:4]:
                    stat = path.stat()
                    files.append((str(path), stat.st_mtime_ns, stat.st_size))
            with _read_lock:  # Cold concurrent viewers share one cached read.
                bars = _read_archive(tuple(files), symbol, end) if files else []
            return {'candles': bars, 'configured': True, 'source': '本地日线档案（未复权）',
                    'requested_start': HISTORY_START, 'available_start': bars[0]['date'] if bars else None,
                    'available_end': bars[-1]['date'] if bars else None}
        except Exception as exc:
            logging.getLogger(__name__).warning('Daily archive unavailable (%s)', type(exc).__name__)
            return {'candles': [], 'configured': True, 'error': '本地历史日线读取失败，保留已有行情'}
