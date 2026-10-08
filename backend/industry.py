"""Industry-wide search topics and evidence rules, independent of stock names."""
import re

PROFILE_VERSION = 'industry-v2'
PROFILE_NOTE = '行业分类来自个股资料；未取得主营业务细分和收入占比，行业关联不等于已确认的公司经营影响。'
EVENT = re.compile(r'政策|监管|供需|需求|订单|价格|上涨|下跌|回落|增长|下降|调整|产能|产量|销量|库存|出口|进口|关税|补贴|采购|集采|审批|营收|利润|成本|融资|利率|限制|禁止|供应|减产|增产|招标|中标|投资|开工|投产')

# Patterns match source industry classifications; new classifications always
# retain a generic topic even when none of these optional factors match.
RULES = [
    (r'石油|石化|油气|炼化|炼油|油服', [('原油价格', ['原油', '油价', '布伦特', 'WTI']), ('能源供需', ['OPEC', '欧佩克', '原油供应', '天然气', '成品油'])]),
    (r'银行', [('利率与信贷', ['利率', '降息', '降准', '信贷', '贷款']), ('银行监管', ['银行监管', '资本充足率', '不良贷款', '存款利率'])]),
    (r'证券|保险|金融|多元金融', [('金融政策', ['资本市场', '金融监管', '保险监管', '证券监管', '利率']), ('业务环境', ['保费', '券商', '证券交易', '保险行业'])]),
    (r'半导体|芯片|集成电路', [('产业政策', ['芯片', '半导体', '出口管制', '集成电路']), ('芯片供需', ['晶圆', '半导体设备', '存储芯片', '先进制程'])]),
    (r'汽车|汽配|乘用车|商用车', [('汽车需求', ['汽车销量', '新能源汽车', '汽车补贴', '以旧换新']), ('汽车产业政策', ['汽车关税', '汽车出口', '汽车行业', '动力电池'])]),
    (r'电池|新能源', [('电池材料', ['碳酸锂', '锂价', '锂矿', '电池材料']), ('电池需求', ['动力电池', '储能', '装机量', '新能源汽车'])]),
    (r'医药|制药|生物|医疗|中药|化学制药', [('医药政策', ['药品', '集采', '医保', '药监', '医疗器械']), ('医药需求', ['医药行业', '创新药', '疫苗', '临床试验'])]),
    (r'房地产|地产开发|地产服务', [('地产政策', ['房地产', '房贷', '住房', '购房']), ('地产供需', ['商品房', '房企', '土地市场', '住宅销售'])]),
    (r'农|牧|渔|饲料|养殖|种植', [('农产品价格', ['生猪', '猪价', '玉米', '大豆', '粮价', '农产品']), ('农业供需', ['饲料', '养殖', '疫病', '农业政策'])]),
    (r'钢铁|有色|金属|矿业|采矿', [('商品价格', ['钢价', '铁矿石', '铜价', '铝价', '金价', '有色金属']), ('金属供需', ['钢铁', '金属库存', '矿产', '冶炼'])]),
    (r'煤炭', [('煤炭价格', ['煤价', '动力煤', '焦煤', '焦炭']), ('煤炭供需', ['煤炭', '煤矿', '电煤', '煤炭进口'])]),
    (r'光伏|风电|电力设备', [('能源设备', ['光伏', '硅料', '风电', '电网']), ('能源政策', ['可再生能源', '新能源消纳', '电力设备', '储能政策'])]),
    (r'电力|燃气|公用事业', [('公用事业价格', ['电价', '气价', '天然气', '煤价']), ('公用事业政策', ['电力市场', '电力需求', '燃气', '供电'])]),
    (r'轨交|轨道|铁路设备|铁路装备', [('轨交装备需求', ['铁路固定资产投资', '铁路投资', '高铁建设', '动车组采购', '列车采购', '铁路建设', '轨交设备', '轨道交通建设', '动车组产能', '动车组订单', '地铁建设', '城轨建设']), ('轨交政策', ['铁路规划', '铁路招标', '列车采购', '轨道交通政策'])]),
    (r'自动化|机器人|通用设备|专用设备|仪器仪表', [('设备更新需求', ['设备更新', '工业机器人销量', '工业机器人订单', '机器人量产', '自动化设备订单', '工业母机', '制造业投资', '工业装备']), ('设备产业政策', ['智能制造', '设备更新政策', '工业装备'])]),
    (r'航空|机场', [('燃油成本', ['航空燃油', '航油', '油价', '原油']), ('航空需求', ['航空', '机场', '客运', '机票'])]),
    (r'航运|港口|物流|运输|铁路', [('运输需求', ['航运', '港口', '物流', '货运', '铁路']), ('运输价格', ['运价', '运费', '燃油', '集装箱'])]),
    (r'白酒|酿酒|食品|饮料', [('消费需求', ['白酒', '食品', '饮料', '消费', '动销']), ('食品政策', ['食品安全', '消费税', '酿酒', '食品行业'])]),
    (r'化工|化学|化肥|橡胶|塑料', [('化工价格', ['化工', '化肥', '橡胶', '塑料', '原油']), ('化工供需', ['化工产能', '化学品', '化工出口', '环保政策'])]),
    (r'计算机|软件|信息技术|互联网|通信|电子', [('技术与需求', ['人工智能', '算力', '云计算', '软件', '通信', '消费电子']), ('技术政策', ['数据安全', '网络安全', '信息技术', '通信监管'])]),
    (r'建筑|建材|水泥|工程', [('建设需求', ['基建', '建筑', '水泥', '建材', '工程']), ('建设成本', ['钢价', '建材价格', '专项债', '基础设施'])]),
    (r'家电|家具|家居|零售|商贸|纺织|服装', [('消费政策', ['消费', '补贴', '以旧换新', '关税']), ('消费需求', ['零售', '家电', '家具', '纺织', '服装'])]),
    (r'旅游|酒店|餐饮|休闲', [('旅游需求', ['旅游', '酒店', '餐饮', '出行']), ('旅游政策', ['旅游政策', '签证', '免税', '文旅'])]),
    (r'传媒|游戏|出版|影视|文化', [('文化政策', ['游戏版号', '出版', '影视', '传媒', '文化政策']), ('文化需求', ['票房', '游戏', '广告市场', '电影'])]),
]


