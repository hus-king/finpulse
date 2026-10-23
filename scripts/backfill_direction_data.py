"""Collect dated public news and longer daily bars without changing live caches.

Run with the deployed virtualenv and --root pointing to a deployed checkout.
Successful requests are saved immediately and reused on resume. News search
uses the configured Tavily account; no LLM analysis or notifications are run.
"""
import argparse
import asyncio
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys


def corpus_tools():
    # Production releases may predate the offline experiment module. A copy of
    # news_direction.py can be shipped beside this standalone collection script.
    try:
        from backend import news_direction
    except ImportError:
        import news_direction
    return news_direction


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def intervals(start, end, width):
    """Inclusive, disjoint date windows, including a short final window."""
    cursor = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while cursor <= last:
        stop = min(last, cursor + timedelta(days=width - 1))
        yield cursor.isoformat(), stop.isoformat()
        cursor = stop + timedelta(days=1)


def cleaned_documents(stock, response, start, end, captured_at):
    from backend.news_cleaning import clean_report, normalize_url
    raw = {'date_range': [start, end], 'searches': [{'stock': stock['name'], 'response': response}]}
    extracts = {normalize_url(row.get('url', '')): row['raw_content']
                for row in response['results'] if isinstance(row.get('raw_content'), str) and row['raw_content'].strip()}
    cleaned = clean_report(raw, extracts, {stock['name']: {'code': stock['code'], 'aliases': [stock['name'], stock['code']]}})
    documents = []
    for item in cleaned['items']:
        identity = [stock['code'], item['url'], item['title'], item['cleaned_text'],
                    item['effective_date'], item['published_date_metadata'], item['text_source'], item['date_status']]
        # Match the original public-cache export identity so older observations win.
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        documents.append({'id': key, 'code': stock['code'], 'company': stock['name'],
                          'url': item['url'], 'title': item['title'], 'text': item['cleaned_text'],
                          'publication_date': item['effective_date'],
                          'publication_timestamp': item['published_date_metadata'],
                          'text_source': item['text_source'], 'date_status': item['date_status'],
                          'first_seen_at': captured_at,
                          'provenance': {'kind': 'historical_segment_search', 'window': [start, end], 'captured_at': captured_at}})
    reasons = Counter(reason for row in cleaned['audit'] for reason in row['reason_codes'])
    return documents, reasons


HISTORY_WORKER = '''
import contextlib,json,sys
try:
 with contextlib.redirect_stdout(sys.stderr):
  import akshare as ak
  code,start,end,adjust=sys.argv[1:]
  frame=ak.stock_zh_a_hist_tx(symbol=('sh' if code.startswith('6') else 'sz')+code,start_date=start.replace('-',''),end_date=end.replace('-',''),adjust=adjust,timeout=12)
 print(frame.to_json(orient='records',force_ascii=False,date_format='iso'))
except Exception as exc:
 print(json.dumps({'error':type(exc).__name__}))
'''


async def history(code, start, end, adjustment):
    valid_bars = corpus_tools().valid_bars
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', HISTORY_WORKER, code, start, end, adjustment,
                                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 90)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    rows = json.loads(output)
    if not isinstance(rows, list) or not rows:
        raise ValueError('No history returned')
    bars = valid_bars([{**row, 'date': str(row['date'])[:10]} for row in rows if start <= str(row['date'])[:10] <= end])
    if not bars:
        raise ValueError('No valid history in requested interval')
    return [bars[day] for day in sorted(bars)]


