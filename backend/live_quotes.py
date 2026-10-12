"""High-performance batch realtime quotes with dual-channel (Tencent + Sina) fallback."""
import asyncio
import logging
import re
from datetime import datetime
import httpx

from .news_cleaning import SHANGHAI
from .trading_calendar import market_state

logger = logging.getLogger(__name__)


def code_to_symbol(code: str) -> str:
    code = code.strip()
    return ('sh' if code.startswith('6') else 'sz') + code


async def fetch_tencent_quotes(codes: list[str], timeout: float = 3.5) -> dict[str, dict]:
    if not codes:
        return {}
    symbols = [code_to_symbol(c) for c in codes]
    url = f"http://qt.gtimg.cn/q={','.join(symbols)}"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="ignore")

    results = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("~")
        if len(parts) > 35:
            try:
                name = parts[1].strip()
                code = parts[2].strip()
                price = float(parts[3])
                prev_close = float(parts[4])
                open_price = float(parts[5])
                time_raw = parts[30].strip()
                as_of = (
                    f"{time_raw[:4]}-{time_raw[4:6]}-{time_raw[6:8]} {time_raw[8:10]}:{time_raw[10:12]}:{time_raw[12:14]}"
                    if len(time_raw) == 14
                    else datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
                )
                change_amount = float(parts[31])
                change_pct = float(parts[32])
                high = float(parts[33])
                low = float(parts[34])
                volume = int(float(parts[6])) * 100 if parts[6] else 0
                turnover = float(parts[37]) * 10000 if len(parts) > 37 and parts[37] else 0.0
                results[code] = {
                    "code": code,
                    "name": name,
                    "price": price,
                    "change": change_pct,
                    "change_amount": change_amount,
                    "previous_close": prev_close,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "volume": volume,
                    "turnover": turnover,
                    "as_of": as_of,
                    "source": "tencent",
                }
            except (ValueError, IndexError):
                continue
    return results


async def fetch_sina_quotes(codes: list[str], timeout: float = 3.5) -> dict[str, dict]:
    if not codes:
        return {}
    symbols = [code_to_symbol(c) for c in codes]
    url = f"http://hq.sinajs.cn/list={','.join(symbols)}"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="ignore")

    results = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        try:
            head, body = line.split("=", 1)
            raw_code = head.split("_")[-1].strip()
            code = raw_code[-6:]
            content = body.strip().strip('"')
            parts = content.split(",")
            if len(parts) > 31:
                name = parts[0].strip()
                open_price = float(parts[1])
                prev_close = float(parts[2])
                price = float(parts[3])
                high = float(parts[4])
                low = float(parts[5])
                volume = int(float(parts[8])) if parts[8] else 0
                turnover = float(parts[9]) if parts[9] else 0.0
                as_of = f"{parts[30].strip()} {parts[31].strip()}"
                pct = round(((price - prev_close) / prev_close) * 100, 2) if prev_close else 0.0
                change_amount = round(price - prev_close, 2)
                results[code] = {
                    "code": code,
                    "name": name,
                    "price": price,
                    "change": pct,
                    "change_amount": change_amount,
                    "previous_close": prev_close,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "volume": volume,
                    "turnover": turnover,
                    "as_of": as_of,
                    "source": "sina",
                }
        except (ValueError, IndexError):
            continue
    return results


async def get_batch_quotes(codes: list[str]) -> dict:
    valid_codes = [c for c in dict.fromkeys(codes) if re.fullmatch(r"\d{6}", c)]
    if not valid_codes:
        return {
            "status": "ok",
            "source": "none",
            "items": {},
            "as_of": datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
            "is_trading": False,
        }

    items = {}
    source = "tencent"
    try:
        items = await fetch_tencent_quotes(valid_codes)
    except Exception as exc:
        logger.warning("Tencent quotes request failed: %s; falling back to Sina", exc)

    missing = [c for c in valid_codes if c not in items]
    if missing:
        try:
            sina_items = await fetch_sina_quotes(missing)
            items.update(sina_items)
            source = "tencent+sina" if items and len(items) > len(sina_items) else "sina"
        except Exception as exc:
            logger.warning("Sina fallback failed: %s", exc)

    state = market_state()
    return {
        "status": "ok",
        "source": source,
        "is_trading": state.get("is_trading", False),
        "as_of": datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
        "items": items,
    }
