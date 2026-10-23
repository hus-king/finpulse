"""Point-in-time sample construction for the offline news-direction experiment.

No model or web dependencies. Samples are stock/dates, never individual articles.
"""
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import math
import re
import statistics

SHANGHAI = timezone(timedelta(hours=8))
SCHEMA_VERSION = 'news-direction-v1'
HORIZON = 3
NEWS_DAYS = 10
MARKET_NAMES = ['return_1', 'return_5', 'return_10', 'return_20', 'ma_5', 'ma_20',
                'volatility_20', 'volume_5', 'volume_20', 'range_20']


def timestamp(value):
    if not isinstance(value, str) or len(value) <= 10:
        raise ValueError('A timestamp with a timezone is required')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('A timestamp with a timezone is required')
    return stamp.astimezone(SHANGHAI)


def publication_time(article):
    day = date.fromisoformat(article['publication_date'])
    value = article.get('publication_timestamp')
    if value and len(value) > 10:
        try:
            stamp = timestamp(value)
            if stamp.date() == day:
                return stamp
        except ValueError:
            pass
    # Date-only news cannot be assumed available at midnight or before the close.
    return datetime.combine(day, time(23, 59, 59), SHANGHAI)


def documents(corpus, *, include_filtered=False):
    accepted, rejected = [], Counter()
    identities = set()
    for item in corpus['news']:
        reason = None
        if item.get('text_source') != 'extracted_body':
            reason = 'not_full_body'
        elif item.get('date_status') != 'body_verified':
            reason = 'date_not_body_verified'
        elif (not isinstance(item.get('text'), str)
              or (len(item['text'].strip()) < 80 and not (include_filtered and item.get('direction_excluded')))):
            reason = 'short_body'
        elif not re.fullmatch(r'\d{6}', str(item.get('code', ''))):
            reason = 'invalid_code'
        if reason:
            rejected[reason] += 1
            continue
        try:
            pub, seen = publication_time(item), timestamp(item['first_seen_at'])
        except (ValueError, KeyError, TypeError):
            rejected['invalid_time'] += 1
            continue
        identity = item['id']
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ValueError('Document IDs must be unique nonempty strings')
        identities.add(identity)
        if item.get('direction_excluded') and not include_filtered:
            rejected['direction_filtered'] += 1
            continue
        text_hash = hashlib.sha256(re.sub(r'\s+', '', item['text']).encode()).hexdigest()
        accepted.append({**item, 'published': pub, 'observed': max(pub, seen), 'text_hash': text_hash})
    return accepted, dict(rejected)


def valid_bars(bars):
    result = {}
    for item in bars:
        day = date.fromisoformat(item['date']).isoformat()
        values = {key: float(item[key]) for key in ('open', 'close', 'high', 'low', 'volume')}
        if not all(math.isfinite(x) for x in values.values()):
            raise ValueError('Non-finite price data')
        if (min(values[k] for k in ('open', 'close', 'high', 'low')) <= 0
                or values['volume'] < 0 or values['low'] > min(values['open'], values['close'])
                or values['high'] < max(values['open'], values['close'])):
            raise ValueError('Invalid OHLCV data')
        bar = {'date': day, **values}
        if day in result and result[day] != bar:
            raise ValueError('Conflicting duplicate price dates')
        result[day] = bar
    return result


def market_features(bars):
    if len(bars) != 21 or any(b['volume'] <= 0 for b in bars):
        raise ValueError('21 consecutive, nonsuspended trading bars are required')
    closes = [b['close'] for b in bars]
    volumes = [b['volume'] for b in bars]
    return ([closes[-1] / closes[-1 - n] - 1 for n in (1, 5, 10, 20)]
            + [closes[-1] / statistics.mean(closes[-n:]) - 1 for n in (5, 20)]
            + [statistics.pstdev([b / a - 1 for a, b in zip(closes, closes[1:])])]
            + [volumes[-1] / statistics.mean(volumes[-n:]) - 1 for n in (5, 20)]
            + [statistics.mean((b['high'] - b['low']) / b['close'] for b in bars[-20:])])


