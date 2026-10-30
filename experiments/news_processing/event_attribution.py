"""Conservative single-event attribution over a source-bound subject registry.

This is a research route, not an article filter. Unresolved actors stay unresolved.
No reviewed event subject, relation label, or model prediction is an input.
"""
import re
from .contract import RELATIONS
from .evidence import _verify
from .subjects import normalized_view


def unresolved(reason):
    return {'status':'UNRESOLVED','reason':reason,'actor_name':'','actor_code':'','evidence':[]}


def relation_route(attribution,prediction=None):
    """Combine a source check with an external-only model suggestion; never delete."""
    if attribution['status']=='OWN':
        return {'candidate_label':'OWN','route':'SOURCE_OWN','review_required':False,'delete_news':False}
    label=prediction['label'] if prediction is not None else 'UNKNOWN'
    if label not in RELATIONS:raise ValueError('External model must not classify OWN')
    review=attribution['status']=='UNRESOLVED' or label=='UNKNOWN'
    return {'candidate_label':label,'route':'REVIEW' if review else 'SHADOW_SUGGESTION',
            'review_required':review,'delete_news':False}


def public_signal(article,event_ids):
    evidence={x['id']:x['text'] for x in article['evidence']}
    if not event_ids or any(i not in evidence for i in event_ids):return unresolved('missing_event_evidence')
    event=''.join(evidence[i] for i in event_ids);context=''.join(evidence.values())
    if ('公司' not in event and
        (re.search(r'(?:我国|全国|规模以上).*(?:产销|产量|造船|增加值|总产量)',event,re.S) or
         (re.search(r'流通领域.*(?:市场|监测).*价格',context,re.S) and '产品名称' in event and '本期价格' in event) or
         (re.search(r'(?:国证|中证|行业).*指数',event,re.S) and re.search(r'收益率|指数.*(?:上涨|下跌|市场表现)',event,re.S)))):
        return {'status':'NOT_OWN','basis':'explicit_public_signal','actor_name':'公共行业或市场统计','actor_code':'',
                'evidence':[{'id':i,'quote':evidence[i]} for i in event_ids]}
    return unresolved('no_explicit_public_signal')


def attribute_event(registry,article_id,event_fragments,target_code,*,publication_date=''):
    if not event_fragments or any(not isinstance(x,str) or not x.strip() for x in event_fragments):
        return unresolved('missing_event_evidence')
    source=registry['sources'].get(article_id)
    if source is None:return unresolved('no_source_bound_article')
    packet=source['packet'];_verify(packet)
    full,positions=normalized_view(packet['visible']);intervals=[]
    for fragment in event_fragments:
        text,_=normalized_view(fragment);start=full.find(text)
        if start<0:return unresolved('event_quote_not_in_source')
        # Ambiguous title/signature fragments are not used as actor evidence.
        if full.find(text,start+1)>=0:continue
        intervals.append((start,start+len(text)))
    blocks=[]
    for start,end in intervals:
        if blocks and start==blocks[-1][1]:blocks[-1]=(blocks[-1][0],end)
        else:blocks.append((start,end))
    action=r'收到|获得|获批|批准|签署|达成|销售|实现|回购|提议|拟|计划|采购'
    chosen=next(((a,b) for a,b in blocks if re.search(action,full[a:b]) and not full[a:b].endswith('公告')),None)
    if chosen is None:return unresolved('no_unique_contiguous_event_clause')
    start,end=chosen;lo,hi=positions[start],positions[end-1]+1
    proof=[{'article_id':article_id,'article_sha256':packet['sha256'],'start':lo,'end':hi,'quote':packet['visible'][lo:hi]}]
    selected=packet['visible'][lo:hi]
    selected=re.sub(r'^[一二三四五六七八九十]+、[^\n，。]{1,35}(?:情况|事项|提示|简介|安排)\n','',selected)
    text,_=normalized_view(selected)
    title,_=normalized_view(packet['visible'].split('\n')[0])
    if text.startswith(title):text=text[len(title):]
    # Explicit historical, quoted, reported or hypothetical framing is not active own evidence.
    if re.search(r'如果|假设|一旦|倘若|曾(?:于|获得|获批)|早在|原研|历史|此前|资料显示|表示[，,：:]|指出[，,：:]|援引|引用|介绍[，,：:]',text):
        return unresolved('historical_reported_or_hypothetical')
    year=re.search(r'(?:已于|于)?(20\d{2})年.*?(?:获批|上市)',text)
    if year and publication_date and int(year[1])<int(publication_date[:4]):
        return unresolved('older_approval_background')
    text=re.sub(r'^(?:\d+[、.．]|[一二三四五六七八九十]+、)','',text)
    text=re.sub(r'^(?:近日|近期)[，,]?','',text)
    text=re.sub(r'^20\d{2}年\d{1,2}月(?:\d{1,2}日|份)?[，,]?','',text)
    issuer=source['source']['issuer_code']
    heading=packet['visible'][:600];normalized_heading,_=normalized_view(heading)
    local_aliases=[a for a in registry['companies'].get(issuer,{}).get('aliases',[])
                   if a['name'] in normalized_heading]
    names={a['name']:issuer for a in local_aliases}
    # Other issuers are resolved only by exact registered names, never fuzzy containment.
    for code,company in registry['companies'].items():
        if code!=issuer:
            for a in company['aliases']:names[a['name']]=code
    relations=[r for r in registry['relations'] if r['article_id']==article_id and r['parent_code']==issuer
               and r['proof']['article_sha256']==packet['sha256']]
    children={r['child_name']:r for r in relations}
    actor=next((name for name in sorted(set(names)|set(children),key=len,reverse=True) if text.startswith(name)),None)
    basis='exact_source_bound_subject';relation=None
    if actor:
        rest=text[len(actor):];code=names.get(actor,issuer)
        if actor in children:relation=children[actor];code=issuer;basis='article_subsidiary'
    elif text.startswith(('本公司','公司')) and local_aliases:
        actor='本公司' if text.startswith('本公司') else '公司';rest=text[len(actor):];code=issuer
        basis='issuer_pronoun_subject'
    else:return unresolved('subject_not_resolved')
    rest=re.sub(r'^（以下简称[^）]{0,80}）','',rest)
    child_prefix=re.match(r'^(?:的)?(?:全资子公司|控股子公司|子公司)',rest)
    if child_prefix:
        tail=rest[child_prefix.end():]
        child=next((n for n in sorted(children,key=len,reverse=True) if tail.startswith(n)),None)
        if not child:return unresolved('subsidiary_not_bound_in_this_article')
        actor=child;relation=children[child];code=issuer;basis='article_subsidiary';rest=tail[len(child):]
    if not re.match(r'^(?:董事会|董事长|与|及|于|向|收到|获得|获批|批准|签署|达成|销售|实现|通过|回购|提议|拟|计划|采购|已|现)',rest):
        return unresolved('identity_prefix_is_not_a_subject_boundary')
    if not re.search(r'收到|获得|获批|批准|签署|达成|销售|实现|回购|提议|拟|计划|采购',rest[:180]):
        return unresolved('no_active_event_predicate')
    if code!=target_code and any(a['name'] in rest for a in registry['companies'].get(target_code,{}).get('aliases',[])):
        return unresolved('target_mentioned_as_non_primary_party')
    return {'status':'OWN' if code==target_code else 'NOT_OWN','basis':basis,
            'actor_name':actor,'actor_code':code,'target_code':target_code,'evidence':proof,
            'issuer_code':issuer,'registry_sha256':registry['sha256'],
            'subsidiary_proof':relation}
