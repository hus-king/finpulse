"""Evidence-bound distant/conditional controls; AI silver, never automatic removal."""
import copy
import hashlib
import html
import json
import re

from . import bulk_dataset as bulk, evidence, targeted_dataset as targeted
from .contract import canonical, digest


FACT_PROMPT = targeted.FACT_PROMPT + '''
本轮必须再指定一个原子焦点：focus_quote为news_span内一段连续的完整事件原文（最多300字），focus_product为该焦点原文中的具体产品/市场/政策对象词（最多30字）。两者逐字引用，不生成总结。多商品涨跌综述只选一个完整商品事件，不把不同品种合并成一条因果事实；不能只选“上涨”等没有对象的词。无合格事件scope=UNKNOWN时两字段可空。focus_quote要有事件谓语及必要时间/主体，涉及具体公司时subject_name必须在focus_quote出现。保留全文以识别限制。
输出items各项增加"focus_quote":"原文","focus_product":"原文词"。'''
FACT_PROMPT += '''
如给了source_focus_anchor，本轮必须审该固定正文片段，不能换成同篇另一商品或公司事件。此时不输出focus_quote和focus_product，程序会绑定给定原文；只输出事实字段和覆盖该焦点的news_span。主体名称只引用固定焦点中出现的主体，不得借用同篇其他事件的主体。若片段不完整、不成事件，scope=UNKNOWN，不能改挑有利于目标公司的片段。完整正文用于还原时点、限定语和阶段；未提供目标公司或预期关系。未给anchor时才自行选择并输出focus_quote和focus_product。'''

FACT_REVIEW_PROMPT = targeted.FACT_PROMPT.split('只返回紧凑JSON')[0] + '''
你是独立来源审稿人。只审给定selected_event_ids和focus_quote对应的同一事件，不另选区间或焦点，不猜初标类别。可看全文识别背景、阶段、统计周期及限制。
核验focus_quote是否是原文中完整的单事件（focus_complete），focus_product是否准确指代该事件的产品或市场对象（focus_valid）。价格段落不能选没有该商品谓语的残句；表格需要表头和单位。不完整则false，不能以主观补完代替。
只返回JSON {"items":[{"id":"文章id","scope":"...","subject_name":"字面主体或空","subject_code":"字面代码或空","stage":"...","time_role":"...","focus_complete":true,"focus_valid":true}]}。不重写news_span、focus_quote、focus_product；不返回目标公司判断。'''
# The reviewer must use the same canonical stage/time rules as the primary annotator.
FACT_REVIEW_PROMPT += '\n统一阶段口径：' + targeted.FACT_PROMPT.split('统一阶段口径：',1)[1]

RELATION_PROMPT = targeted.RELATION_PROMPT + '''
本轮只判断focus_quote中的原子事件，其他原文是上下文，不能偷偷改用另一商品涨跌。industry_context为同一事件所有目标共享的、未经关系标注的年报主营或行业背景原文；它只能证明标明field的事实与产业环节存在，不能证明目标已经发生价格或利润变化。industry_background不证明发行人自己的采购或供应。只按报告期事实使用，不补持仓或具体供货。
选择最短有依据的经济传导：产品售价/采购成本/需求就是终点，成本→利润不是多一步。DIRECT是一跳的已披露同产品、同市场暴露；CONDITIONAL是一跳但具体地区、品种、采购、客户、资格等仍缺确认。DISTANT至少一个不同的中介产品/市场，不得把目标利润、行业景气或泛宏观当中介；不能为得到DISTANT绕远路。如果新闻产品本来就是目标明确投入，应按最短路径判断DIRECT或CONDITIONAL。
计步按具体物料/经济环节：原料A→加工材料B→采购B的设备公司，是存在不同中介材料的潜在两步传导；材料B报价→采购B成本→利润仍是一步。同一材料但地区或牌号缺确认归条件关联。若目标已直接采购A，即使也存在经B的路径，也不得绕远路称远关联。不能把原油、炼制成品油与航油天然视为同一产品；必须检查给定的加工、品种与业务证据。此处是标签定义示意，不是新闻样本或真实因果已实现的断言。
每个pair增加：endpoint_role=SELF|INPUT_COST|OUTPUT_PRICE|DEMAND|SUPPLY|COMPETITION|FINANCING|VALUATION|SECTOR_ACTIVITY|SENTIMENT|NONE|UNKNOWN；endpoint_product为引用的目标business_ids原文中的具体终点产品词，NONE/UNKNOWN/OWN/SENTIMENT可空。intermediates=[{"product":"新闻/industry_context原文中的具体中介词","news_ids":[],"bridge_ids":[]}]；没有中介必须[]。中介不得引用目标自己的主营充当外部证据。每个中介均须有原文id依据，所引新闻同一片段或同一家中介企业的主营引文中必须同时支持前一产品与本产品的相邻环节；第一环节从focus_product开始。不能把分别出现、没有相邻业务依据的几个词拼成链。终点产品也须与末级中介有共同主营依据或属于同一投入产品。全链条仍需说明待确认的传导条件，不声称引用已证明因果实现。
missing_axes为具体缺失类别数组：REGION|GRADE|HOLDING|CLIENT|REGULATORY_ELIGIBILITY|CONTRACT_PRICING|TRANSMISSION|TIME|OTHER。CONDITIONAL和DISTANT至少一个，其他可[]。path_basis=OBSERVED_EXPOSURE|CONDITIONAL_EXPOSURE|POTENTIAL_TRANSMISSION|SENTIMENT_ONLY|NO_SUPPORTED_PATH|INSUFFICIENT|SELF_EVENT，分别对应DIRECT、CONDITIONAL、DISTANT、SENTIMENT、NONE、UNKNOWN、OWN。
DISTANT必须有intermediates；CONDITIONAL不得用空泛“影响待观察”代替具体缺失暴露；DIRECT/CONDITIONAL不能有intermediates。NONE/UNKNOWN/SENTIMENT/OWN也不造中介。why最多65字，保留具体链条。只输出给定目标，格式仍为items[].pairs[]。'''