def valid_industry(value):
    return isinstance(value, str) and 2 <= len(value.strip()) <= 60 and value.strip() not in ('A 股', 'A股', '-', '--', '未知', '其他', 'None', 'nan')


def industry_keyword(industry):
    # Classification levels (e.g. 银行Ⅱ) are labels, not words used in articles.
    return re.sub(r'[ⅠⅡⅢⅣⅤ]+$', '', industry.strip()).strip()


def factors_for(industry):
    if not valid_industry(industry):
        return []
    factors = []
    for pattern, rows in RULES:
        if re.search(pattern, industry):
            factors.extend(rows)
    return factors


def build_topics(industry):
    if not valid_industry(industry):
        return []
    industry = industry.strip()
    topics = [{'industry': industry, 'label': '行业动态', 'query': f'{industry_keyword(industry)} 行业 政策 供需 价格 新闻', 'version': PROFILE_VERSION}]
    factors = factors_for(industry)
    if factors:
        terms = list(dict.fromkeys(term for _, aliases in factors for term in aliases))[:8]
        topics.append({'industry': industry, 'label': '关键影响因素', 'query': ' '.join(terms) + ' 变化 政策 供需 新闻', 'version': PROFILE_VERSION})
    return topics


def match_industry(industry, title, content):
    if not valid_industry(industry):
        return {'factors': [], 'reason': ''}
    # A title alone and query metadata are not evidence. Only the main text
    # already cleaned of navigation/related-news blocks may support a match.
    body = '\n'.join(line for line in content.splitlines() if line.strip() != title.strip())[:3000]
    factors = []
    def contains(text, terms):
        return any(term.casefold() in text.casefold() for term in terms)
    if EVENT.search(title) and EVENT.search(body):
        keyword = industry_keyword(industry)
        if keyword and keyword in title and keyword in body:
            factors.append('行业动态')
        for label, terms in factors_for(industry):
            if contains(title, terms) and contains(body, terms):
                factors.append(label)
    factors = list(dict.fromkeys(factors))
    return {'factors': factors, 'reason': f'目标公司所属行业为{industry}；材料涉及' + '、'.join(factors) + '，属于间接行业关联，具体业务影响需核验。' if factors else ''}


def select_analyses(news, maximum):
    """Reserve one third for industry context, then fill within the same budget."""
    if maximum <= 0:
        return []
    companies = [row for row in news if row.get('news_scope', 'company') != 'industry']
    industries = [row for row in news if row.get('news_scope') == 'industry']
    industry_limit = max(1, maximum // 3) if maximum >= 2 else 0
    selected = companies[:maximum - industry_limit] + industries[:industry_limit]
    ids = {row['id'] for row in selected}
    selected.extend(row for row in news if row['id'] not in ids)
    return selected[:maximum]
