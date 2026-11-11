"""Read-only, unlabelled news intake. No training or production application imports."""
import argparse
import gzip
import hashlib
import html
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

SHANGHAI = timezone(timedelta(hours=8))
CAPS = {'news': 1500, 'major_news': 400}
FIELDS = {'news': 'datetime,title,content,channels', 'major_news': 'title,content,pub_time,src'}
SITE_NAMESPACES = ('collection', 'dashboard', 'industry_search', 'company_business', 'stock_industry')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (dt if dt.tzinfo else dt.replace(tzinfo=SHANGHAI)).astimezone(SHANGHAI)
    except (ValueError, TypeError):
        return None


def future_publication(dt, origin):
    observed = timestamp(origin.get('collected_at') or origin.get('retrieved_at'))
    return bool(dt and observed and dt.date() > observed.date())


class WindowCollector:
    """Persist every attempt; reuse exact cached calls; split potentially capped windows."""
    def __init__(self, root, request, max_requests=39):
        self.root, self.request, self.max_requests = Path(root), request, max_requests
        self.root.mkdir(parents=True, exist_ok=True)

    def collect(self, api, src, start, end, cap=None):
        cap = CAPS[api] if cap is None else cap
        if not timestamp(start) or not timestamp(end) or timestamp(end) < timestamp(start):
            raise ValueError('Ordered timestamp window required')
        pending, failures, stack, completed = [], 0, [(start, end)], []
        while stack:
            lo, hi = stack.pop()
            params = {'src': src, 'start_date': lo, 'end_date': hi}
            public_request = {'api_name': api, 'params': params, 'fields': FIELDS[api]}
            key = sha(json.dumps(public_request, sort_keys=True).encode())
            meta_path, raw_path = self.root / (key + '.json'), self.root / (key + '.response')
            if meta_path.exists():
                info = json.loads(meta_path.read_text())
                if info['request'] != public_request:
                    raise ValueError('Cached request identity mismatch')
                if info.get('status') == 'ok' and sha(raw_path.read_bytes()) != info['raw_sha256']:
                    raise ValueError('Raw provider response changed')
            else:
                if len(list(self.root.glob('*.json'))) >= self.max_requests:
                    pending.append({'start': lo, 'end': hi, 'reason': 'request_budget'})
                    continue
                info = {'request': public_request, 'attempted_at': datetime.now(SHANGHAI).isoformat(),
                        'status': 'started', 'raw_file': raw_path.name}
                save(meta_path, info)
                try:
                    raw = self.request(api, params, FIELDS[api])
                    raw_path.write_bytes(raw)
                    info['raw_sha256'] = sha(raw)
                    body = json.loads(raw)
                    if body.get('code') != 0:
                        raise ValueError('provider_code_' + str(body.get('code')))
                    data = body.get('data')
                    if not isinstance(data, dict) or not isinstance(data.get('items'), list) or not isinstance(data.get('fields'), list):
                        raise ValueError('invalid_provider_schema')
                    required = {'title','content','datetime' if api == 'news' else 'pub_time'}
                    if len(set(data['fields'])) != len(data['fields']) or not required <= set(data['fields']):
                        raise ValueError('missing_or_duplicate_provider_fields')
                    if any(len(row) != len(data['fields']) for row in data['items']):
                        raise ValueError('provider_row_width')
                    info.update(status='ok', rows=len(data['items']), fields=data['fields'])
                except Exception as exc:
                    # Never log exception text: HTTP errors can contain credentials/response bodies.
                    info.update(status='failed', error_type=type(exc).__name__)
                save(meta_path, info)
            if info['status'] != 'ok':
                failures += 1
                pending.append({'start': lo, 'end': hi, 'reason': info['status']})
            elif info['rows'] >= cap:
                left, right = timestamp(lo), timestamp(hi)
                if (right - left).total_seconds() <= 1:
                    pending.append({'start': lo, 'end': hi, 'reason': 'saturated_minimum_window'})
                else:
                    middle = (left + timedelta(seconds=int((right-left).total_seconds()) // 2)).strftime('%Y-%m-%d %H:%M:%S')
                    # Shared endpoint avoids boundary losses; exact duplicates are retained for audit.
                    stack.extend([(middle, hi), (lo, middle)])
            else:
                completed.append({'start': lo, 'end': hi, 'request_id': key})
        return {'api': api, 'src': src, 'start': start, 'end': end, 'complete': not pending,
                'failed_requests': failures, 'pending_windows': pending, 'complete_windows': completed}


def provider_request(config):
    token, endpoint = config['token'], config['http_url']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def fetch(api, params, fields):
        body = json.dumps({'api_name': api, 'token': token, 'params': params, 'fields': fields}).encode()
        request = urllib.request.Request(endpoint, data=body, headers={'Content-Type': 'application/json'})
        with opener.open(request, timeout=35) as response:
            raw = response.read()
        if token.encode() in raw:
            raise ValueError('credential_echo_in_response')
        time.sleep(.3)
        return raw
    return fetch


def site_records(row):
    """Whitelist news fields only; all saved filtering/AI decisions remain separate."""
    namespace = row['namespace']
    if row.get('owner') != '' or namespace not in SITE_NAMESPACES:
        return
    p = json.loads(row['payload'])
    origin = {'provider': 'finpulse_site', 'namespace': namespace, 'record_key': row['record_key'],
              'record_payload_sha256': sha(row['payload'].encode()),
              'snapshot_created_at': row.get('created_at'), 'snapshot_updated_at': row.get('updated_at'),
              'collected_at': p.get('collected_at') or p.get('as_of'),
              'collection_target_code': p.get('code') or p.get('stock', {}).get('code')}
    def article(item, section, position, provider=None):
        date_fields = ('published_date','published_date_metadata','time','published_date_body','effective_date')
        dates = [item.get(k) for k in date_fields if item.get(k)]
        record = {'title': item.get('title') or '', 'url': item.get('original_url') or item.get('url') or '',
                  'content': item.get('raw_content') or item.get('cleaned_text') or item.get('content') or '',
                  'time_raw': next((v for v in dates if timestamp(v)), next(iter(dates),'')),
                  'source': item.get('source') or provider or '',
                  'date_metadata': {k:item[k] for k in (*date_fields,'date_status') if k in item},
                  'text_source': item.get('text_source') or ('raw_content' if item.get('raw_content') else 'search_snippet' if section in {'raw_search','industry_cache'} else 'site_saved_text')}
        where = {**origin, 'section': section, 'position': position}
        yield {'kind': 'article', 'origin': where, 'record': record}
        if section == 'audit':
            yield {'kind': 'legacy_filter_decision', 'origin': where,
                   'decision': {k:item[k] for k in ('status','reason_codes','date_status') if k in item}}
        if section == 'dashboard_news' and (item.get('analysis') is not None or item.get('score') is not None):
            yield {'kind': 'legacy_ai_judgment', 'origin': where,
                   'judgment': {k:item[k] for k in ('analysis','score','model','analyzed_at','analysis_prompt_version') if k in item},
                   'human_verified': False, 'training_label': False}
    if namespace == 'dashboard':
        for i, item in enumerate(p.get('news', [])):
            yield from article(item, 'dashboard_news', i)
    elif namespace == 'collection':
        for section in ('items', 'audit'):
            for i, item in enumerate(p.get('cleaning', {}).get(section, [])):
                yield from article(item, section, i)
        for group_index, group in enumerate(p.get('raw', {}).get('searches', [])):
            for i, item in enumerate(group.get('response', {}).get('results', [])):
                yield from article(item, 'raw_search', [group_index, i], group.get('provider'))
    elif namespace == 'industry_search':
        for i, item in enumerate((p.get('data') or {}).get('results', [])):
            yield from article(item, 'industry_cache', i)
    else:
        yield {'kind': 'profile_context_unverified', 'origin': origin,
               'profile': {k:(p.get('data') or {}).get(k) for k in ('code','name','industry','main_business','revenue_segments','note','products','upstream','downstream','regions','source','url')},
               'profile_fetched_at':p.get('fetched_at'), 'profile_status':p.get('status')}


def export_site(config_path, since):
    """Consistent read-only transaction; stream compressed records without a DB copy."""
    import pymysql
    c = json.loads(Path(config_path).read_text(encoding='utf-8-sig'))['database']
    conn = pymysql.connect(host=c.get('host','127.0.0.1'), port=int(c.get('port',3306)),
        user=c['username'], password=c['password'], database=c.get('name','finpulse_dev'),
        charset='utf8mb4', connect_timeout=10, read_timeout=120)
    try:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT')
        count, kinds = 0, Counter()
        with gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', mtime=0) as output:
            def emit(value):
                output.write((json.dumps(value,ensure_ascii=False,default=str)+'\n').encode())
            emit({'kind':'snapshot_metadata','since':since,'started_at':datetime.now(SHANGHAI).isoformat(),
                  'readonly':True,'allowed_namespaces':SITE_NAMESPACES,'owner_empty_only':True})
            with conn.cursor(pymysql.cursors.SSDictCursor) as cur:
                placeholders = ','.join(['%s']*len(SITE_NAMESPACES))
                cur.execute("SELECT namespace,record_key,owner,payload,created_at,updated_at FROM research_records WHERE owner='' AND namespace IN ("+placeholders+") AND updated_at >= %s ORDER BY namespace,record_key", (*SITE_NAMESPACES, int(timestamp(since).timestamp())))
                for row in cur:
                    try:
                        for value in site_records(row):
                            emit(value); kinds[value['kind']] += 1
                    except (ValueError, KeyError, TypeError, AttributeError) as exc:
                        emit({'kind':'record_failure','namespace':row['namespace'],
                              'record_key':row['record_key'],'error_type':type(exc).__name__})
                        kinds['record_failure'] += 1
                    count += 1
                    if count % 50 == 0:
                        print(json.dumps({'exported_db_records':count,'emitted':kinds}),file=sys.stderr,flush=True)
            emit({'kind':'snapshot_complete','db_records':count,'emitted':kinds,
                  'completed_at':datetime.now(SHANGHAI).isoformat()})
        conn.rollback()
    finally:
        conn.close()


def compact_article(value, seen):
    """Lossless content references for repeated snapshots, not semantic event deduplication."""
    if value['kind'] != 'article':
        return value
    record = value['record']
    identity = [record.get('title'),record.get('content')]
    if not all(identity): identity.append(record)
    key = sha(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode())
    if key in seen:
        return {**value,'kind':'article_observation','article_source_id':key,
                'record':{k:v for k,v in record.items() if k not in {'title','content'}}}
    seen.add(key)
    return {**value,'article_source_id':key}


class BodyParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.ignored = [], []
    def handle_starttag(self, tag, attrs):
        if tag in {'script','style','nav','footer','header'}:
            self.ignored.append(tag)
        if not self.ignored:
            if tag in {'p','div','br','tr','li','section'}: self.parts.append('\n')
            elif tag in {'td','th'}: self.parts.append(' | ')
    def handle_endtag(self, tag):
        if self.ignored and tag == self.ignored[-1]: self.ignored.pop()
        if not self.ignored and tag in {'p','div','tr','li'}: self.parts.append('\n')
    def handle_data(self, data):
        if not self.ignored: self.parts.append(data)


def normalize_article(record, origin, window=None):
    raw = str(record.get('content') or '')
    if re.search(r'<(?:p|div|table|script|nav|br)\b', raw, re.I):
        parser = BodyParser(); parser.feed(raw); content = ''.join(parser.parts).strip()
    else:
        content = html.unescape(raw).strip()
    time_raw = record.get('time_raw') or ''
    if not timestamp(time_raw):
        time_raw = next((v for k,v in record.get('date_metadata',{}).items() if k != 'date_status' and timestamp(v)), time_raw)
    flags, dt = [], timestamp(time_raw)
    if not content: flags.append('missing_body')
    elif len(re.sub(r'\s','',content)) < 80: flags.append('short_text_review_length_not_completeness')
    if content and content == record.get('title'): flags.append('title_only')
    if record.get('text_source') == 'search_snippet': flags.append('search_snippet_only')
    if not dt: flags.append('missing_or_invalid_time')
    elif window and not timestamp(window[0]) <= dt <= timestamp(window[1]): flags.append('outside_requested_window')
    if future_publication(dt, origin): flags.append('future_publication_date')
    # Text equality only identifies candidate copies. Dated observations stay distinct.
    key = [re.sub(r'\s+','',str(record.get('title') or '')), re.sub(r'\s+','',content)]
    if not all(key): key += [record.get('url'), record.get('time_raw'), origin]
    content_group_id = sha(json.dumps(key,ensure_ascii=False,sort_keys=True).encode())
    key.append(dt.isoformat() if dt else str(time_raw))
    return {'id':sha(json.dumps(key,ensure_ascii=False,sort_keys=True).encode()),
            'content_group_id':content_group_id,
            'title':record.get('title') or '', 'content':content, 'url':record.get('url') or '',
            'publication_time_raw':time_raw, 'publication_time':dt.isoformat() if dt else None,
            'time_basis':'provider_or_site_metadata; naive timestamps assumed Asia/Shanghai',
            'source':record.get('source') or '', 'source_content_sha256':sha(raw.encode()),
            'quality_flags':flags, 'observations':[{**origin,'publication_time_raw':record.get('time_raw') or '',
                'url':record.get('url') or '', 'source':record.get('source') or '',
                'source_content_sha256':sha(raw.encode()), 'quality_flags':flags,
                'date_metadata':record.get('date_metadata',{}), 'text_source':record.get('text_source')}], 'split':'unassigned',
            'label_status':'unlabelled', 'human_verified':False, 'training_admitted':False}


def unique_articles(rows):
    unique = {}
    for row in rows:
        if row['id'] not in unique:
            unique[row['id']] = row
        else:
            unique[row['id']]['observations'].extend(row['observations'])
            # Observation-specific defects do not disappear when another source is better.
            unique[row['id']]['quality_flags'] = sorted(set(unique[row['id']]['quality_flags']) | set(row['quality_flags']))
    return list(unique.values())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--site-config'); p.add_argument('--since')
    args = p.parse_args()
    if not args.site_config or not args.since: p.error('--site-config and --since required')
    export_site(args.site_config, args.since)


if __name__ == '__main__':
    main()
