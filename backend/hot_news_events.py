"""Newsworthiness gates: a new concrete event before any economic inference."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class NewsEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['policy', 'conflict', 'disruption', 'safety', 'product', 'company', 'technology', 'economic_data']
    actor: str = Field(min_length=2, max_length=60)
    action: str = Field(min_length=2, max_length=80)
    new_fact: str = Field(min_length=8, max_length=180)
    novelty: Literal['new_development', 'background', 'unclear']
    importance: Literal['major_public', 'major_industry', 'major_brand', 'minor', 'unclear']
    attribution: Literal['announcement', 'reported', 'reported_claim']


EVENT_PROMPT = '''先判断“这是不是一条重要的新事件”，再判断市场入口，不能因为能联想到行业就纳入。
优先具有具体指向的全民热点：重大言论引发品牌争议/解约、知名产品安全质疑及监管新进展，不能因娱乐人物或事故未定性就直接过滤。泛泛“正能量、伟大征程、谱写新篇章”等宣传，未指向明确措施、企业、产品或行业变化时排除。
保留须同时满足：有明确主体及具体发生/宣布的新变化；属于国内国际大事、重要产业事件或知名品牌的重要产品/安全事件；材料能说明新增事实，而不是靠假设扩大影响。
“半突发”包括新政策、重大新品发布、产品安全争议、停航停产、正式召回、监管调查、重要公司突发经营变化及重大技术落地；计划内发布也可以是新事件，不要求一定是事故。
排除：趋势观察、成就宣传、生活消费现象/技巧、一般地方小事、个人体验、日常报价、例行财报解读、泛行业评论、无新增量的旧闻复述。不能靠“如果推广会带动设备需求”“或许影响情绪”放大小事。
特别排除：ETF资金流入/产品宣传、CPO概念反弹/某股涨停、食品饮料板块异动、期货黄金点评、港股早报/隔夜要闻等盘面汇总。例行季度产量、地区进出口统计即使“创同期新高”也不是重要突发变化；只有重大项目投产停产、实质政策/安全/经营变化等才考虑。知名品牌的服务口号或常规公关表态也不因品牌知名就变成重大事件。不要从一篇盘面综述中的只言片语抽一个大事件来保留整个综述。
正例：美国宣布加征关税；主要航道受袭停航；央行宣布降准；知名车企大规模召回；尊界V800制动测试争议后厂家回应或监管调查；苹果发布iPhone Duo及开售后有出处的屏裂反馈/渠道价格异常。传言本身不等于事故属实；可报道“测试方称/媒体报道用户反馈/官方启动调查”，保留车型与各方归属，不把问界、尊界或关联公司混为一谈；渠道降价不能写成已证实销量暴跌。
反例：“看不见的外贸”畅跑全球、超长预售45天原来是在等退货回来、冷冻烘焙卖3块精品面包卖30块、人造古镇为啥集体哑火、有KTV的列车火了、深圳禁鸣喇叭那么多车咋知道谁按的。这些是背景/消费现象或小事；即使摘要出现外贸、库存、AI、设备等词也不纳入。
对“内存价格倒挂”“饮料行业怎么了”等，只有明确重要主体及刚发生的重大变动和新数据才考虑，不把长期问题当作突发事件。依据不足review，宁缺毋滥，不填满榜单。
每项增加event：不保留可为null；keep必须是对象：kind(policy/conflict/disruption/safety/product/company/technology/economic_data)、actor(材料中逐字出现的主体)、action(具体动作)、new_fact(8至180字逐字引用新增事实)、novelty(new_development/background/unclear)、importance(major_public/major_industry/major_brand/minor/unclear)、attribution(announcement/reported/reported_claim)。
keep只允许new_development与三种major重要性；不要用后来可能发生的事充当new_fact。标题和摘要不是独立事实核验，也不能证明准确的新闻日期。'''


def editorial_reason(topic):
    title = topic['title']
    material = title + '\n' + topic.get('summary', '')
    if re.search(r'伟大征程|奋进新征程|正能量.{0,10}征程|谱写.{0,12}新篇章', title) and not re.search(r'关税|召回|禁令|监管调查|停产|停航|正式投产|签署.{0,12}(?:协议|合同)', title):
        return '泛宣传或口号，没有具体可定位的新事件及影响对象。'
    if re.search(r'(?:维持|给予|重申|予以|予).{0,15}(?:买入|增持|减持|中性|卖出|跑赢|优于).{0,5}评级|目标价|投资策略|研报解读|组织提效|转型观察', title):
        return '投资观点、评级或应用趋势宣传，不作为独立的重要新事件。'
    if re.search(r'早报|晨报|收评|午评|盘前必读|盘面综述|隔夜.{0,6}要闻|全球要闻|(?:期货|黄金|金价).{0,15}点评|概念.{0,8}(?:反弹|涨停|拉升)|板块.{0,8}(?:异动|拉升|涨停)', title):
        return '行情播报、投资点评或多事件汇总，不是单一重要新事件。'
    if re.search(r'ETF|交易型开放式指数基金', title, re.I) and re.search(r'净流入|领跑|加码|交易活跃|吸金|资金|涨幅', title) and not re.search(r'立案|监管处罚|欺诈|清盘|退市', title):
        return '基金产品宣传或资金/行情播报，不以行业联想提升为新闻事件。'
    if re.search(r'前三季度|一季度|二季度|三季度|上半年|前[三六九]月', title) and re.search(r'产量|进出口|创历史|同比', title) and not re.search(r'停产|召回|破产|首次亏损|立案|重大事故', material):
        return '例行周期统计或产量宣传，没有新的重大突发变化。'
    if re.search(r'服务口号|服务理念|服务宣言|顾客.{0,6}上帝|员工.{0,6}也是人', title):
        return '服务理念或公关表态，没有材料支持重要的新经营变化。'
    commentary = re.search(r'看不见的|畅跑全球|原来是|怎么了|为啥|为何|到底有多|咋知道|早晚问题|集体哑火|卖\d+块|点唱|保值率解读|购物攻略|穿搭|红毯同框|复盘|回顾', title)
    # Concrete reported developments may appear inside an explanatory headline.
    development = re.search(r'宣布|签署|立案|联合调查|备案召回|决定召回|发布.{0,10}(?:禁令|召回|新规)|正式(?:开市|投产|开售)|停产|停航|发动空袭|发生.{0,6}(?:爆炸|泄露)|测试.{0,15}(?:断裂|踩断)|用户(?:反馈|反映).{0,15}(?:屏裂|裂开|断裂)', material)
    if commentary and not development:
        return '现象讨论、背景解读或日常消费话题，未见重要主体刚发生的具体事件，不因可联想到行业而纳入。'
    if re.search(r'涨幅扩大|跌幅扩大|收报|指数.{0,10}[涨跌]\d|[涨跌]幅.{0,8}[0-9]+%', title) and not re.search(r'关税|禁令|战争|停火|制裁|召回|熔断|历史新|创纪录', material):
        return '例行行情报价，没有新的重要事件，不作为新闻事件入选。'
    return None


def rule_event(topic):
    """Conservative fallback. Unknown importance stays unselected, never guessed."""
    text = topic['title'] + '\n' + topic.get('summary', '')
    if editorial_reason(topic) or re.search(r'网传|传闻|假如|如果|是否|会不会|否认|辟谣|复盘|回顾|抗日|抗战|二战|一战|[？?]', text):
        return None
    kind = actor = action = None
    channels = []
    importance = 'major_public'
    authority = re.search(r'中国人民银行|央行|美联储|国务院|工信部|市场监管总局|欧盟|美国|中国|伊朗|以色列|俄罗斯|乌克兰|日本', text)
    policy = re.search(r'宣布|签署|决定|发布|实施|启动', text)
    if authority and policy and re.search(r'关税|出口管制|进口禁令|降准|降息|加息|制裁|禁售', text):
        kind, actor, action, channels = 'policy', authority, policy, ['regulation', 'trade'] if re.search(r'关税|出口|进口|制裁', text) else ['funding', 'demand']
    route = re.search(r'霍尔木兹海峡|苏伊士运河|红海航道|红海|台湾海峡', text)
    disruption = re.search(r'封锁|关闭|中断|停航|受阻|袭击|禁航', text)
    if not kind and route and disruption:
        kind, actor, action, channels = 'disruption', route, disruption, ['supply', 'cost']
    fighting = re.search(r'宣布停火|达成停火|发动空袭|发动袭击|爆发冲突|宣布开战', text)
    if not kind and authority and fighting:
        kind, actor, action, channels = 'conflict', authority, fighting, ['risk', 'supply']
    brand = re.search(r'小米|特斯拉|尊界|问界|苹果|Apple|iPhone|华为|三星|英伟达|长鑫|奥迪|波音|CrowdStrike|微软', text, re.I)
    safety = re.search(r'召回|立案调查|联合调查|(?:踏板|支架).{0,8}(?:断裂|踩断)|(?:屏幕|内屏).{0,6}(?:裂开|开裂)|屏裂|大规模.{0,6}(?:宕机|故障)|全球.{0,6}(?:宕机|故障)', text)
    attribution = 'announcement'
    if not kind and brand and safety:
        kind, actor, action, channels = 'safety', brand, safety, ['risk', 'regulation']
        importance = 'major_brand'
        attribution = 'reported_claim' if re.search(r'测试|反馈|反映|投诉', text) and not re.search(r'召回|联合调查|立案调查', text) else 'reported'
    product = re.search(r'发布|正式开售|全球首发|停售|停止销售', text)
    if not kind and brand and product and re.search(r'iPhone|旗舰|首款.{0,8}(?:折叠|芯片|大模型)|新一代.{0,6}(?:芯片|大模型)', text, re.I):
        kind, actor, action, channels, importance = 'product', brand, product, ['demand', 'supply'], 'major_brand'
    company = re.search(r'宣布破产|申请破产|宣布退市|宣布收购|停止供货|拒绝.{0,10}(?:供货|订单)|双品牌架构|正式分家', text)
    if not kind and brand and company:
        kind, actor, action, channels, importance = 'company', brand, company, ['supply', 'risk'], 'major_brand'
    grid = re.search(r'南方区域电力市场|全国统一电力市场|国家重大水利工程', text)
    opening = re.search(r'正式开市|正式投产|正式开工|批准开工', text)
    if not kind and grid and opening:
        kind, actor, action, channels, importance = 'policy', grid, opening, ['regulation', 'supply'], 'major_industry'
    disaster = re.search(r'台风|地震|洪水', text)
    if not kind and disaster and re.search(r'多地|大范围|全国|主要港口|多座工厂', text) and re.search(r'停产|停航|停工|封港', text):
        kind, actor, action, channels = 'disruption', disaster, re.search(r'停产|停航|停工|封港', text), ['supply', 'risk']
    if not kind:
        return None
    start = max(0, min(actor.start(), action.start()) - 10)
    quote = text[start:start + 160].strip()
    if len(quote) < 8:
        return None
    return {'event': {'kind': kind, 'actor': actor.group(), 'action': action.group(), 'new_fact': quote,
                      'novelty': 'new_development', 'importance': importance, 'attribution': attribution},
            'channels': channels}


def validate_event(event, material):
    if not event or event['novelty'] != 'new_development' or event['importance'] not in ('major_public', 'major_industry', 'major_brand'):
        raise ValueError('Kept topic lacks an important new event')
    if event['actor'] not in material or event['new_fact'] not in material:
        raise ValueError('Event subject or new fact is not in source material')