async def collect(args):
    sys.path.insert(0, str(args.root.resolve()))
    from backend import providers
    documents, timestamp = corpus_tools().documents, corpus_tools().timestamp
    from backend.news_cleaning import SHANGHAI
    baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
    accepted_before, _ = documents(baseline)
    stocks = {n['code']: {'code': n['code'], 'name': n['company']} for n in accepted_before}
    if args.codes:
        requested = set(args.codes.split(','))
        if requested - stocks.keys():
            raise ValueError('Requested stock missing from qualified baseline')
        stocks = {code: stock for code, stock in stocks.items() if code in requested}
    end = date.fromisoformat(args.end) if args.end else datetime.now(SHANGHAI).date() - timedelta(days=1)
    news_start = end - timedelta(days=args.news_days - 1)
    market_start = end - timedelta(days=args.market_days - 1)
    windows = list(intervals(news_start.isoformat(), end.isoformat(), args.window_days))
    plan = {'version': 1, 'baseline_sha256': hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
            'stocks': stocks, 'news_range': [news_start.isoformat(), end.isoformat()],
            'market_range': [market_start.isoformat(), end.isoformat()], 'window_days': args.window_days,
            'search_recipe': 'company name + code + 财经新闻 公司公告', 'max_results': 20,
            'search_depth': 'advanced', 'planned_search_requests': len(stocks) * len(windows),
            'max_search_requests': args.max_search_requests,
            'endpoint_fingerprint': digest(providers.read_config().get('tavily_base_url', 'https://api.tavily.com')),
            'availability': 'actual response capture time; historical publication dates never replace first_seen_at',
            'output': 'isolated files only; no database, scheduler, notification or model changes'}
    if plan['planned_search_requests'] > args.max_search_requests:
        raise ValueError('Search request budget exceeded; reduce stocks/days or set an explicit larger budget')
    args.output.mkdir(parents=True, exist_ok=True)
    plan_path = args.output / 'plan.json'
    if plan_path.exists() and json.loads(plan_path.read_text()) != plan:
        raise ValueError('Output already contains a different plan; choose a new directory')
    save(plan_path, plan)
    for folder in ('news', 'market'):
        (args.output / folder).mkdir(exist_ok=True)
    news = {n['id']: n for n in baseline['news']}
    counts, failures, rejected, changes = Counter(), [], Counter(), []
    capacity = asyncio.Semaphore(2)
    assemble_only = getattr(args, 'assemble_only', False)

    async def stock_job(code, stock):
        async with capacity:
            market = baseline['markets'].setdefault(code, {'raw': [], 'adjusted': []})
            for kind, adjust in (('raw', ''), ('adjusted', 'qfq')):
                path = args.output / 'market' / f'{code}-{kind}.json'
                try:
                    if path.exists():
                        saved = json.loads(path.read_text())
                        counts['reused_market_requests'] += 1
                    else:
                        if assemble_only:
                            counts['missing_market_requests'] += 1
                            continue
                        bars = await history(code, market_start.isoformat(), end.isoformat(), adjust)
                        saved = {'bars': bars, 'captured_at': datetime.now(timezone.utc).isoformat(),
                                 'source': 'AkShare / Tencent', 'adjustment': adjust or 'none',
                                 'volume_unit': 'shares per deployed AkShare adapter'}
                        save(path, saved)
                        counts['market_requests'] += 1
                    prior = {bar['date']: bar for bar in market.get(kind, [])}
                    for bar in saved['bars']:
                        old = prior.get(bar['date'])
                        if old and old != bar:
                            changes.append({'code': code, 'kind': kind, 'date': bar['date'], 'before': old, 'after': bar})
                        if old is None:
                            counts['added_' + kind + '_bars'] += 1
                        prior[bar['date']] = bar
                    market[kind] = [prior[day] for day in sorted(prior)]
                    market['backfill_metadata'] = {'captured_at': saved['captured_at'], 'volume_unit': saved['volume_unit'],
                                                 'adjusted_reference': 'qfq fetched now; not a historical point-in-time price feed'}
                except Exception as exc:
                    failures.append({'code': code, 'stage': kind, 'error_type': type(exc).__name__})
            for start, stop in windows:
                path = args.output / 'news' / f'{code}-{start}-{stop}.json'
                try:
                    if path.exists():
                        saved = json.loads(path.read_text())
                        counts['reused_news_requests'] += 1
                    else:
                        if assemble_only:
                            counts['missing_news_requests'] += 1
                            continue
                        response = await asyncio.wait_for(providers.tavily('search', {
                            'query': f'{stock["name"]} {code} 财经新闻 公司公告', 'topic': 'news',
                            'search_depth': 'advanced', 'max_results': 20, 'start_date': start, 'end_date': stop,
                            'include_raw_content': True, 'include_answer': False, 'include_usage': True}), 65)
                        # Save source material before cleaning; a cleaner failure must not charge twice on resume.
                        saved = {'stock': stock, 'window': [start, stop], 'response': response,
                                 'captured_at': datetime.now(timezone.utc).isoformat()}
                        save(path, saved)
                        counts['news_requests'] += 1
                    rows, reasons = cleaned_documents(stock, saved['response'], start, stop, saved['captured_at'])
                    rejected.update(reasons)
                    counts['search_results'] += len(saved['response']['results'])
                    counts['saturated_windows'] += len(saved['response']['results']) >= 20
                    for row in rows:
                        if row['id'] not in news or timestamp(row['first_seen_at']) < timestamp(news[row['id']]['first_seen_at']):
                            news[row['id']] = row
                except Exception as exc:
                    failures.append({'code': code, 'stage': 'news', 'window': [start, stop], 'error_type': type(exc).__name__})
                counts['finished_news_windows'] += 1
                print(json.dumps({'code': code, 'window': [start, stop], 'counts': dict(counts), 'failures': len(failures)}), flush=True)
                if not assemble_only:
                    await asyncio.sleep(.25)

    await asyncio.gather(*(stock_job(code, stock) for code, stock in sorted(stocks.items())))
    baseline['news'] = sorted(news.values(), key=lambda n: n['id'])
    baseline['exported_at'] = datetime.now(timezone.utc).isoformat()
    baseline['source'] = 'audited prior corpus + timestamped historical search windows + longer daily bars'
    baseline['backfill_provenance'] = plan
    accepted, excluded = documents(baseline)
    previous_ids = {n['id'] for n in accepted_before}
    new = [n for n in accepted if n['id'] not in previous_ids]
    summary = {'completed_at': baseline['exported_at'],
               'complete': not failures and not counts['missing_market_requests'] and not counts['missing_news_requests'],
               'assemble_only': assemble_only,
               'counts': dict(counts), 'failures': failures, 'cleaner_rejections': dict(rejected),
               'qualified_before': len(accepted_before), 'qualified_after': len(accepted),
               'new_qualified_versions': len(new), 'rejected_documents': excluded,
               'new_qualified_publication_range': [min(n['publication_date'] for n in new), max(n['publication_date'] for n in new)] if new else None,
               'raw_bars': sum(len(m['raw']) for m in baseline['markets'].values()),
               'adjusted_bars': sum(len(m.get('adjusted', [])) for m in baseline['markets'].values()),
               'market_revisions': len(changes), 'search_completeness': 'bounded search, not a complete historical news archive',
               'strict_evaluation': 'Backfilled news is first observable now; cannot enlarge past observed test dates.'}
    save(args.output / 'corpus.json', baseline)
    save(args.output / 'market-revisions.json', changes)
    summary['corpus_sha256'] = hashlib.sha256((args.output / 'corpus.json').read_bytes()).hexdigest()
    save(args.output / 'collection-report.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--end', help='Last included day; defaults to yesterday in Shanghai')
    parser.add_argument('--codes', help='Comma-separated subset of qualified baseline stocks')
    parser.add_argument('--news-days', type=int, default=30)
    parser.add_argument('--market-days', type=int, default=365)
    parser.add_argument('--window-days', type=int, default=7)
    parser.add_argument('--max-search-requests', type=int, default=300)
    parser.add_argument('--assemble-only', action='store_true', help='Freeze saved responses without making any network requests')
    args = parser.parse_args()
    if not (1 <= args.news_days <= 3660 and 1 <= args.market_days <= 7300 and 1 <= args.window_days <= 30
            and 0 < args.max_search_requests <= 10000):
        parser.error('Invalid day window or request budget')
    asyncio.run(collect(args))


if __name__ == '__main__':
    main()
