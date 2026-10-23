"""Auditable source-only cleaning for the offline direction experiment.

Rules are conservative heuristics, not a semantic ground-truth classifier.
No generated summaries, inferred event dates, or source timestamp backdating.
"""
from datetime import datetime
import hashlib
import re

from .news_cleaning import clean_text, core_title, material_kind
from .news_direction import SHANGHAI

POLICY_VERSION = 'event-news-session-v2'
MARKET = re.compile(
    r'涨停|跌停|跟涨|领涨|领跌|股价|市值|换手率|主力资金|龙虎榜|融资余额|融券余额|'
    r'资金净流|净买入|净卖出|目标价|\d[\d.]*元[/／]股|(?:股票|个股|板块|概念|指数|A股|港股)'
    r'[^。；\n]{0,30}(?:上涨|下跌|大涨|大跌|拉升|跳水|涨幅|跌幅)|报收|截至收盘')
MOVE = re.compile(r'大涨|大跌|暴涨|暴跌|拉升|跳水|涨幅|跌幅|成交额|成交量|上涨|下跌|涨超|跌超')
# Time-trap guard: strip the STOCK's own price movement, keep business movement
# (revenue/profit/product-price changes). Stock-only terms are always removed;
# ambiguous verbs (涨/跌/大涨/...) are removed only near a stock marker.
STOCK_MARKER = re.compile(r'股价|该股|个股|股票|市值|换手率|收盘|盘中|午后|今日|昨日|A股|港股|大盘|指数|板块|涨停|跌停|上市首日|上市以来')
STOCK_ONLY = re.compile(r'涨停|跌停|收涨|收跌|换手率|放量|缩量|报收|截至收盘|创新高|创新低|'
                        r'龙虎榜|融资余额|融券余额|主力资金|资金净|封板|封死|破发|破净|涨停板|跌停板')
AMBIGUOUS = re.compile(r'大涨|大跌|暴涨|暴跌|上涨|下跌|涨幅|跌幅|涨超|跌超|拉升|跳水|异动|'
                       r'跟涨|领涨|领跌|成交额|成交量|市盈率|市净率|走高|走低|上扬|下行|上行|'
                       r'下滑|回落|回撤|阴跌|腰斩|翻倍|翻红|翻绿|反弹|蒸发')
MOVEMENT = re.compile(
    r'(?:涨停|跌停|收涨|收跌|跟涨|领涨|领跌|涨超|跌超|暴涨|暴跌|大涨|大跌|'
    r'拉升|跳水|异动|涨幅|跌幅|换手率|成交量|成交额|放量|缩量|资金净|主力资金|'
    r'龙虎榜|融资余额|融券余额|报收|截至收盘|创新高|创新低|市盈率|市净率|'
    r'封板|封死|破发|破净|涨停板|跌停板|走高|走低|上扬|下行|上行|下滑|回落|回撤|'
    r'阴跌|腰斩|翻倍|翻红|翻绿|反弹|蒸发|上涨|下跌)[^，。；;！？!?\n]{0,12}')
BUSINESS_PRICE = re.compile(r'价格|售价|报价|原材料|光伏板|组件|碳酸锂|库存|供需|销量|营收|利润|销售额|出货')
EVENT = re.compile(
    r'签订|签署|中标|订单|供货合同|采购合同|合作协议|战略合作|投产|停产|扩产|减产|'
    r'产能|出货量|交付量|销量|销售额|营业收入|营收|净利润|业绩预告|业绩快报|财报|'
    r'季报|年报|半年报|分红|回购|增持|减持|定增|发行债券|债券发行|并购|收购|重组|'
    r'诉讼|仲裁|立案|处罚|监管函|问询函|调查|召回|事故|火灾|断裂|质量争议|违约|破产|'
    r'关税|出口管制|禁令|补贴|融资政策|贸易.*磋商|获批|审批|专利|研发|发布.*产品|'
    r'(?:原材料|产品|组件|光伏板|碳酸锂|原油|天然气|煤炭|钢材|电池)[^。；\n]{0,20}(?:价格|售价|报价)|'
    r'供需|需求|利率|汇率|行权|股份奖励|新增发行|募集|认购|'
    r'法院|开庭|合同纠纷|股东会|股东大会|刹车|制动|安全隐患|产品.*(?:故障|缺陷)|'
    r'起诉|指控|反垄断|采购|招标|出资|投资设立|产量|批准|国家标准|股权投资|备案|'
    r'IPO|上市|正式运营|投入运营|限电|断供|油价|供应|集装箱.*价格')
RISK = re.compile(r'风险|不确定|尚未|未能|未获|不保证|可能|取决于|条件|取消|终止|否认|澄清|不涉及|不代表|'
                  r'引发|消息面|由于|因为|受.*影响|回应|表示|透露|公告|发布|证实')
NOISE = re.compile(r'炒股就看|金麒麟分析师|下载.*APP|打开.*APP|扫码|点击查看|投资有风险|不构成投资建议|'
                   r'版权所有|ICP备|公网安备|信息服务许可证|Copyright|微信.*QQ|新浪财经APP')