PROMPTS = {'facts_primary': FACT_PROMPT, 'facts_review': FACT_REVIEW_PROMPT,
           'relations_primary': RELATION_PROMPT,
           'relations_review': RELATION_PROMPT + '\n你独立审稿；没有提供其他模型的分类、路径和理由。'}
ENDPOINTS = {'SELF','INPUT_COST','OUTPUT_PRICE','DEMAND','SUPPLY','COMPETITION','FINANCING',
             'VALUATION','SECTOR_ACTIVITY','SENTIMENT','NONE','UNKNOWN'}
AXES = {'REGION','GRADE','HOLDING','CLIENT','REGULATORY_ELIGIBILITY','CONTRACT_PRICING',
        'TRANSMISSION','TIME','OTHER'}
BASIS = dict(OWN='SELF_EVENT', DIRECT='OBSERVED_EXPOSURE', CONDITIONAL='CONDITIONAL_EXPOSURE',
             DISTANT='POTENTIAL_TRANSMISSION', SENTIMENT='SENTIMENT_ONLY',
             NONE='NO_SUPPORTED_PATH', UNKNOWN='INSUFFICIENT')
CONTEXT_FIELDS = {'id','evidence_id','company_code','company_name','field','text','source_url',
                  'source_sha256','as_of','published_date'}


def industry_context(rows):
    """Only neutral, literal business evidence crosses into the student input."""
    if not isinstance(rows,list):raise ValueError('Missing industry evidence list')
    result=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)-CONTEXT_FIELDS:raise ValueError('Annotated or foreign industry context field')
        if any(not isinstance(row.get(k),str) or not row[k].strip()
               for k in ('id','evidence_id','company_code','company_name','text','source_url',
                         'source_sha256','as_of','published_date')):
            raise ValueError('Industry context needs literal source and owner')
        if row['id'] in seen:raise ValueError('Duplicate industry evidence')
        seen.add(row['id']);result.append(copy.deepcopy(row))
    return result


def check_context(rows,catalog,date):
    rows=industry_context(rows)
    for row in rows:
        owner=catalog.get(row['company_code'],{});source=owner.get('primary_profile_source',{})
        quote=next((q for q in owner.get('evidence',[])+owner.get('industry_evidence',[])
                    if q['id']==row['evidence_id']),None)
        if (row['company_name']!=owner.get('name') or row['source_url']!=source.get('url')
                or row['source_sha256']!=source.get('sha256') or not quote
                or row['text']!=quote['text'] or row.get('field')!=quote.get('field')):
            raise ValueError('Industry evidence differs from trusted company catalog')
        if not row['published_date']<=date or row['as_of']>date:
            raise ValueError('Future business evidence')
    return rows


def fastwire_receipt(article,raw_response,receipt,row_index):
    """Title==body is a full flash only when the returned content field proves it."""
    if (receipt.get('status')!='ok' or receipt.get('request',{}).get('api_name')!='news'
            or hashlib.sha256(raw_response).hexdigest()!=receipt.get('raw_sha256')):
        raise ValueError('Unverified fastwire response')
    raw=json.loads(raw_response);data=raw['data']
    if 'content' not in data['fields']:raise ValueError('Fastwire has no returned body field')
    row=dict(zip(data['fields'],data['items'][row_index]));body=str(row.get('content') or '')
    if (not body or hashlib.sha256(body.encode()).hexdigest()!=article.get('source_content_sha256')
            or html.unescape(body).strip()!=article['content']
            or str(row.get('title') or '')!=article['title']
            or row.get('datetime')!=article['publication_time_raw']
            or receipt['request'].get('params',{}).get('src')!=article['source']):
        raise ValueError('Fastwire receipt does not bind this article')
    return {'source_format':'full_fastwire','response_sha256':receipt['raw_sha256'],
            'row_index':row_index,'body_sha256':hashlib.sha256(body.encode()).hexdigest(),
            'news_original_url_verified':False}