def action_in_window(raw, adjusted):
    """Detect adjustment changes via OHLC affine residuals, including cash dividends.

Returns None when no adjusted reference exists. Raw close direction remains the
explicit target in that case; this experiment does not claim total returns.
"""
    if any(b['date'] not in adjusted for b in raw):
        return None
    pairs = [(b[k], adjusted[b['date']][k]) for b in raw for k in ('open', 'close', 'high', 'low')]
    mx = statistics.mean(x for x, _ in pairs)
    my = statistics.mean(y for _, y in pairs)
    variance = sum((x - mx) ** 2 for x, _ in pairs)
    if variance < 1e-12:
        return max(y for _, y in pairs) - min(y for _, y in pairs) > .025
    slope = sum((x - mx) * (y - my) for x, y in pairs) / variance
    tolerance = .025 + max(abs(y) for _, y in pairs) * .000005
    return slope <= 0 or max(abs(y - (my + slope * (x - mx))) for x, y in pairs) > tolerance


def build_samples(corpus, mode='observed', *, prediction=False):
    if corpus.get('schema_version') != 1 or mode not in ('observed', 'publication_replay'):
        raise ValueError('Unsupported corpus schema or time protocol')
    exported = timestamp(corpus['exported_at'])
    policy = corpus.get('direction_policy', {})
    if policy and policy.get('version') != 'event-news-session-v2':
        raise ValueError('Unsupported news direction policy')
    # Retain tombstones until after version selection: an excluded new version
    # must not resurrect the previous text at the same URL.
    articles, rejected = documents(corpus, include_filtered=True)
    by_code = defaultdict(list)
    for article in articles:
        if article['published'] <= exported:
            by_code[article['code']].append(article)
    prices = {code: valid_bars(value['raw']) for code, value in corpus['markets'].items()}
    # Shared market calendar prevents a suspended stock's next bar being called t+3.
    calendar = sorted({day for bars in prices.values() for day in bars
                       if datetime.combine(date.fromisoformat(day), time(15), SHANGHAI) <= exported})
    cutoffs = [datetime.combine(date.fromisoformat(day), time(15), SHANGHAI) for day in calendar]
    time_field = 'observed' if mode == 'observed' else 'published'
    first_news = min((a[time_field].date().isoformat() for a in articles), default='9999-12-31')
    samples, skipped = [], Counter()
    for code, stock_news in sorted(by_code.items()):
        if code not in prices:
            skipped['no_prices'] += 1
            continue
        bars = prices[code]
        adjusted = valid_bars(corpus['markets'][code].get('adjusted', []))
        positions = [len(calendar) - 1] if prediction else range(20, len(calendar) - HORIZON)
        for index in positions:
            if index < 20 or calendar[index] < max(first_news, policy.get('signal_start', first_news)):
                continue
            days = calendar[index - 20:index + (1 if prediction else HORIZON + 1)]
            if any(day not in bars or bars[day]['volume'] <= 0 for day in days):
                skipped['missing_or_suspended_bar'] += 1
                continue
            window = [bars[d] for d in days]
            adjustment = action_in_window(window, adjusted)
            if policy.get('require_adjustment_reference') and adjustment is None:
                skipped['missing_adjustment_reference'] += 1
                continue
            if adjustment:
                skipped['corporate_action'] += 1
                continue
            sequence = [[] for _ in range(NEWS_DAYS)]
            # Select the latest AVAILABLE version per URL, before deduplicating text.
            versions = {}
            for article in sorted(stock_news, key=lambda a: (a['observed'], a['id'])):
                available = article['observed'] if mode == 'observed' else article['published']
                if available <= cutoffs[index]:
                    versions[article['url'] or article['id']] = article
            hashes = set()
            for article in sorted(versions.values(), key=lambda a: (a['published'], a['id']), reverse=True):
                if article.get('direction_excluded'):
                    continue
                bucket_time = article['observed'] if policy and mode == 'observed' else article['published']
                bucket = bisect_left(cutoffs, bucket_time)
                offset = bucket - (index - NEWS_DAYS + 1)
                if 0 <= offset < NEWS_DAYS and article['text_hash'] not in hashes:
                    hashes.add(article['text_hash'])
                    if len(sequence[offset]) < 32:
                        sequence[offset].append(article['id'])
            record = {'code': code, 'date': calendar[index], 'cutoff': cutoffs[index].isoformat(),
                      'market': market_features(window[:21]), 'news': sequence,
                      'news_count': sum(map(len, sequence)),
                      'adjustment_checked': action_in_window(window, adjusted) is not None}
            if policy:
                record['news_protocol'] = mode
            if not prediction:
                ret = bars[calendar[index + HORIZON]]['close'] / bars[calendar[index]]['close'] - 1
                if ret == 0:
                    skipped['flat_target'] += 1
                    continue
                record.update(label=int(ret > 0), return_3d=ret, label_end=calendar[index + HORIZON])
            samples.append(record)
    audit = {'protocol': mode, 'accepted_document_versions': len(articles), 'rejected_documents': rejected,
             'eligible_document_versions': sum(not a.get('direction_excluded') for a in articles),
             'direction_exclusions': dict(Counter(a.get('direction_category', 'unknown') for a in articles if a.get('direction_excluded'))),
             'sample_count': len(samples), 'sample_dates': len({s['date'] for s in samples}),
             'samples_with_news': sum(s['news_count'] > 0 for s in samples),
             'adjustment_unchecked_samples': sum(not s['adjustment_checked'] for s in samples),
             'skipped': dict(skipped), 'calendar': calendar,
             'target': 'unadjusted close(t+3)/close(t)-1; flat targets excluded; no trading return claim'}
    return samples, audit


