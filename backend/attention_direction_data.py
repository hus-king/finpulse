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
SESSION_NEWS_NAMES = ['publication_age_log_days', 'unknown_publication_time', 'availability_age_log_days',
                      'preopen', 'morning', 'afternoon', 'off_session', 'trading_age']


def trading_clock(stamp, calendar):
    """Trading-day coordinate; nights/weekends do not create trading exposure."""
    stamp = timestamp(stamp) if isinstance(stamp, str) else stamp
    day = stamp.date().isoformat()
    index = bisect_left(calendar, day)
    if index == len(calendar) or calendar[index] != day:
        return float(index)
    minute = stamp.hour * 60 + stamp.minute + stamp.second / 60
    elapsed = min(120., max(0., minute - 570)) + min(120., max(0., minute - 780))
    return index + elapsed / 240


def session_news_time(article, cutoff, calendar, mode):
    published = article['published']
    available = article['observed'] if mode == 'observed' else published
    unknown = len(article.get('publication_timestamp') or '') <= 10
    # EOD is a conservative AVAILABILITY bound, not proof of an after-hours
    # publication. Use the oldest plausible intraday age for the recency prior.
    freshness_time = published.replace(hour=0, minute=0, second=0, microsecond=0) if unknown else published
    minute = published.hour * 60 + published.minute
    on_day = not unknown and published.date().isoformat() in calendar
    preopen = on_day and minute < 570
    morning = on_day and 570 <= minute < 690
    afternoon = on_day and 780 <= minute < 900
    age = max(0., (cutoff - freshness_time).total_seconds() / 86400)
    available_age = max(0., (cutoff - available).total_seconds() / 86400)
    return [math.log1p(age) / math.log(32), float(unknown),
            math.log1p(available_age) / math.log(32), float(preopen), float(morning), float(afternoon),
            float(not unknown and not (preopen or morning or afternoon)),
            max(0., trading_clock(cutoff, calendar) - trading_clock(freshness_time, calendar))]


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
    sessions = bool(corpus.get('direction_policy'))
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
                if sessions:
                    article_times.append(session_news_time(article, cutoff, calendar, sample['news_protocol']))
                else:
                    age = max(0, (cutoff - article['published']).total_seconds() / 86400)
                    article_times.append([math.log1p(age) / math.log(32), float(not article.get('publication_timestamp') or len(article['publication_timestamp']) <= 10)])
            ages.append(article_times + [[0.] * (8 if sessions else 2)] * (32 - len(keys)))
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