def teacher_packet(stage,articles,catalog):
    packet=targeted.teacher_packet(stage,articles,catalog)
    for visible,a in zip(packet['articles'],articles):
        if stage=='facts_primary' and 'source_focus_anchor' in a:
            anchor=a['source_focus_anchor']
            if (not isinstance(anchor,dict) or set(anchor)!={'quote','product'}
                    or not all(isinstance(anchor[k],str) and anchor[k] for k in anchor)
                    or anchor['quote'] not in a['text'] or anchor['product'] not in anchor['quote']):
                raise ValueError('Nonliteral source focus anchor')
            visible['source_focus_anchor']=copy.deepcopy(anchor)
        elif stage=='facts_review':
            visible.update(focus_quote=a['fact']['focus_quote'],focus_product=a['fact']['focus_product'])
        elif stage.startswith('relations_'):
            visible['industry_context']=check_context(a.get('industry_context',[]),catalog,a['date'])
    return packet


def check_fact(article,raw,*,review=False):
    if not isinstance(raw,dict):raise ValueError('Missing source fact')
    if not review and 'source_focus_anchor' in article:
        anchor=article['source_focus_anchor']
        for key,source_key in [('focus_quote','quote'),('focus_product','product')]:
            echo=raw.get(key,anchor[source_key])
            missing_echo=echo==''
            contains_anchor=key=='focus_quote' and isinstance(echo,str) and anchor['quote'] in echo
            if echo!=anchor[source_key] and not missing_echo and not contains_anchor:
                raise ValueError('Annotator changed predeclared source anchor')
        raw={**raw,'focus_quote':anchor['quote'],'focus_product':anchor['product']}
    if review:
        if raw.get('focus_complete') is not True or raw.get('focus_valid') is not True:
            raise ValueError('Source reviewer rejected atomic focus')
        original=article['fact']
        for key in ('focus_quote','focus_product'):
            if key in raw and raw[key]!=original[key]:raise ValueError('Reviewer changed atomic focus')
        if 'news_span' in raw and raw['news_span']!=[original['news_ids'][0],original['news_ids'][-1]]:
            raise ValueError('Reviewer changed selected interval')
        raw={**raw,'news_span':[original['news_ids'][0],original['news_ids'][-1]],
             'focus_quote':original['focus_quote'],'focus_product':original['focus_product']}
    answer=targeted.check_fact(article,raw,review=review)
    quote,product=raw.get('focus_quote'),raw.get('focus_product')
    if not isinstance(quote,str) or not isinstance(product,str):raise ValueError('Missing literal atomic focus')
    selected=evidence.resolve(article['packet'],answer['news_ids'])
    visible=article['packet']['visible'][selected[0]['start']:selected[-1]['end']] if selected else ''
    if answer['scope']!='UNKNOWN':
        if not quote or len(quote)>300 or quote not in visible or quote not in article['text']:
            raise ValueError('Focus absent from selected source body')
        if not product or len(product)>30 or product not in quote:raise ValueError('Focus product not literal')
        if 'source_focus_anchor' in article and {'quote':quote,'product':product}!=article['source_focus_anchor']:
            raise ValueError('Annotator changed predeclared source anchor')
        # A Chinese clause can inherit its subject from the preceding clause.
        # bulk.validate_item binds the actor to the continuous selected context;
        # blind fact review checks that it is the actor of this fixed focus.
    answer.update(focus_quote=quote,focus_product=product)
    return answer


