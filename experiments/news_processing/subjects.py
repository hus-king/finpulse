"""Source-bound issuer names and article-local subsidiary relationships."""
import hashlib
import re
from bisect import bisect_left
from pathlib import Path
from urllib.parse import urlsplit

from .contract import digest
from .evidence import article_packet, resolve


def normalized_view(text):
    """Only remove whitespace introduced by PDF layout; retain origin mapping."""
    positions=[i for i,c in enumerate(text) if not c.isspace()]
    return ''.join(text[i] for i in positions),positions


def _proof(packet,start,end,positions):
    lo,hi=positions[start],positions[end-1]+1
    ids=[q['id'] for q in packet['evidence'] if q['end']>lo and q['start']<hi]
    return {'article_id':packet['id'],'article_sha256':packet['sha256'],
            'evidence_ids':ids,'quotes':resolve(packet,ids),'matched_span':[lo,hi],
            'normalization':'whitespace_only'}


def build_registry(root,materials):
    root=Path(root).resolve();companies={};relations=[];sources={};pending=[]
    for m in materials:
        if m.get('quality',{}).get('body')!='ready':continue
        code=m.get('issuer');source=m['source']
        if not code or m.get('publisher') not in {'CNINFO','SSE','ISSUER'}:continue
        raw=(root/m['raw_file']).resolve()
        if not raw.is_relative_to(root) or hashlib.sha256(raw.read_bytes()).hexdigest()!=source['raw_sha256']:
            raise ValueError('Subject source raw digest mismatch')
        host=urlsplit(source['url']).hostname or ''
        if host not in {'static.cninfo.com.cn','static.sse.com.cn','www.petrochina.com.cn','www.fosunpharma.com'}:
            pending.append({'article_id':m['id'],'reason':'unsupported_primary_source'});continue
        if source.get('issuer_code')!=code:
            pending.append({'article_id':m['id'],'reason':'conflicting_source_issuer'});continue
        packet=article_packet(m['id'],m['news']);text,positions=normalized_view(packet['visible'])
        header=re.search(r'(?:A股)?证券代码[：:\s]*(\d{6})\s*(?:A股)?证券简称[：:\s]*([\u4e00-\u9fffA-Za-z*]{2,24}?)(?=\s|公告|编号|债券|H股|关于|[，,])',packet['visible'][:900])
        if not header or header[1]!=code:
            pending.append({'article_id':m['id'],'reason':'issuer_header_not_resolved'});continue
        sources[m['id']]={'packet':packet,'source':source,'raw_sha256':source['raw_sha256'],'url':source['url']}
        company=companies.setdefault(code,{'code':code,'aliases':[]})
        def add(name,kind,start,end):
            if name not in {a['name'] for a in company['aliases']}:
                company['aliases'].append({'name':name,'kind':kind,'issuer_code':code,'proof':_proof(packet,start,end,positions),
                                          'issuer_proof':_proof(packet,bisect_left(positions,header.start()),bisect_left(positions,header.end()),positions)})
        add(header[2],'security_short_name',bisect_left(positions,header.start(2)),bisect_left(positions,header.end(2)))
        # Use physical issuer-heading lines, never arbitrary body company mentions.
        formal=[]
        for match in re.finditer(r'(?m)^([\u4e00-\u9fff（）()A-Za-z]{4,60}?(?:股份有限公司|有限责任公司|有限公司))',packet['visible'][:600]):
            name=re.sub(r'\s+','',match[1])
            if name.startswith(('本公司','公司及','董事会')):continue
            start=next(i for i,p in enumerate(positions) if p==match.start(1));end=start+len(name)
            add(name,'formal_issuer_heading',start,end);formal.append(name)
            break
        parent_names=list(formal)
        if any(re.search(re.escape(name)+r'（以下简称[“"「]?公司[”"」]?）',text) for name in formal):parent_names.append('公司')
        if parent_names:
            pattern='(?P<parent>'+ '|'.join(re.escape(n) for n in sorted(parent_names,key=len,reverse=True))+r')(?:（[^）]{0,32}）)?(?:的)?(?P<relation>全资子公司|控股子公司|子公司)(?P<child>[\u4e00-\u9fff（）()A-Za-z]{2,80}?(?:有限责任公司|股份有限公司|有限公司))'
            for match in re.finditer(pattern,text):
                item={'parent_code':code,'child_name':match['child'],'relation':match['relation'],
                      'article_id':m['id'],'proof':_proof(packet,match.start(),match.end(),positions),
                      'human_verified':False,'scope':'this_article_only'}
                if item not in relations:relations.append(item)
    owners={}
    for code,c in companies.items():
        for a in c['aliases']:owners.setdefault(a['name'],set()).add(code)
    ambiguous=sorted(name for name,codes in owners.items() if len(codes)>1)
    for c in companies.values():c['aliases']=[a for a in c['aliases'] if a['name'] not in ambiguous]
    result={'version':'source-bound-subjects-v1','companies':companies,'relations':relations,
            'sources':sources,'pending':pending,'ambiguous_aliases':ambiguous,'human_verified':False}
    result['sha256']=digest(result)
    return result


def bindings(registry,code,article_id):
    return {'registry_sha256':registry['sha256'],'target_code':code,'aliases':registry['companies'].get(code,{}).get('aliases',[]),
            'relations':[r for r in registry['relations'] if r['parent_code']==code and r['article_id']==article_id],
            'human_verified':False}