PAYWALL = re.compile(r'单篇付费|解锁全文|订阅后阅读|仅供会员|付费后阅读')
FOOTER = re.compile(r'(?m)^\s*(?:#{1,6}\s*)?(?:相关新闻|相关资讯|相关阅读|最新文章|热门文章|新闻推荐|'
                    r'精选推荐|头条号入驻|财经自媒体联盟.*|加载中|点击加载更多|更多新闻)\s*$')
HEADER_TIME = re.compile(
    r'(?m)^\s*(?:(?:发布时间|发布日期|发表时间|更新时间|时间|来源|作者)[^\n。！？]{0,70}?)?'
    r'(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})日?[ T\t]*'
    r'(\d{1,2}):(\d{2})(?::(\d{2}))?')


def header_timestamp(text, publication_date):
    """Recover only same-day header times; unknown seconds round to minute end."""
    values = []
    for match in HEADER_TIME.finditer(text[:1800]):
        y, m, d, hour, minute = map(int, match.groups()[:5])
        try:
            value = datetime(y, m, d, hour, minute, int(match[6]) if match[6] else 59, tzinfo=SHANGHAI)
        except ValueError:
            continue
        if value.date().isoformat() == publication_date:
            values.append(value)
    return max(values).isoformat() if values else publication_date


def strip_movement(sentence):
    """Drop the stock's own price-movement phrases, keeping event and business
    figures (e.g. '营收大涨', '产品价格上涨')."""
    parts = re.split(r'(?<=[，,、；;。！？!?])', sentence)
    out = []
    for part in parts:
        if STOCK_ONLY.search(part) or (AMBIGUOUS.search(part) and STOCK_MARKER.search(part)):
            part = MOVEMENT.sub('', part)
        out.append(part)
    return ''.join(out)


def clean_article(article):
    """Return a versioned model view and its reviewable cleaning decision."""
    text, stats = clean_text(article['text'], article.get('title', ''))
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    title = core_title(article.get('title', ''))
    published = header_timestamp(text, article['publication_date'])
    heading = re.split(r'[_|｜]|\s-\s', title)[0].strip()
    positions = [i for i, char in enumerate(text[:6000]) if not char.isspace()]
    compact = ''.join(text[i] for i in positions)
    needle = re.sub(r'\s+', '', heading)
    position = compact.find(needle) if len(needle) >= 8 else -1
    if position > 0:
        text = text[positions[position]:]
    footer = FOOTER.search(text)
    if footer:
        text = text[:footer.start()]
    sentences = [s.strip() for s in re.split(r'(?<=[。！？!?；;])|\n+', text) if s.strip()]
    # A headline alone is not evidence for an event. Remove repeated headlines.
    body = [s for s in sentences if s != title and not NOISE.search(s) and re.search(r'[\w\u4e00-\u9fff]{2}', s)]
    def is_market(sentence):
        direct = any(key and key in sentence for key in (article.get('company'), article.get('code')))
        return bool(MARKET.search(sentence) or (direct and MOVE.search(sentence) and not BUSINESS_PRICE.search(sentence)))
    evidence = [s for s in body if EVENT.search(s)]
    market = is_market(title) or any(is_market(s) for s in body)
    category = ('mixed' if market else 'event') if evidence else ('market_only' if market else 'review')
    removed = [s for s in body if category == 'mixed' and is_market(s) and not EVENT.search(s) and not RISK.search(s)]
    kept = [s for s in body if s not in removed]
    kept = [strip_movement(s) for s in kept]
    kept = [s for s in kept if s.strip()]
    view = '\n'.join(kept)
    non_article = bool(material_kind(article) or PAYWALL.search(text) or re.search(r'(?:ETF|基金).*申赎清单', title, re.I))
    excluded = category in ('market_only', 'review') or len(view) < 80 or non_article
    model_title = title
    if category == 'mixed' and is_market(title):
        model_title = evidence[0][:100]
    model_title = strip_movement(model_title)
    identity = hashlib.sha256((POLICY_VERSION + '\0' + article['id'] + '\0' + model_title + '\0' + view).encode()).hexdigest()
    decision = {'source_id': article['id'], 'id': identity, 'code': article['code'], 'url': article.get('url'),
                'title': title, 'category': category, 'excluded': excluded,
                'reason': ('non_article_or_paywall' if non_article else 'pure_market' if category == 'market_only'
                           else 'needs_review' if category == 'review' else 'short_clean_body' if len(view) < 80 else 'retained'),
                'event_evidence': evidence[:5], 'removed_market_sentences': removed,
                'publication_timestamp': published, 'timestamp_source': 'body_header' if len(published) > 10 else 'date_only_end_of_day',
                'cleaning_stats': stats}
    result = {**article, 'id': identity, 'source_id': article['id'], 'title': model_title, 'text': view,
              'publication_timestamp': published, 'direction_excluded': excluded,
              'direction_category': category, 'publication_timestamp_source': decision['timestamp_source']}
    return result, decision
