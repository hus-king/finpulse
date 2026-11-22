"""Explicit historical source collection; no scheduler, website or production DB."""
import argparse
import concurrent.futures
import copy
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

from .contract import digest
from .expansion import VERSION, input_hash, eligible, validate_roles
from .sources import announcement_date, source_identity
from .storage import write_json, write_jsonl, read_jsonl

SHANGHAI = timezone(timedelta(hours=8))
HEADERS = {'User-Agent':'Mozilla/5.0', 'Referer':'https://www.cninfo.com.cn/'}
TERMS = ['药品', '销售', '合同', '项目', '投资', '业绩', '担保', '回购', '诉讼', '补助', '产能', '技术']
ORG_IDS = {'600031':'gssh0600031','601919':'9900003201','002714':'9900022995',
           '600276':'gssh0600276','000977':'gssz0000977','600703':'gssh0600703','601336':'GD040482'}


def query_params(profile, term):
    code = profile['code']; org = profile.get('disclosure_org_id') or ORG_IDS.get(code)
    if not org: raise ValueError('Source-supported disclosure organization id required')
    return {'pageNum':1, 'pageSize':30, 'column':'szse' if code.startswith(('0','3')) else 'sse',
            'tabName':'fulltext', 'stock':code + ',' + org, 'searchkey':term, 'seDate':'2026-04-01~2026-09-30',
            'sortName':'time', 'sortType':'asc', 'isHLtitle':'false'}


def html_body(raw):
    soup = BeautifulSoup(raw, 'html.parser')
    node = soup.select_one('.TRS_Editor, .trs_editor_view, .TRS_UEDITOR, .article_content, #zoom, .ccontent, .contentText')
    if node is None: raise ValueError('No supported article body container')
    for child in node.select('script, style, nav, footer'): child.decompose()
    rows = []
    for table in node.select('table'):
        cells = table.select('th, td')
        if any(c.get('rowspan', '1') != '1' or (c.get('colspan', '1') != '1' and
               not (len(c.parent.find_all(['th','td'],recursive=False))==1 and c.get_text(' ',strip=True).startswith(('注：','注:')))) for c in cells):
            raise ValueError('Merged HTML table requires source review')
        table_rows = [[c.get_text(' ', strip=True) for c in row.find_all(['th','td'], recursive=False)]
                      for row in table.find_all('tr')]
        rows.extend(r for r in table_rows if r)
    return node.get_text('\n', strip=True), rows


def event_family(item):
    """Deliberately over-group; these are provisional families, not verified events."""
    if item.get('issuer'):
        title = item['title']
        category = next((kind for kind in ['回购', '药品', '销售', '担保', '合同', '诉讼', '补助', '业绩', '项目', '投资', '技术'] if kind in title), 'other')
        return 'issuer-family-' + item['issuer'] + '-' + category
    if item.get('publisher') == 'NBS': return 'macro-release-' + item['published_date']
    # Retain all MIIT overlapping monthly industry releases in one monthly group.
    return 'industry-release-' + item['published_date'][:7]


def source_metadata(item, raw, first_seen, fetch_info=None):
    info=fetch_info or {}
    return {'url': item['url'], 'published_date': item['published_date'],
            'published_precision': 'date_only', 'first_seen_at': first_seen,
            'first_seen_scope': 'actual collection time, not historical market availability',
            'raw_sha256': hashlib.sha256(raw).hexdigest(), 'publisher': item.get('publisher', 'CNINFO'),
            'issuer_code': item.get('issuer'), 'license_status': 'source_usage_not_admitted_for_training',
            'final_url':info.get('final_url'),'http_status':info.get('status'),
            'request_sha256':info.get('request_sha256'),'cache_metadata_file':info.get('cache_file'),
            'date_evidence':item.get('date_evidence','CNINFO disclosure date; date only')}


def merge_body_families(materials):
    parents={m['group_id']:m['group_id'] for m in materials}; seen={}
    def find(g):
        while parents[g]!=g: g=parents[g]
        return g
    for m in materials:
        body=m['body_sha256']
        if not body: continue
        g=find(m['group_id'])
        if body in seen:
            other=find(seen[body]); lo,hi=sorted([g,other]); parents[hi]=lo
        seen[body]=g
    for m in materials: m['group_id']=find(m['group_id'])