def check_pair(article,catalog,pair):
    """Check provenance and path structure without inventing a replacement label."""
    code=pair['code'];relation=pair['relation'];role=pair.get('endpoint_role')
    if role not in ENDPOINTS or pair.get('path_basis')!=BASIS[relation]:raise ValueError('Missing or contradictory endpoint status')
    fixed={'OWN':'SELF','NONE':'NONE','UNKNOWN':'UNKNOWN','SENTIMENT':'SENTIMENT'}
    if relation in fixed and role!=fixed[relation]:raise ValueError('Endpoint does not match relation')
    if relation not in fixed and role in set(fixed.values()):raise ValueError('Missing fundamental endpoint')
    product=pair.get('endpoint_product')
    if not isinstance(product,str):raise ValueError('Missing endpoint product')
    target={q['id']:q['text'] for q in catalog[code]['evidence']}
    clean=lambda s:re.sub(r'\s+','',s)
    if relation not in fixed and (not product or len(product)>50 or not any(
            clean(product) in clean(target[i]) for i in pair['business_ids'])):
        raise ValueError('Endpoint product absent from cited target business')
    axes=evidence._strings(pair.get('missing_axes'),'missing_axes')
    if set(axes)-AXES or (relation in {'CONDITIONAL','DISTANT'} and not axes):raise ValueError('Missing concrete exposure axis')
    nodes=pair.get('intermediates')
    if not isinstance(nodes,list) or len(nodes)>3:raise ValueError('Invalid intermediate chain')
    if bool(nodes)!=(relation=='DISTANT'):raise ValueError('Intermediate count contradicts shortest relation')
    news={q['id']:q['text'] for q in article['packet']['evidence'] if q['id'] in article['fact']['news_ids']}
    bridges={q['id']:q for q in check_context(article.get('industry_context',[]),catalog,article['date'])}
    previous=article['fact']['focus_product'];seen={clean(previous)}
    def adjacent(left,right,nids,bids,*,target_ids=()):
        groups={}
        for i in bids:groups.setdefault(bridges[i]['company_code'],[]).append(bridges[i]['text'])
        if target_ids:groups[code]=[target[i] for i in target_ids]
        texts=[news[i] for i in nids]+[''.join(values) for values in groups.values()]
        return any(clean(left) in clean(t) and clean(right) in clean(t) for t in texts)
    for node in nodes:
        if not isinstance(node,dict):raise ValueError('Invalid intermediate')
        word=node.get('product')
        if not isinstance(word,str) or not word.strip() or len(word)>50 or re.fullmatch(
                r'(?:目标)?(?:企业|公司|行业|宏观|经济|市场|流动性|利润|盈利|成本|收入|景气|需求|供给)(?:变化|改善|预期)?',word):
            raise ValueError('Generic profit or macro endpoint is not an intermediate')
        nids=evidence._strings(node.get('news_ids'),'intermediate news_ids')
        bids=evidence._strings(node.get('bridge_ids'),'bridge_ids')
        if not nids and not bids:raise ValueError('Unproven intermediate')
        if set(nids)-set(news) or set(bids)-set(bridges):raise ValueError('Foreign intermediate evidence')
        if any(bridges[i]['company_code']==code for i in bids):raise ValueError('Target cannot serve as its own external intermediary')
        texts=[news[i] for i in nids]+[bridges[i]['text'] for i in bids]
        if not any(clean(word) in clean(text) for text in texts):raise ValueError('Intermediate product absent from cited evidence')
        if clean(word) in seen:raise ValueError('Repeated or redundant intermediate')
        if not adjacent(previous,word,nids,bids):raise ValueError('No shared source or business support for adjacent products')
        previous=word;seen.add(clean(word))
    if nodes and clean(previous)!=clean(product) and not adjacent(previous,product,
            nodes[-1]['news_ids'],nodes[-1]['bridge_ids'],target_ids=pair['business_ids']):
        raise ValueError('Unsupported intermediate to endpoint link')
    return copy.deepcopy(pair)


def check_relations(article,catalog,raw):
    answer,errors=targeted.check_relations(article,catalog,raw);valid=[]
    for pair in answer['pairs']:
        try:valid.append(check_pair(article,catalog,pair))
        except (ValueError,KeyError,TypeError) as exc:errors[pair['code']]=str(exc)
    answer['pairs']=valid
    return answer,errors


def pair_agreement(left,right):
    if not left or not right:return False
    clean=lambda x:re.sub(r'\s+','',x)
    if any(left[k]!=right[k] for k in ('relation','endpoint_role','path_basis')):return False
    if clean(left['endpoint_product'])!=clean(right['endpoint_product']):return False
    if left['relation'] in {'CONDITIONAL','DISTANT'} and not set(left['missing_axes']) & set(right['missing_axes']):return False
    if len(left['intermediates'])!=len(right['intermediates']):return False
    for a,b in zip(left['intermediates'],right['intermediates']):
        if clean(a['product'])!=clean(b['product']):return False
        if not (set(a['news_ids']) & set(b['news_ids']) or set(a['bridge_ids']) & set(b['bridge_ids'])):return False
    return True


def example(article,catalog,event,pair=None,*,reviewed=False):
    row=bulk.example(article,catalog,event,pair,reviewed=reviewed)
    inputs=json.loads(row['messages'][1]['content'])
    inputs['event_focus']={'quote':event['focus_quote'],'product':event['focus_product']}
    if pair is not None:inputs['industry_context']=industry_context(article.get('industry_context',[]))
    row['messages'][1]['content']=canonical(inputs)
    row['sha256']=digest({k:v for k,v in row.items() if k!='sha256'})
    return row
