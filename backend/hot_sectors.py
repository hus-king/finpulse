"""Bounded sector hypotheses grounded in a hot topic, independent of watchlists."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

SECTORS = {
    'oil_gas': '油气开采', 'refining': '炼化化工', 'shipping': '航运港口', 'aviation': '航空运输',
    'autos': '汽车整车', 'auto_parts': '汽车零部件', 'semiconductors': '半导体', 'computing': '算力通信',
    'batteries': '储能电池', 'renewables': '新能源发电', 'grid_equipment': '电网设备', 'banks': '银行',
    'real_estate': '房地产', 'building': '建筑建材', 'machinery': '工程机械', 'retail': '消费零售',
    'food': '食品饮料', 'medicine': '医药医疗', 'agriculture': '农业种植', 'metals': '有色金属',
    'gold': '贵金属开采', 'tourism': '旅游酒店', 'media': '传媒互联网', 'export_manufacturing': '出口制造',
}


class SectorImpact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    direction: Literal['positive', 'negative', 'mixed', 'unclear']
    reason: str = Field(min_length=1, max_length=120)
    evidence: str = Field(min_length=2, max_length=120)

    @model_validator(mode='after')
    def valid_sector(self):
        if self.id not in SECTORS or not self.reason.strip() or not self.evidence.strip():
            raise ValueError('Unknown sector or missing evidence')
        return self


def validate_sectors(rows, material):
    if len({row['id'] for row in rows}) != len(rows): raise ValueError('Duplicate sector')
    if any(row['evidence'] not in material for row in rows): raise ValueError('Sector evidence not in topic')


def rule_sectors(topic):
    """Conditional economic paths, never claims of a company's actual exposure."""
    text = topic['title'] + '\n' + topic.get('summary', '')
    result = []
    uncertain = bool(re.search(r'否认|辟谣|传闻|网传|是否|会不会|不实|假如|[？?]', text))

    def add(sector, direction, reason, match):
        if any(row['id'] == sector for row in result): return
        start = max(0, match.start() - 15)
        result.append({'id': sector, 'direction': 'unclear' if uncertain else direction,
                       'reason': '材料含未核实、否认或提问信息，板块关联与方向需要进一步核对。' if uncertain else reason,
                       'evidence': text[start:start + 110].strip()})

    oil = re.search(r'原油|油价|石油价格|天然气价格', text)
    strait = re.search(r'霍尔木兹|苏伊士|红海|航道|海峡', text)
    price_link = r'(?:持续|大幅|小幅|再度|出现|已经|明显|开始|继续|加速|短线|一度|正在|普遍|有所|的|[：:，,\s]){0,8}'
    energy_price = r'(?:原油(?:价格)?|油价|石油价格|天然气价格)'
    up = bool(re.search(energy_price + price_link + r'(?:上涨|上行|走高|涨价|攀升)', text))
    down = bool(re.search(energy_price + price_link + r'(?:下跌|下行|回落|降价)', text))
    blocked = bool(strait and re.search(r'封锁|停航|受阻|关闭|中断|袭击|禁航', text))
    if blocked:
        add('shipping', 'mixed', '通航受阻可能抬升部分航线运费，也增加绕航和保险成本，需核对航线与合同。', strait)
    if oil or blocked and '霍尔木兹' in text:
        match = oil or strait
        add('oil_gas', 'positive' if up or blocked else 'negative' if down else 'unclear',
            '供给预期收紧或价格上行时，资源生产端收入可能受益；需核对实际价格、产量与成本。' if up or blocked else
            '能源价格下行可能压低资源生产端收入，需核对产量与成本。' if down else '需要核实能源供给与价格是否发生实际变化。', match)
        add('aviation', 'negative' if up or blocked else 'positive' if down else 'unclear',
            '燃油价格或相关运输成本可能上升，影响航空运营成本；取决于航线、套保与成本转嫁。' if up or blocked else
            '燃油成本下行可能改善运营成本，仍取决于套保、票价和需求。' if down else '需进一步核实燃油成本及航线敞口。', match)
        if not blocked: add('refining', 'mixed', '原料采购成本与产品售价可能同时变化，净影响取决于加工价差和传导能力。', match)
    trade = re.search(r'关税|出口管制|贸易制裁', text)
    auto = re.search(r'汽车|车企|整车', text)
    if trade and not auto:
        add('export_manufacturing', 'mixed', '贸易政策可能改变受覆盖产品的成本与准入，也可能引发订单转移；净方向需核实品类、市场与豁免范围。', trade)
    if trade and auto:
        easing = bool(re.search(r'取消关税|降低关税|关税豁免|下调关税', text))
        for sector in ('autos', 'auto_parts'):
            add(sector, 'positive' if easing else 'negative',
                '贸易限制减轻可能改善相关出口订单或成本，需核实产品覆盖与出口地区。' if easing else
                '贸易壁垒可能增加受覆盖出口业务的成本或市场准入压力；不能推定所有车企均有相同敞口。', trade)
    chips = re.search(r'芯片|半导体|集成电路', text)
    if chips:
        limit = bool(re.search(r'管制|制裁|禁令', text))
        add('semiconductors', 'mixed' if limit else 'positive' if re.search(r'突破|量产|需求增长|需求推动|需求驱动', text) else 'unclear',
            '限制可能压制部分供应与销售，也可能带来替代需求，需区分产业环节。' if limit else '技术或需求变化可能影响相关供给与订单，需核实商业规模及竞争条件。', chips)
    rate = re.search(r'降息|降准|加息|利率|房贷贴息', text)
    if rate:
        easing = bool(re.search(r'降息|降准|下调|降低|贴息', text))
        add('banks', 'mixed', '利率与信贷条件变化可能同时影响息差、信用需求及资产质量，不能统一推定受益。', rate)
        add('real_estate', 'positive' if easing else 'unclear', '融资或购房条件改善时，相关需求与资金成本可能受益，需核实政策执行。' if easing else '需核实融资与购房条件变化的方向及执行范围。', rate)
    batteries = re.search(r'电池|储能', text)
    if batteries: add('batteries', 'positive' if re.search(r'增长|突破|订单|扩张|新型', text) else 'unclear', '技术、装机或订单变化可能影响产业需求，仍需核实价格竞争与实际经营敞口。', batteries)
    infrastructure = re.search(r'电网|水网|水利|基建', text)
    if infrastructure:
        add('grid_equipment' if '电网' in text else 'building', 'positive' if re.search(r'建设|投资|开工|扩建', text) else 'unclear', '工程建设或投资可能带动相关设备与材料需求，需核实规模及项目进度。', infrastructure)
    gold = re.search(r'黄金|金价', text)
    if gold: add('gold', 'positive' if re.search(r'(?:黄金(?:价格)?|金价)' + price_link + r'(?:上涨|走高|攀升)', text) else 'negative' if re.search(r'(?:黄金(?:价格)?|金价)' + price_link + r'(?:下跌|回落)', text) else 'unclear', '价格变化可能影响矿产生产端收入，需核实价格方向、产量与开采成本。', gold)
    for pattern, sector in [(r'医药|药品|医疗', 'medicine'), (r'票房|电影|传媒', 'media'), (r'旅游|酒店', 'tourism'),
                            (r'零售|消费', 'retail'), (r'粮食|农业', 'agriculture'), (r'白酒|食品|饮料', 'food')]:
        match = re.search(pattern, text)
        if match: add(sector, 'unclear', '材料可能涉及该板块，但需核实需求、成本或监管的具体变化后判断净方向。', match)
    return result[:3]


def present_sectors(topic):
    screening = topic['screening']
    rows = screening.get('sectors')
    method = screening.get('method', 'rules')
    if rows is None:
        rows = rule_sectors(topic) if screening['decision'] == 'keep' else []
        method = 'rules'
    return [{**row, 'name': SECTORS[row['id']], 'method': method} for row in rows if row['id'] in SECTORS]
