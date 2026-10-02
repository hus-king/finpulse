"""Re-run the stored Tavily sample, enrich bodies, clean it and join AkShare data.

Run from the project root with: python -m scripts.news_experiment
No LLM calls are made. Network results are cached in .runtime.
"""
import argparse
import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from backend.market_data import build_snapshots, fetch_market_data
from backend.news_cleaning import SHANGHAI, clean_report, normalize_url

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_bodies(raw, cache_path, refresh=False):
    if cache_path.exists() and not refresh:
        return read_json(cache_path)
    config = read_json(ROOT/"config.local.json")
    base_url = config.get("tavily_base_url", "https://api.tavily.com").rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query:
        raise ValueError("Tavily base URL must be an HTTPS URL without credentials or query parameters")
    candidates = clean_report(raw)["items"]
    urls = list(dict.fromkeys(item["original_url"] for item in candidates))
    with httpx.Client(timeout=httpx.Timeout(45, connect=10)) as client:
        response = client.post(base_url+"/extract", headers={"Authorization": "Bearer "+config["tavily_api_key"]}, json={"urls": urls, "extract_depth": "basic", "format": "text", "include_usage": True})
    if not response.is_success:
        raise RuntimeError(f"Tavily extraction failed: HTTP {response.status_code}")
    result = {"requested_urls": urls, "status": response.status_code, "collected_at": datetime.now(SHANGHAI).isoformat(), "response": response.json()}
    save_json(cache_path, result)
    return result


def run(reference_date, refresh_market=False, refresh_extract=False, offline=False):
    reference = date.fromisoformat(reference_date)
    runtime = ROOT/".runtime"
    raw = read_json(runtime/"tavily-smoke-2026-10-01.json")
    extract_path = runtime/f"tavily-cleaning-extract-{reference}.json"
    extraction_error = None
    if offline and not extract_path.exists():
        extraction = {"response": {"results": [], "failed_results": []}}
    else:
        try:
            extraction = extract_bodies(raw, extract_path, refresh=refresh_extract and not offline)
        except (httpx.RequestError, ValueError, RuntimeError, OSError, KeyError) as exc:
            extraction_error = type(exc).__name__
            extraction = {"response": {"results": [], "failed_results": []}}
    bodies = {normalize_url(row["url"]): row["raw_content"] for row in extraction["response"].get("results", [])}
    cleaned = clean_report(raw, extracts=bodies)
    market_path = runtime/f"akshare-smoke-{reference}.json"
    if not offline and (refresh_market or not market_path.exists()):
        save_json(market_path, fetch_market_data(raw["date_range"][0], str(reference)))
    market_report = read_json(market_path) if market_path.exists() else {}
    report = {"reference_date": str(reference), "generated_at": datetime.now(SHANGHAI).isoformat(), "raw_sample": "tavily-smoke-2026-10-01.json", "cleaning": cleaned, "body_extraction": {"success_count": len(bodies), "failed_results": extraction["response"].get("failed_results", []), "request_error_type": extraction_error, "usage": extraction["response"].get("usage"), "cached_response": extract_path.name}, "market": {"akshare_version": market_report.get("akshare_version"), "snapshots": build_snapshots(market_report), "cached_response": market_path.name}}
    for item in cleaned["items"]:
        snapshot = next(s for s in report["market"]["snapshots"] if s["stock"] == item["stock"])
        day_bar = next((bar for bar in snapshot["history"] if bar["date"] == item["effective_date"]), None)
        item["market_context"] = {"stock_code": item["stock_code"], "source": snapshot["source"], "is_realtime": False, "latest_daily_close": snapshot["close"], "latest_bar_date": snapshot["as_of_date"], "news_calendar_day_bar": day_bar, "note": "同日行情只是日线记录，未对齐新闻发布时刻，不证明新闻导致价格变化"}
    destination = runtime/f"news-cleaned-akshare-{reference}.json"
    save_json(destination, report)
    from scripts.render_news_experiment import render_preview
    render_preview(report, runtime/"tavily-preview"/"cleaned.html")
    print(json.dumps({"report": str(destination), "summary": cleaned["summary"], "extraction": report["body_extraction"], "market": [{key: snapshot[key] for key in ["stock", "status", "close", "as_of_date", "history_rows", "quote_status"]} for snapshot in report["market"]["snapshots"]]}, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-date", default="2026-10-02", help="Client's reference date, YYYY-MM-DD")
    parser.add_argument("--refresh-market", action="store_true")
    parser.add_argument("--refresh-extract", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Use local responses without any network calls")
    args = parser.parse_args()
    run(args.reference_date, args.refresh_market, args.refresh_extract, args.offline)