def fetch_cache(root, slot, url, data=None):
    rawpath = root / 'raw' / (slot + '.bin'); meta = root / 'raw' / (slot + '.json')
    if meta.exists():
        info = json.loads(meta.read_text())
        if info['url'] != url or info['request_sha256'] != hashlib.sha256(data or b'').hexdigest():
            raise ValueError('Cache request identity changed')
        if info.get('error'): return None, info
        raw = rawpath.read_bytes()
        if hashlib.sha256(raw).hexdigest() != info['sha256']: raise ValueError('Corrupt raw cache')
        return raw, info
    info = {'url':url, 'cache_file':'raw/'+slot+'.json', 'request_sha256':hashlib.sha256(data or b'').hexdigest(),
            'first_seen_at':datetime.now(SHANGHAI).isoformat()}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=HEADERS), timeout=35) as response:
            raw = response.read(20_000_001)
            if len(raw) > 20_000_000: raise ValueError('Raw body exceeds declared limit')
            info.update(final_url=response.url, status=response.status, sha256=hashlib.sha256(raw).hexdigest())
        import os
        temp=rawpath.with_suffix('.bin.tmp'); temp.write_bytes(raw); os.replace(temp,rawpath)
    except Exception as exc:
        raw = None; info.update(error=type(exc).__name__)
    write_json(meta, info)
    return raw, info


