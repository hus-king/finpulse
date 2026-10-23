"""Freeze an auditable event-news corpus without overwriting its source snapshot."""
import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path

from backend.direction_news_filter import POLICY_VERSION, clean_article
from backend.news_direction import documents, valid_bars


def prepare(source, output, signal_start, market_dir=None):
    date.fromisoformat(signal_start)
    raw = source.read_bytes()
    corpus = json.loads(raw)
    qualified, rejected = documents(corpus)
    market_audit = []
    if market_dir is not None:
        markets = {}
        for code in sorted({x['code'] for x in qualified}):
            market = {}
            for kind in ('raw', 'adjusted'):
                path = market_dir / f'{code}-{kind}.json'
                data = path.read_bytes()  # Missing fresh references fail closed.
                saved = json.loads(data)
                bars = valid_bars(saved['bars'])
                # The inspected deployed AkShare adapter exempts sz000* from
                # conversion as if they were indices; these codes are stocks.
                multiplier = 100 if code.startswith('000') else 1
                market[kind] = [{**bar, 'volume': bar['volume'] * multiplier} for _, bar in sorted(bars.items())]
                market_audit.append({'code': code, 'kind': kind, 'bars': len(bars),
                                     'source_sha256': hashlib.sha256(data).hexdigest(),
                                     'captured_at': saved['captured_at'], 'volume_multiplier': multiplier,
                                     'volume_unit': 'shares', 'source': saved['source']})
            markets[code] = market
        # Entire per-symbol histories come from one fresh provider snapshot;
        # never splice old hand/share units across dates in the training window.
        corpus['markets'] = markets
    cleaned, audit = [], []
    for article in qualified:
        item, decision = clean_article(article)
        # documents() adds parsed datetimes; these are derived, not source fields.
        item.pop('published', None)
        item.pop('observed', None)
        item.pop('text_hash', None)
        cleaned.append(item)
        audit.append(decision)
    output.mkdir(parents=True, exist_ok=False)
    corpus['news'] = cleaned
    corpus['direction_policy'] = {'version': POLICY_VERSION, 'signal_start': signal_start,
                                  'bucket': 'available_time_in_observed; publication_time_in_replay',
                                  'cutoff': '15:00 Asia/Shanghai after complete daily bar',
                                  'require_adjustment_reference': True,
                                  'fresh_market_snapshots': market_audit,
                                  'source_sha256': hashlib.sha256(raw).hexdigest(),
                                  'limitations': 'Rule-based event detection; uncertain articles quarantined; mixed event/price sentences retained verbatim.'}
    counts = Counter(x['category'] for x in audit)
    summary = {'policy': corpus['direction_policy'], 'source_qualified': len(qualified), 'source_rejections': rejected,
               'categories': dict(counts), 'retained': sum(not x['excluded'] for x in audit),
               'excluded': sum(x['excluded'] for x in audit),
               'removed_market_sentences': sum(len(x['removed_market_sentences']) for x in audit),
               'body_header_times': sum(x['timestamp_source'] == 'body_header' for x in audit),
               'date_only_times': sum(x['timestamp_source'] != 'body_header' for x in audit),
               'market_snapshots': market_audit}
    for name, value in [('corpus.json', corpus), ('cleaning-audit.json', audit), ('cleaning-report.json', summary)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--signal-start', required=True)
    parser.add_argument('--market-dir', type=Path, help='Fresh collector market/ responses; do not splice legacy volume units')
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output, args.signal_start, args.market_dir), ensure_ascii=False))
