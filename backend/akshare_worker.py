"""Isolated, time-bounded AkShare process; stdout is a single JSON document."""
import contextlib
import json
import sys


def main():
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import akshare as ak
            kind, code, start, end = sys.argv[1:]
            symbol = ('sh' if code.startswith('6') else 'sz') + code
            if kind == 'news':
                frame = ak.stock_news_em(symbol=code)
            else:
                frame = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start.replace('-', ''), end_date=end.replace('-', ''), adjust='', timeout=12)
        print(json.dumps({'rows': json.loads(frame.to_json(orient='records', force_ascii=False, date_format='iso'))}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__}))


if __name__ == '__main__':
    main()
