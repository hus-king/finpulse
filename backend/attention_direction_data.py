"""Point-in-time company cards and article-level features for offline training."""
from bisect import bisect_left
from collections import Counter
from datetime import date, datetime, time
import hashlib
import json
import math
import re
import statistics

from .news_direction import NEWS_DAYS, SHANGHAI, documents, timestamp, valid_bars

DAY_MARKET_NAMES = ['return_1', 'return_5', 'ma_5', 'volume_5', 'range', 'close_open']


def company_cards(snapshot):
    """Verbatim business fragments, not inferred financial/relationship facts."""
    cards, versions = {}, []
    for version in snapshot['versions']:
        if not version.get('source') or not str(version.get('url') or '').startswith(('http://', 'https://')):
            continue
        known = timestamp(version['known_at'])
        text = version['main_business'].strip()
        fragments = [text] + [x.strip() for x in re.split(r'[。；;、]', text) if len(x.strip()) >= 4]
        ids = []
        for fragment in dict.fromkeys(fragments):
            kind = 'business_overview' if fragment == text else 'verbatim_business_fragment'
            identity = 'fact_' + hashlib.sha256(json.dumps([version['code'], kind, fragment], ensure_ascii=False).encode()).hexdigest()
            ids.append(identity)
            if identity not in cards:
                cards[identity] = {'id': identity, 'code': version['code'], 'kind': kind, 'text': fragment,
                                   'source': version['source'], 'url': version['url']}
            if len(ids) == 8:
                break
        versions.append({'code': version['code'], 'id': version['id'], 'known_at': known.isoformat(), 'cards': ids})
    return {'schema_version': 1, 'cards': sorted(cards.values(), key=lambda x: x['id']),
            'versions': sorted(versions, key=lambda x: (x['known_at'], x['id'])),
            'limitations': 'Business description only; no invented exposures or historical publication dates'}


def available_cards(bundle, code, cutoff):
    eligible = [v for v in bundle['versions'] if v['code'] == code and timestamp(v['known_at']) <= timestamp(cutoff)]
    return max(eligible, key=lambda v: (timestamp(v['known_at']), v['id']))['cards'] if eligible else []


def daily_market_sequence(bars, days):
    """Six daily features, each using only prices available by that daily close."""
    result = []
    for index in range(len(days) - NEWS_DAYS, len(days)):
        current = bars[days[index]]
        close = current['close']
        previous = [bars[d] for d in days[index - 4:index + 1]]
        result.append([close / bars[days[index - 1]]['close'] - 1,
                       close / bars[days[index - 5]]['close'] - 1,
                       close / statistics.mean(b['close'] for b in previous) - 1,
                       current['volume'] / statistics.mean(b['volume'] for b in previous) - 1,
                       (current['high'] - current['low']) / close,
                       close / current['open'] - 1])
    return result


def indexed_features(samples, corpus, bundle, vector_ids):
    """Indices reference a shared frozen vector table; padding is index zero."""
    lookup = {key: i + 1 for i, key in enumerate(vector_ids)}
    articles = {a['id']: a for a in documents(corpus)[0]}
    prices = {code: valid_bars(market['raw']) for code, market in corpus['markets'].items()}
    exported = timestamp(corpus['exported_at'])
    calendar = sorted({day for bars in prices.values() for day in bars
                       if datetime.combine(date.fromisoformat(day), time(15), SHANGHAI) <= exported})
    output = {name: [] for name in ('news_ids', 'news_time', 'fact_ids', 'day_market', 'day_time', 'market')}
    coverage = Counter()
    for sample in samples:
        end = bisect_left(calendar, sample['date'])
        days = calendar[end - 20:end + 1]
        day_features, time_features, ids, ages = daily_market_sequence(prices[sample['code']], days), [], [], []
        cutoff = timestamp(sample['cutoff'])
        for day, keys in enumerate(sample['news']):
            ids.append([lookup[key] for key in keys] + [0] * (32 - len(keys)))
            article_times = []
            for key in keys:
                article = articles[key]
                age = max(0, (cutoff - article['published']).total_seconds() / 86400)
                article_times.append([math.log1p(age) / math.log(32), float(not article.get('publication_timestamp') or len(article['publication_timestamp']) <= 10)])
            ages.append(article_times + [[0., 0.]] * (32 - len(keys)))
            lag = (date.fromisoformat(sample['date']) - date.fromisoformat(days[-NEWS_DAYS + day])).days
            time_features.append([(NEWS_DAYS - 1 - day) / 9, lag / 20, float(bool(keys)), math.log1p(len(keys)) / math.log(33)])
        facts = available_cards(bundle, sample['code'], sample['cutoff'])
        output['news_ids'].append(ids)
        output['news_time'].append(ages)
        output['fact_ids'].append([lookup[key] for key in facts] + [0] * (8 - len(facts)))
        output['day_market'].append(day_features)
        output['day_time'].append(time_features)
        output['market'].append(sample['market'])
        coverage['samples'] += 1
        coverage['with_news'] += sample['news_count'] > 0
        coverage['with_company'] += bool(facts)
        coverage['with_news_and_company'] += bool(facts) and sample['news_count'] > 0
    return output, dict(coverage)
