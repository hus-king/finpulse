"""Lossless table-row ledger and literal temporal choices for source curation."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from . import development as d
from . import evidence as e
from .contract import digest
from .subjects import normalized_view


def _quotes(article,start,end):
    ids=[q['id'] for q in article['evidence'] if q['end']>start and q['start']<end]
    return ids,e.resolve(article,ids)


def table_units(record,block_size=8):
    if not isinstance(block_size,int) or isinstance(block_size,bool) or block_size<1:raise ValueError('Invalid block size')
    a=d.article_packet(record);rows=record['state']['news'].get('market_rows',[])
    serialized='\n'.join(' | '.join(str(c) for c in row) for row in rows)
    if not a['visible'].endswith(serialized):raise ValueError('Table differs from article')
    offset=len(a['visible'])-len(serialized);ledger=[];prices=[];header=None;specs=False
    for index,row in enumerate(rows):
        line=' | '.join(str(c) for c in row);ids,quotes=_quotes(a,offset,offset+len(line)) if line else ([],[])
        entry={'index':index,'cells':row,'start':offset,'end':offset+len(line),'evidence_ids':ids,
               'resolved_evidence':quotes,'row_sha256':digest(row),'role':'pending'}
        offset+=len(line)+1
        if len(row)==5 and row[:2]==['产品名称','单位']:
            header=row;specs=False;entry['role']='header'
        elif len(row)==4 and row[:2]==['序号','监测产品']:
            specs=True;entry['role']='specification_header'
        elif specs and len(row)==4:entry['role']='specification'
        elif row and row[0] and all(not str(v).strip() for v in row[1:]) and len(row)>1:entry['role']='category'
        elif len(row)==1 and str(row[0]).startswith(('注','说明')):entry['role']='footnote'
        elif header and len(row)==5:
            try:
                values=[Decimal(str(c).strip()) for c in row[2:]]
                if not row[0] or not row[1] or any(not v.is_finite() for v in values) or values[0]<=0:raise ValueError('Invalid price')
                if values[1]*values[2]<0:raise ValueError('Conflicting price change signs')
                if values[1]==0 and values[2]!=0:raise ValueError('Rounded change and percentage are ambiguous')
                entry['role']='price'
                prices.append({'row_index':index,'product':row[0],'unit':row[1],'price':str(row[2]),
                    'change':str(row[3]),'change_percent':str(row[4]),
                    'direction':'UP' if values[1]>0 else 'DOWN' if values[1]<0 else 'FLAT',
                    'evidence_ids':ids,'row_sha256':entry['row_sha256']})
            except (InvalidOperation,ValueError):pass
        ledger.append(entry)
    context={'title':record['state']['news']['title'],'headers':[r for r in ledger if r['role']=='header'],
             'footnotes':[r for r in ledger if r['role']=='footnote'],
             'specifications':[r for r in ledger if r['role'].startswith('specification')],
             'categories':[r for r in ledger if r['role']=='category']}
    pending=[r['index'] for r in ledger if r['role']=='pending']
    return {'article_id':a['id'],'article_sha256':a['sha256'],'source_rows_sha256':digest(rows),
            'total_rows':len(rows),'ledger':ledger,'price_rows':[p['row_index'] for p in prices],
            'pending_rows':pending,'complete':bool(prices) and not pending,'context':context,
            'block_size':block_size,'blocks':[{'row_indices':[p['row_index'] for p in prices[i:i+block_size]],
                'prices':prices[i:i+block_size]} for i in range(0,len(prices),block_size)]}


def validate_table(plan,record):
    # Rebuild against the independent frozen source, not a checksum supplied by the result.
    expected=table_units(record,plan['block_size'])
    if plan!=expected:raise ValueError('Missing, duplicated, reordered or changed source table rows')


def time_candidates(article):
    e._verify(article);text,positions=normalized_view(article['visible'])
    year=r'(?:19|20)\d{2}年'
    patterns=[('DAY',rf'(?:{year}\d{{1,2}}月\d{{1,2}}日|(?:19|20)\d{{2}}-\d{{2}}-\d{{2}})'),
              ('RANGE',rf'(?:{year})?(?:\d{{1,2}}[—–\-至]\d{{1,2}}月份?|[上下]半年|第[一二三四1-4]季度|\d{{1,2}}月[上中下]旬)'),
              ('RELATIVE',r'截至目前|截至\d{1,2}月底|回购期间|提议前\d+个月内|近日|本次|目前|年内'),
              ('MONTH',rf'(?:{year})?\d{{1,2}}月份?'),('YEAR',year)]
    candidates=[];covered=[]
    for precision,pattern in patterns:
        for match in re.finditer(pattern,text):
            if any(match.start()<hi and match.end()>lo for lo,hi in covered):continue
            normalized=match[0];day=''
            if precision=='DAY':
                numbers=[int(n) for n in re.findall(r'\d+',normalized)]
                try:day=date(*numbers).isoformat()
                except ValueError:
                    covered.append(match.span());continue
            start,end=positions[match.start()],positions[match.end()-1]+1
            ids,quotes=_quotes(article,start,end)
            item={'article_id':article['id'],'start':start,'end':end,'quote':article['visible'][start:end],
                  'normalized':normalized,'precision':precision,'event_date':day,
                  'evidence_ids':ids,'resolved_evidence':quotes}
            item['id']='T-'+digest({'article':article['sha256'],'start':start,'end':end})[:20]
            candidates.append(item);covered.append(match.span())
    return sorted(candidates,key=lambda c:c['start'])