def temporal_split(samples):
    days = sorted({s['date'] for s in samples})
    if len(days) < 15:
        raise ValueError(f'Only {len(days)} signal dates; need at least 15 for a purged train/validation/test split')
    validation_start, test_start = days[int(len(days) * .6)], days[int(len(days) * .8)]
    train = [s for s in samples if s['date'] < validation_start and s['label_end'] < validation_start]
    validation = [s for s in samples if validation_start <= s['date'] < test_start and s['label_end'] < test_start]
    test = [s for s in samples if s['date'] >= test_start]
    if not all((train, validation, test)) or len({s['label'] for s in train}) < 2:
        raise ValueError('Insufficient dated samples or training classes after purging label overlap')
    return {'train': train, 'validation': validation, 'test': test}, {
        'validation_start': validation_start, 'test_start': test_start,
        'purged_samples': len(samples) - len(train) - len(validation) - len(test),
        **{name: {'samples': len(rows), 'dates': len({s['date'] for s in rows}),
                  'start': min(s['date'] for s in rows), 'end': max(s['date'] for s in rows),
                  'latest_label_end': max(s['label_end'] for s in rows),
                  'up_fraction': sum(s['label'] for s in rows) / len(rows)}
           for name, rows in [('train', train), ('validation', validation), ('test', test)]}}


def tiny_temporal_holdout(samples):
    """Fixed-parameter pipeline smoke test when three-way evaluation cannot fit.

    Two final signal dates are held out; all intersecting training label intervals
    are purged. This does not supply validation data or model-selection evidence.
    """
    days = sorted({s['date'] for s in samples})
    if len(days) < 6:
        raise ValueError('Too few dates even for the tiny fixed-parameter holdout')
    test_start = days[-2]
    train = [s for s in samples if s['date'] < test_start and s['label_end'] < test_start]
    test = [s for s in samples if s['date'] >= test_start]
    if not train or len({s['label'] for s in train}) < 2:
        raise ValueError('No usable training classes after purging the tiny holdout')
    parts = {'train': train, 'test': test}
    return parts, {'test_start': test_start, 'validation': None,
                   'purged_samples': len(samples) - len(train) - len(test),
                   'evaluation': 'pipeline_smoke_test_only; no validation or model selection',
                   **{name: {'samples': len(rows), 'dates': len({s['date'] for s in rows}),
                             'start': min(s['date'] for s in rows), 'end': max(s['date'] for s in rows),
                             'latest_label_end': max(s['label_end'] for s in rows),
                             'up_fraction': sum(s['label'] for s in rows) / len(rows)}
                      for name, rows in parts.items()}}