def collect(root, profiles_path, *, limit=300):
    root = Path(root); root.mkdir(parents=True, exist_ok=True); (root / 'raw').mkdir(exist_ok=True)
    if (root / 'protocol.json').exists(): raise ValueError('Already frozen; use label/resume, not recollection')
    profiles = json.loads(Path(profiles_path).read_text())
    if len(profiles) < 4: raise ValueError('At least four source-backed company profiles required')
    profile_sha = hashlib.sha256(Path(profiles_path).read_bytes()).hexdigest()
    declaration = {'created_at':datetime.now(SHANGHAI).isoformat(), 'target_materials':limit,
                   'target_distinct_reviewed_event_groups':300, 'pairs_per_material':4,
                   'window':['2026-04-01','2026-09-30'], 'sources':['CNINFO','NBS','MIIT'],
                   'terms':TERMS, 'profiles_sha256':profile_sha, 'company_codes':[p['code'] for p in profiles],
                   'nbs_listing_pages':8, 'roles':'hash of conservative family, fixed before teacher; provisional until source review',
                   'label_pilot_materials':24, 'max_body_chars':10000,
                   'training_admission':False, 'independent_test':False, 'automatic_deletion':False,
                   'synthetic_materials':0, 'development_only':True}
    dp = root / 'collection_declaration.json'
    if dp.exists() and {k:v for k,v in json.loads(dp.read_text()).items() if k!='created_at'} != {k:v for k,v in declaration.items() if k!='created_at'}:
        raise ValueError('Discovery declaration changed')
    if not dp.exists(): write_json(dp, declaration)
    write_json(root / 'profiles.json', profiles)
    tasks = [(p, term) for p in profiles for term in TERMS]

    def query(job):
        p, term = job
        params = query_params(p, term)
        raw, info = fetch_cache(root, 'query-' + p['code'] + '-' + term,
                               'https://www.cninfo.com.cn/new/hisAnnouncement/query', urllib.parse.urlencode(params).encode())
        if raw is None: return []
        value = json.loads(raw); results = []
        for e in value.get('announcements') or []:
            title = html.unescape(re.sub('<[^>]*>', '', e.get('announcementTitle',''))); day = announcement_date(e)
            if e.get('secCode') != p['code'] or not day or not '2026-04-01' <= day <= '2026-09-30': continue
            if any(x in title for x in ['年度报告','季度报告','半年度报告','摘要','英文','董事会决议','股东会','股东大会','章程','管理办法','议事规则','法律意见','独立董事','审核意见','制度']): continue
            adjunct = e.get('adjunctUrl','')
            if not adjunct.lower().endswith('.pdf'): continue
            results.append({'title':title,'issuer':p['code'],'published_date':day,'publisher':'CNINFO',
                            'url':'https://static.cninfo.com.cn/' + adjunct,'discovery_slot':info['url'] + '#' + p['code'] + '-' + term})
        return results
    candidates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(query, tasks): candidates.extend(result)
    # Download entire listings and preserve them before selecting any article body.
    for pub, urls in [('NBS', ['https://www.stats.gov.cn/sj/zxfb/' + ('' if i==0 else f'index_{i}.html') for i in range(8)]),
                      ('MIIT', ['https://www.miit.gov.cn/gxsj/index.html'])]:
        for i, url in enumerate(urls):
            raw, info = fetch_cache(root, f'listing-{pub}-{i}', url)
            if raw is None: continue
            soup = BeautifulSoup(raw.decode('utf-8', errors='replace'), 'html.parser')
            for a in soup.select('a[href]'):
                href = urllib.parse.urljoin(url, a['href']); title = a.get_text(' ', strip=True)
                if not title or not re.search(r'2026年',title): continue
                if pub=='NBS':
                    found = re.search(r'/2026\d{2}/t(2026\d{4})_',href)
                    if not found: continue
                    day = found[1][:4]+'-'+found[1][4:6]+'-'+found[1][6:]
                else:
                    # MIIT exact date is read from article metadata during extraction.
                    if '/art/' not in href: continue
                    day = ''
                if day and not '2026-04-01' <= day <= '2026-09-30': continue
                candidates.append({'title':title,'url':href,'published_date':day,'publisher':pub,'discovery_slot':url})
    seen = set(); unique = []
    # Public listings/primary issuer documents selected deterministically, never by teacher labels.
    for item in sorted(candidates, key=lambda x:(x['publisher'], x['published_date'], x['url'])):
        identity = source_identity(item['url'])
        if identity in seen: continue
        seen.add(identity); unique.append(item)
    # Balance rather than allowing prolific drug announcements to occupy all slots.
    buckets = {}
    for item in unique: buckets.setdefault(item.get('issuer',item['publisher']), []).append(item)
    selected = []
    while len(selected) < limit and any(buckets.values()):
        for key in sorted(buckets):
            if buckets[key] and len(selected)<limit: selected.append(buckets[key].pop(0))
    write_json(root / 'candidate_registry.json', unique); write_json(root / 'selected_sources.json', selected)

    def download(item):
        key = digest(source_identity(item['url']))[:20]
        return item, key, fetch_cache(root,key,item['url'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        downloaded = list(pool.map(download, selected))
    # PDF parsing is deliberately sequential to bound memory.
    materials = []; import io
    import pdfplumber
    for item, key, (raw, info) in downloaded:
        body=''; rows=[]; reasons=[]; pages=[]
        try:
            if raw is None: raise ValueError('Source fetch failed')
            if raw.startswith(b'%PDF'):
                with pdfplumber.open(io.BytesIO(raw)) as pdf:
                    for page in pdf.pages:
                        tables=page.extract_tables(); text=page.extract_text() or ''
                        pages.append({'page':page.page_number,'text':text,'tables':tables})
                        if any(sum(bool(re.search(r'\d',str(c))) for r in t for c in r if c) >= 6 for t in tables):
                            reasons.append('numeric_pdf_table_requires_visual_column_review')
                body='\n\n'.join(p['text'] for p in pages)
                write_json(root / 'raw' / (key + '.pages.json'), pages)
            else:
                htmlraw=raw.decode('utf-8',errors='replace')
                soup=BeautifulSoup(htmlraw,'html.parser')
                dates=[m.get('content','') for m in soup.select('meta[name="PubDate"],meta[name="pubdate"],meta[name="Date"],meta[name="publishdate"]')]
                dates=[d.replace('/','-')[:10] for d in dates if re.match(r'20\d{2}[-/]\d{2}[-/]\d{2}',d)]
                if not dates: dates=re.findall(r'20\d{2}-\d{2}-\d{2}',soup.get_text(' ',strip=True)[:2000])
                item['date_evidence']='article metadata or displayed date' if dates else 'URL path date only; pending'
                if dates:
                    if item['published_date'] and dates[0]!=item['published_date']:
                        reasons.append('listing_url_vs_article_date_conflict')
                    item['published_date']=dates[0]
                else: reasons.append('missing_article_publication_date')
                try: body,rows=html_body(htmlraw)
                except ValueError:
                    node=soup.select_one('.TRS_Editor, .trs_editor_view, .TRS_UEDITOR, .article_content, #zoom, .ccontent, .contentText')
                    if node: body=node.get_text('\n',strip=True)
                    raise
            if len(body.strip())<80: reasons.append('missing_complete_body')
            if len(body)>declaration['max_body_chars']: reasons.append('long_body_requires_section_review_no_truncation')
            if not '2026-04-01' <= item['published_date'] <= '2026-09-30': reasons.append('publication_date_outside_declared_window_or_missing')
        except Exception as exc: reasons.append(type(exc).__name__)
        source = source_metadata(item,raw or b'',info['first_seen_at'],info)
        material={'id':key,'title':item['title'],'issuer':item.get('issuer'),
                  'publisher':item['publisher'],'published_date':item['published_date'],
                  'source':source,'news':{'title':item['title'],'text':body,'market_rows':rows},
                  'body_sha256':hashlib.sha256(body.encode()).hexdigest() if body else None,
                  'raw_file':'raw/'+key+'.bin','quality':{'body':'pending' if reasons else 'ready',
                      'date':'ready' if item['published_date'] and '2026-04-01' <= item['published_date'] <= '2026-09-30' else 'pending'},
                  'quality_reasons':sorted(set(reasons)), 'event_group_review':'provisional_conservative_family',
                  'human_verified':False}
        material['group_id']=event_family({**item,'published_date':item['published_date'] or 'unknown'})
        materials.append(material)
        print(json.dumps({'material':key,'publisher':item['publisher'],'body':material['quality']['body'],'chars':len(body)},ensure_ascii=False),flush=True)
    merge_body_families(materials)
    roles={m['group_id']: ('train' if int(digest(m['group_id'])[:8],16)%10<7 else
                         'validation' if int(digest(m['group_id'])[:8],16)%10<9 else 'calibration') for m in materials}
    records=[]
    for i,m in enumerate(materials):
        idx=next((j for j,p in enumerate(profiles) if p['code']==m.get('issuer')),i%len(profiles))
        for j in range(4):
            profile=profiles[(idx+j)%len(profiles)]
            r={'id':m['id']+'__'+profile['code'],'group_id':m['group_id'],'split':roles[m['group_id']],
               'target_code':profile['code'],'state':{'company':copy.deepcopy(profile),'news':m['news']},
               'source':m['source'],'body_sha256':m['body_sha256'],
               'quality':{**m['quality'],'profile':'ready' if profile.get('selected_fact_evidence') and profile.get('products') else 'pending'},
               'quality_reasons':m['quality_reasons'],'event_group_review':m['event_group_review'],
               'development_only':True,'human_verified':False,'training_admitted':False}
            r['input_sha256']=input_hash(r); records.append(r)
    validate_roles(records)
    write_json(root / 'materials.json',materials); write_json(root / 'group_roles.json',roles)
    write_jsonl(root / 'inputs.jsonl',records)
    # The pilot samples material groups round-robin across publishers/issuers before seeing labels.
    ready=[m for m in materials if m['quality']['body']=='ready' and m['quality']['date']=='ready']
    label_ids={m['id'] for m in ready[:declaration['label_pilot_materials']]}
    pilot=[r for r in records if r['id'].split('__')[0] in label_ids]
    write_jsonl(root / 'label_inputs.jsonl',pilot)
    names=['collection_declaration.json','materials.json','profiles.json','inputs.jsonl','label_inputs.jsonl','group_roles.json']
    frozen={n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names}
    protocol={'version':VERSION,'frozen_files':frozen,'development_only':True,'independent_test':False,
              'human_verified':False,'production_enabled':False,'training_admitted':False,
              'teacher_policy':{'max_attempts':100,'token_soft_stop':700000,'batch_size':4,'workers':3,
                                'attempts_per_batch':2,'max_output_tokens':4500}}
    write_json(root / 'protocol.json',protocol)
    summary={'candidates':len(unique),'materials':len(materials),'provisional_families':len({m['group_id'] for m in materials}),
             'reviewed_distinct_events':0,'pairs':len(records),'eligible_pairs':sum(eligible(r) for r in records),
             'label_pilot_pairs':len(pilot),'companies':len(profiles),'training_admitted':False,'human_verified':False}
    write_json(root / 'collection_summary.json',summary); print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--root',type=Path,required=True)
    p.add_argument('--profiles',type=Path,required=True); p.add_argument('--limit',type=int,default=300)
    a=p.parse_args(); collect(a.root,a.profiles,limit=a.limit)
