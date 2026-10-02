"""AkShare experiment adapters with explicit source, time and failure status."""
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .news_cleaning import SHANGHAI, STOCK_ENTITIES


def _request_frame(stock, symbol, interface, callback):
    started = time.perf_counter()
    try:
        frame = callback()
        rows = json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso"))
        return {"stock": stock, "symbol": symbol, "interface": interface, "status": "ok" if rows else "empty", "elapsed_ms": round((time.perf_counter()-started)*1000), "rows": rows}
    except Exception as exc:
        # Record failure types without leaking cookies, credentials or provider bodies.
        return {"stock": stock, "symbol": symbol, "interface": interface, "status": "error", "elapsed_ms": round((time.perf_counter()-started)*1000), "error_type": type(exc).__name__}


def fetch_market_data(start_date, end_date, timeout=10):
    import akshare as ak

    def fetch(stock):
        code = STOCK_ENTITIES[stock]["code"]
        symbol = ("SH" if code.startswith("6") else "SZ")+code
        quote = _request_frame(stock, symbol, "stock_individual_spot_xq", lambda: ak.stock_individual_spot_xq(symbol=symbol, timeout=timeout))
        history = _request_frame(stock, symbol.lower(), "stock_zh_a_hist_tx", lambda: ak.stock_zh_a_hist_tx(symbol=symbol.lower(), start_date=start_date.replace("-", ""), end_date=end_date.replace("-", ""), adjust="", timeout=timeout))
        history.update(adjust="none", volume_unit="shares", amount_unit="CNY")
        return quote, history

    with ThreadPoolExecutor(max_workers=3) as pool:
        pairs = list(pool.map(fetch, STOCK_ENTITIES))
    return {"collected_at": datetime.now(SHANGHAI).isoformat(), "akshare_version": ak.__version__, "quotes": [pair[0] for pair in pairs], "histories": [pair[1] for pair in pairs]}


def build_snapshots(report):
    """Historical closes remain historical even if a latest-quote request failed."""
    snapshots = []
    for stock, entity in STOCK_ENTITIES.items():
        quote = next((q for q in report.get("quotes", []) if q["stock"] == stock), {})
        history = next((h for h in report.get("histories", []) if h["stock"] == stock), {})
        valid = []
        for row in history.get("rows", []):
            try:
                day = datetime.fromisoformat(row["date"]).date().isoformat()
                close = float(row["close"])
                if close <= 0 or not math.isfinite(close):
                    continue
                valid.append({**row, "date": day, "close": close})
            except (KeyError, TypeError, ValueError):
                continue
        valid.sort(key=lambda row: row["date"])
        current, previous = (valid[-1], valid[-2] if len(valid) > 1 else None) if valid else (None, None)
        change = current["close"]-previous["close"] if previous else None
        snapshots.append({"stock": stock, "stock_code": entity["code"], "status": "ok" if current else "unavailable", "source": "AkShare / 腾讯日线", "interface": "stock_zh_a_hist_tx", "is_realtime": False, "price_kind": "latest_returned_daily_close", "as_of_date": current["date"] if current else None, "close": current["close"] if current else None, "change": round(change, 2) if change is not None else None, "change_percent": round(change/previous["close"]*100, 2) if previous else None, "history_rows": len(valid), "latest_bar": current, "history": valid, "quote_status": quote.get("status", "not_requested"), "quote_error_type": quote.get("error_type"), "collected_at": report.get("collected_at"), "currency": "CNY", "volume_unit": "shares", "amount_unit": "CNY", "adjustment": "none"})
    return snapshots
