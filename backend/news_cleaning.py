"""Conservative, auditable filtering for search results; no model calls."""
import hashlib
import html
import re
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .industry import match_industry, valid_industry

SHANGHAI = timezone(timedelta(hours=8))
STOCK_ENTITIES = {
    "贵州茅台": {"code": "600519", "aliases": ["贵州茅台", "茅台", "600519"]},
    "宁德时代": {"code": "300750", "aliases": ["宁德时代", "寧德時代", "Contemporary Amperex", "CATL", "300750"]},
    "中芯国际": {"code": "688981", "aliases": ["中芯国际", "中芯國際", "SMIC", "688981"]},
    "平安银行": {"code": "000001", "aliases": ["平安银行", "平安銀行", "000001"]},
    "比亚迪": {"code": "002594", "aliases": ["比亚迪", "比亞迪", "BYD", "002594"]},
    "徐工机械": {"code": "000425", "aliases": ["徐工机械", "徐工", "000425", "XCMG"]},
    "美的集团": {"code": "000333", "aliases": ["美的集团", "美的", "000333"]},
    "格力电器": {"code": "000651", "aliases": ["格力电器", "格力", "000651"]},
    "立讯精密": {"code": "002475", "aliases": ["立讯精密", "立讯", "002475"]},
    "三一重工": {"code": "600031", "aliases": ["三一重工", "三一", "600031"]},
    "中国石油": {"code": "601857", "aliases": ["中国石油", "中石油", "601857", "PetroChina"]},
    "中国石化": {"code": "600028", "aliases": ["中国石化", "中石化", "600028", "Sinopec"]},
    "紫金矿业": {"code": "601899", "aliases": ["紫金矿业", "紫金", "601899"]},
    "赛力斯": {"code": "601127", "aliases": ["赛力斯", "601127", "SERES"]},
    "五粮液": {"code": "000858", "aliases": ["五粮液", "000858"]},
}

GENERIC_ROOT_BLACKLIST = {
    '中国', '中华', '北京', '上海', '广东', '深圳', '浙江', '江苏', '山东', '四川',
    '发展', '科技', '投资', '控股', '实业', '重工', '化工', '能源', '国际', '联合', '创新'
}

CORPORATE_SUFFIXES = (
    '机械', '股份', '科技', '集团', '重工', '控股', '生物', '医药', '电子',
    '证券', '软件', '信息', '材料', '电气', '环境', '精工', '精密', '特钢',
    '重机', '建工', '电器', '光伏', '动力', '汽车', '智能', '网络', '通信',
    '新能', '水务', '银行', '保险', '矿业', '钢铁', '石化', '造纸'
)


def get_stock_aliases(stock_name, entity_info=None):
    """Retrieve official, user-specified, and dynamically derived core brand aliases."""
    base_aliases = list((entity_info or {}).get('aliases', []))
    code = (entity_info or {}).get('code', '')
    known = STOCK_ENTITIES.get(stock_name, {}).get('aliases', [])
    derived = []
    for suffix in CORPORATE_SUFFIXES:
        if stock_name.endswith(suffix) and len(stock_name) - len(suffix) >= 2:
            base = stock_name[:-len(suffix)]
            if base not in GENERIC_ROOT_BLACKLIST:
                derived.append(base)
    if len(stock_name) >= 3 and stock_name[-1].upper() in ('A', 'B'):
        derived.append(stock_name[:-1])
    return list(dict.fromkeys([stock_name, code, *base_aliases, *known, *derived]))

REASONS = {
    "non_news_material": "软件下载、技术教程或异常镜像页面，不是财经新闻",
    "quote_page": "行情、历史价格或评级聚合页面，不是独立新闻",
    "news_index": "新闻或公告索引页面，不是独立文章",
    "not_primary_entity": "标题的主要对象不是这只股票",
    "not_industry_event": "缺少与目标行业对应的标题及正文事件证据",
    "company_profile": "企业介绍，缺少明确的新事件",
    "date_conflict": "正文日期与搜索元数据冲突，暂不进入分析",
    "out_of_range": "正文日期不在本次检索区间",
    "missing_date": "没有可用的发布时间，待核验",
    "duplicate_event": "同一股票、相近日期的同一事件，合并来源",
    "invalid_url": "缺少有效的 HTTP/HTTPS 来源链接",
    "missing_content": "正文或搜索片段不足以进行研判",
    "garbled_title": "标题存在乱码或缺少可读的主体信息",
    "insufficient_event_evidence": "正文或片段未提供标题事件的有效材料，暂不研判",
    "event_after_publication": "已发生交易事件晚于标注发布日期，待核验",
}


TRAD_PAIRS = [
    ('萬', '万'), ('億', '亿'), ('貴', '贵'), ('暫', '暂'), ('買', '买'), ('賣', '卖'),
    ('業', '业'), ('績', '绩'), ('營', '营'), ('銷', '销'), ('額', '额'), ('幣', '币'),
    ('證', '证'), ('券', '券'), ('網', '网'), ('訊', '讯'), ('報', '报'), ('告', '告'),
    ('評', '评'), ('級', '级'), ('標', '标'), ('準', '准'), ('創', '创'), ('產', '产'),
    ('團', '团'), ('聯', '联'), ('華', '华'), ('國', '国'), ('臺', '台'), ('廣', '广'),
    ('總', '总'), ('經', '经'), ('濟', '济'), ('會', '会'), ('機', '机'), ('構', '构'),
    ('發', '发'), ('達', '达'), ('轉', '转'), ('讓', '让'), ('購', '购'), ('換', '换'),
    ('籌', '筹'), ('劃', '划'), ('規', '规'), ('審', '审'), ('處', '处'), ('罰', '罚'),
    ('減', '减'), ('增', '增'), ('質', '质'), ('凍', '冻'), ('結', '结'), ('訴', '诉'),
    ('訟', '讼'), ('違', '违'), ('約', '约'), ('債', '债'), ('務', '务'), ('還', '还'),
    ('償', '偿'), ('預', '预'), ('測', '测'), ('類', '类'), ('價', '价'), ('盤', '盘'),
    ('點', '点'), ('開', '开'), ('關', '关'), ('門', '门'), ('間', '间'), ('時', '时'),
    ('綫', '线'), ('線', '线'), ('勢', '势'), ('倉', '仓'), ('庫', '库'), ('監', '监'),
    ('離', '离'), ('職', '职'), ('辭', '辞'), ('執', '执'), ('險', '险'), ('複', '复'),
    ('復', '复'), ('單', '单'), ('雙', '双'), ('號', '号'), ('頭', '头'), ('體', '体'),
    ('統', '统'), ('實', '实'), ('際', '际'), ('導', '导'), ('師', '师'), ('員', '员'),
    ('製', '制'), ('造', '造'), ('設', '设'), ('備', '备'), ('軟', '软'), ('件', '件'),
    ('應', '应'), ('態', '态'), ('獨', '独'), ('補', '补'), ('貼', '贴'), ('稅', '税'),
    ('費', '费'), ('虧', '亏'), ('損', '损'), ('潤', '润'), ('淨', '净'), ('擴', '扩'),
    ('張', '张'), ('縮', '缩'), ('穩', '稳'), ('憂', '忧'), ('慮', '虑'), ('衝', '冲'),
    ('擊', '击'), ('響', '响'), ('壓', '压'), ('詳', '详'), ('細', '细'), ('節', '节'),
    ('錄', '录'), ('顯', '显'), ('見', '见'), ('聞', '闻'), ('視', '视'), ('頻', '频'),
    ('圖', '图'), ('數', '数'), ('據', '据'), ('調', '调'), ('優', '优'), ('維', '维'),
    ('護', '护'), ('質', '质'), ('於', '于'), ('對', '对'), ('進', '进'), ('東', '东'),
    ('車', '车'), ('電', '电'), ('鐵', '铁'), ('鋼', '钢'), ('鋁', '铝'), ('銅', '铜'),
    ('鋰', '锂'), ('礦', '矿'), ('藍', '蓝'), ('籌', '筹'), ('庫', '库'), ('醫', '医'),
    ('藥', '药'), ('生', '生'), ('物', '物'), ('科', '科'), ('技', '技'), ('軍', '军'),
    ('農', '农'), ('糧', '粮'), ('食', '食'), ('酒', '酒'), ('類', '类'), ('飲', '饮'),
    ('料', '料'), ('為', '为'), ('這', '这'), ('個', '个'), ('與', '与'), ('無', '无'),
    ('從', '从'), ('體', '体'), ('現', '现'), ('將', '将'), ('獲', '获'), ('得', '得')
]
TRANS_TABLE = {ord(a): ord(b) for a, b in TRAD_PAIRS if a != b}

TITLE_SUFFIX_PATTERN = re.compile(
    r'[\s_\-|—–]+(?:东方财富.*|新浪.*|腾讯.*|网易.*|搜狐.*|凤凰.*|同花顺.*|金融界.*|证券时报.*|证券日报.*|中国证券报.*|上海证券报.*|中证网.*|上证报.*|第一财经.*|财联社.*|界面.*|雪球.*|moomoo.*|富途.*|格隆汇.*|21财经.*|21经济.*|海报新闻.*|中新经纬.*|澎湃.*|财闻网.*|CFi.*|Noticias.*|时间线.*|财富号.*|手机网.*|客户端.*|财经网.*|财经.*|信息_相关_显示.*)$',
    re.IGNORECASE
)


def normalize_url(url):
    try:
        parts = urlsplit(url)
    except (ValueError, TypeError):
        return ""
    if parts.scheme not in ("https", "http") or not parts.netloc or parts.username or parts.password:
        return ""
    tracking = {"from", "spm", "gubaurl", "guba", "name", "source", "ref", "oid", "vt", "cid", "node_id", "clickid"}
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_") and k.lower() not in tracking]
    netloc = parts.netloc.lower()
    path = parts.path

    # Canonicalize mobile / WAP subdomains for common financial portals
    if netloc in ("wap.eastmoney.com", "stock.eastmoney.com", "mguba.eastmoney.com") and path.startswith("/a/"):
        netloc = "finance.eastmoney.com"
    elif netloc in ("finance.sina.cn", "m.sina.cn") and "/finance/" in path:
        netloc = "finance.sina.com.cn"
    elif netloc == "m.cls.cn":
        netloc = "www.cls.cn"

    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme.lower()
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def source_page_kind(url):
    """Known quote/financial/profile paths are not individual news articles."""
    parts = urlsplit(url)
    host, path = parts.hostname or '', parts.path.lower()
    if (host == 'qcc.com' or host.endswith('.qcc.com')) and path.startswith('/firm/'):
        return 'company_profile'
    if host == 'www.fscinda.com' and path.startswith('/product/'):
        return 'company_profile'
    if host.endswith('.finance.sina.com.cn') and '/vcb_allnewsstock/' in path:
        return 'news_index'
    if (host == 'msn.com' or host.endswith('.msn.com')) and '/money/watchlist' in path:
        return 'quote_page'
    if host.endswith('.finance.sina.com.cn') and '/vci_corpmanager/' in path:
        return 'company_profile'
    if host == 'data.eastmoney.com' and re.fullmatch(r'/notice/\d{6}\.html', path):
        return 'news_index'
    if (host.endswith('.finance.sina.com.cn') and ('/quotes_service/' in path or '/vfd_' in path)
        or host == 'www.cnyes.com' and path.startswith('/astock/quote/')
        or (host == 'investing.com' or host.endswith('.investing.com')) and path.startswith(('/equities/', '/commodities/', '/indices/'))
        or host == 'stockanalysis.com' and path.startswith('/quote/')
        or (host == 'yahoo.com' or host.endswith('.yahoo.com')) and path.startswith('/quote/')
        or host == 'futunn.com' or host.endswith('.futunn.com') and path.startswith('/stock/')
        or (host == 'moomoo.com' or host.endswith('.moomoo.com')) and re.match(r'^/(?:[a-z]{2,8}/)?stock/', path)
        or host == 'data.eastmoney.com' and path.startswith('/zjlx/')
        or host.endswith('.finance.sina.com.cn') and '/vcb_allbulletin/' in path):
        return 'quote_page'
    return None


def material_kind(row):
    kind=source_page_kind(row.get('url') or '')
    if kind:
        return kind
    title=core_title(row.get('title',''))
    host=urlsplit(row.get('url') or '').hostname or ''
    if (host.startswith('www.www.')
        or re.search(r'安卓网|(?:软件|游戏)下载(?:站|网)|股票代码(?:验证|校验)|深度研究方法论',title)):
        return 'non_news_material'
    return None


def core_title(title):
    title = unicodedata.normalize("NFKC", html.unescape(title or ""))
    title = title.translate(TRANS_TABLE)
    title = TITLE_SUFFIX_PATTERN.sub("", title)
    title = re.sub(r"^[【\[(（][^】\])）]+[】\])）]\s*", "", title)
    return title.strip()


def clean_text(raw, title=""):
    """Keep source text order; record removed characters and fragment gaps."""
    raw = raw or ""
    text = unicodedata.normalize("NFKC", html.unescape(raw))
    text = text.translate(TRANS_TABLE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text).replace("\r", "")
    short_title = core_title(title)
    position = text.find(short_title) if len(short_title) >= 8 else -1
    if 0 < position < 2500:
        text = text[position:]
    footer = re.search(r"(?m)^\s*(?:#{1,6}\s*)?(?:责任编辑[：:]|用户评论|网友评论|实时资讯|最新资讯|相关报道|相关推荐|相关阅读|推荐阅读|热门推荐|推荐新闻|推荐资讯|免责声明|时报热榜|热点视频|新浪简介\||Copyright|关于我们\||追加内容|举报|下载[\"“]?证券时报|\s*-\s*\d{2}/)", text)
    if footer:
        text = text[:footer.start()]
    text = text.replace("[...]", "[搜索片段存在截断]")
    noise = {"新浪首页", "新浪财经APP", "缩小字体", "放大字体", "收藏", "微博", "分享", "字号", "超大", "大", "标准", "小", "点赞", "微信 腾讯QQ QQ空间", "返回顶部"}
    kept, seen = [], set()
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        line = re.sub(r"^#{1,6}\s*", "", line)
        if not line or line in noise or line.startswith("新浪财经APP "):
            continue
        if len(line) >= 12 and line in seen:
            continue
        seen.add(line)
        kept.append(line)
    result = "\n".join(kept).strip()
    return result, {"before_chars": len(raw), "after_chars": len(result), "removed_chars": max(0, len(raw)-len(result)), "has_fragment_gaps": "[搜索片段存在截断]" in result}


def metadata_date(raw):
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (ValueError, TypeError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI).date()


def body_publication_date(text):
    # Only publication headers count. Years mentioned in an article are not publication dates.
    pattern = r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})日?"
    head = text[:1800]
    matches = [
        re.search(r"(?:发布时间|发布日期|发表时间|更新时间|时间[：:]|来源[：:])[^\n]{0,80}?" + pattern, head),
        re.search(r"(?m)^\s*" + pattern, head),
        re.search(r"(?m)^[^。\n]{0,30}\s+" + pattern + r"(?:\s+\d{1,2}:\d{2})?", head[:600]),
        re.search(r"(?m)^" + pattern + r"\s+\d{1,2}:\d{2}", head[:600]),
    ]
    for match in matches:
        if match:
            try:
                return date(*map(int, match.groups()))
            except ValueError:
                pass
    return None


def category(title, text):
    if re.search(r"牛证|熊证|权证|强制赎回", title):
        return "derivatives", "衍生品事件", "auxiliary"
    if re.search(r"目标价|评级|高盛", title):
        return "analyst_opinion", "机构观点", "auxiliary"
    if re.search(r"(?:跌|涨)\s*\d|盘中|成交额|资金净|主力资金|北水|南下资金|净买入|净卖出", title):
        return "market_brief", "行情快讯", "auxiliary"
    if re.search(r"公告|股东会|债券发行|财务资助|持股计划|回购", title) or (re.search(r"Contemporary Amperex", title, flags=re.I) and "公告" in text[:1000]):
        return "announcement", "公告材料", "company"
    return "company_news", "公司相关新闻", "company"


def title_tokens(title):
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", core_title(title).lower())
    return text, {text[i:i+2] for i in range(max(0, len(text)-1))}


def simhash(text):
    tokens = Counter(text[i:i+3] for i in range(max(0, len(text)-2)))
    weights = [0]*64
    for token, count in tokens.items():
        hashed = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
        for bit in range(64):
            weights[bit] += count if hashed & (1 << bit) else -count
    return sum(1 << bit for bit, weight in enumerate(weights) if weight > 0)


def same_event(left, right):
    if left["stock"] != right["stock"] or left["category"] != right["category"]:
        return False, {}
    ld, rd = left["effective_date"], right["effective_date"]
    if not ld or not rd or abs((date.fromisoformat(ld)-date.fromisoformat(rd)).days) > 1:
        return False, {}
    lt, ls = title_tokens(left["title"])
    rt, rs = title_tokens(right["title"])
    # Different numbered plans/amounts must not be merged based on similar wording.
    ln, rn = re.findall(r"\d+(?:\.\d+)?", lt), re.findall(r"\d+(?:\.\d+)?", rt)
    if ln and rn and ln != rn:
        return False, {}
    lp, rp = re.findall(r"第([一二三四五六七八九十\d]+)期", lt), re.findall(r"第([一二三四五六七八九十\d]+)期", rt)
    if lp and rp and lp != rp:
        return False, {}
    jaccard = len(ls & rs)/max(1, len(ls | rs))
    sequence = SequenceMatcher(None, lt, rt).ratio()
    distance = (simhash(lt) ^ simhash(rt)).bit_count()
    is_sub = (lt in rt or rt in lt) and len(min(lt, rt, key=len)) >= 8
    matched = left["url"] == right["url"] or (jaccard >= .40 and sequence >= .65) or (distance <= 8 and sequence >= .65) or is_sub
    return matched, {"title_jaccard": round(jaccard, 3), "title_sequence": round(sequence, 3), "simhash_distance": distance}


def clean_report(raw_report, extracts=None, entities=None, industry_profile=None):
    entities = STOCK_ENTITIES if entities is None else entities
    extracts = dict(extracts or {})
    for result in raw_report.get("extract", {}).get("response", {}).get("results", []):
        extracts.setdefault(normalize_url(result["url"]), result.get("raw_content", ""))
    start, end = map(date.fromisoformat, raw_report["date_range"])
    sources = raw_report["searches"] + ([raw_report["optimized_search"]] if raw_report.get("optimized_search") else [])
    retained, audit = [], []
    for group_index, search in enumerate(sources):
        stock = search["stock"]
        for rank, raw in enumerate(search["response"].get("results", []), 1):
            url = normalize_url(raw.get("url", ""))
            full_text = extracts.get(url)
            text, stats = clean_text(full_text if full_text is not None else raw.get("content", ""), raw.get("title", ""))
            extracted_date = body_publication_date(full_text) if full_text is not None else None
            text_source = "extracted_body" if full_text is not None else "search_fragments"
            # Dynamic news pages can expose a headline followed only by sidebar
            # stories. Do not present that extraction as the article's body.
            title_actions = re.findall(r'暂停|停业|回购|持股计划|增持|减持|净利润|营收|业绩|债券|解禁|处罚|收购', raw.get('title', ''))
            remainder = '\n'.join(line for line in text.splitlines() if core_title(raw.get('title', '')) not in line)
            if full_text is not None and title_actions and not any(action in remainder for action in title_actions):
                text, stats = clean_text(raw.get('content', ''), raw.get('title', ''))
                stats['extraction_mismatch'] = True
                text_source = 'search_fragments'
            # A fallback fragment must not hide an older publication header
            # discovered in the extracted page.
            meta = metadata_date(raw.get("published_date"))
            body_header = body_publication_date(text) or extracted_date
            body = body_header or extracted_date

            # 正文头部日期置信度高于搜索引擎爬取日期，直接采用正文头部日期
            if body_header:
                effective = body_header
                date_status = "body_verified"
            elif body:
                effective = body
                date_status = "body_verified" if not meta or body == meta else "conflict"
            elif meta:
                effective = meta
                date_status = "metadata_only"
            else:
                effective = None
                date_status = "missing"

            kind, label, tier = category(raw.get("title", ""), text)
            aliases = get_stock_aliases(stock, entities.get(stock))
            # 宽松实体消歧：
            # 1. 标题直接包含标的名称、代码、品牌根词（如“徐工”）或别名
            # 2. 或正文开头（前 350 字）明确提及标的名称或股票代码
            direct_in_title = any(alias and alias.lower() in raw.get('title', '').lower() for alias in aliases)
            head_content = (text or '')[:350].lower()
            direct_in_body = (stock.lower() in head_content or (entities[stock]['code'] and entities[stock]['code'] in head_content))
            direct = direct_in_title or direct_in_body

            scope = 'industry' if search.get('news_scope') == 'industry' and not direct else 'company'
            industry = (industry_profile or {}).get('industry')
            relation = match_industry(industry, raw.get('title', ''), text) if scope == 'industry' else {'factors': [], 'reason': '标题直接涉及目标公司。'}
            if scope == 'industry':
                kind, label, tier = 'industry_news', '行业新闻', 'industry'
            item = {"id": f"g{group_index+1}-r{rank}", "stock": stock, "stock_code": entities[stock]["code"], "title": raw.get("title", ""), "url": url, "original_url": raw.get("url"), "search_rank": rank, "search_group": group_index+1, "search_relevance": raw.get("score"), "published_date_metadata": meta.isoformat() if meta else None, "published_date_body": body.isoformat() if body else None, "effective_date": effective.isoformat() if effective else None, "date_status": date_status, "text_source": text_source, "cleaned_text": text, "text_stats": stats, "category": kind, "category_label": label, "tier": tier}
            item.update(news_scope=scope, industry=industry if valid_industry(industry) else None,
                        related_factors=relation['factors'], relevance_reason=relation['reason'], stale=search.get('cache_status') == 'stale')
            reasons = []
            source_kind = material_kind({"url":url,"title":raw.get("title","")})
            if source_kind:
                reasons.append(source_kind)
            title = raw.get("title", "")
            if not re.search(r'[\u4e00-\u9fff]{2,}', title) and not any(alias.isascii() and not alias.isdigit() and alias.lower() in title.lower() for alias in aliases):
                reasons.append('garbled_title')
            if not url:
                reasons.append("invalid_url")
            if len(text.strip()) < 30:
                reasons.append("missing_content")
            remainder = '\n'.join(line for line in text.splitlines() if core_title(raw.get('title', '')) not in line)
            if title_actions and not any(action in remainder for action in title_actions):
                reasons.append('insufficient_event_evidence')
            occurred = re.search(r'(?m)^(\d{1,2})月(\d{1,2})日[^。\n]{0,100}(?:收盘|买入|发帖|直线拉升)', text[:1000])
            if occurred and effective and not re.search(r'将|拟|计划', occurred.group()):
                try:
                    event_day = date(effective.year, int(occurred[1]), int(occurred[2]))
                    if 0 < (event_day - effective).days <= 31:
                        reasons.append('event_after_publication')
                except ValueError:
                    pass
            if scope == 'industry' and not relation['factors']:
                reasons.append('not_industry_event')
            elif scope == 'company' and not direct:
                reasons.append("not_primary_entity")
            if re.search(r"股票股价|股价行情|历史行情|详细报价|詳細報價|即時報价|股票预测|股票預測|RT Quote", title, flags=re.I):
                if 'quote_page' not in reasons:
                    reasons.append("quote_page")
            if re.search(r"规模最大|公司概况|公司简介|公司介紹|企业介绍", title):
                if 'company_profile' not in reasons:
                    reasons.append("company_profile")
            if not effective:
                reasons.append("missing_date")
            if date_status == "conflict":
                reasons.append("date_conflict")
            if effective and not start <= effective <= end:
                reasons.append("out_of_range")
            entry = {**item, "status": "filtered" if reasons else "retained", "reason_codes": reasons, "reasons": [REASONS[key] for key in reasons]}
            if not reasons:
                for previous in retained:
                    matched, metrics = same_event(item, previous)
                    # A URL repeated in company and industry searches is one
                    # document, irrespective of the query's assigned category.
                    if item['url'] == previous['url'] and item['stock'] == previous['stock']:
                        matched = True
                    if matched:
                        previous["sources"].append({"id": item["id"], "title": title, "url": url, "date": item["effective_date"]})
                        # 优质正文继承：若新条目已提取完整正文，而原保留项仅为搜索片段，则升级为完整正文与校验日期
                        if item["text_source"] == "extracted_body" and previous.get("text_source") != "extracted_body":
                            previous["cleaned_text"] = item["cleaned_text"]
                            previous["text_source"] = "extracted_body"
                            previous["text_stats"] = item["text_stats"]
                            if item.get("effective_date") and item.get("date_status") == "body_verified":
                                previous["effective_date"] = item["effective_date"]
                                previous["date_status"] = "body_verified"
                        entry.update(status="merged", canonical_id=previous["id"], reason_codes=["duplicate_event"], reasons=[REASONS["duplicate_event"]], duplicate_metrics=metrics)
                        break
                else:
                    retained.append({**item, "sources": [{"id": item["id"], "title": title, "url": url, "date": item["effective_date"]}]})
            audit.append(entry)
    counts = Counter(entry["status"] for entry in audit)
    return {"date_range": raw_report["date_range"], "summary": {"input": len(audit), "retained": counts["retained"], "filtered": counts["filtered"], "merged": counts["merged"], "company_events": sum(item["tier"] == "company" for item in retained), "industry_events": sum(item['news_scope'] == 'industry' for item in retained), "auxiliary_events": sum(item["tier"] == "auxiliary" for item in retained)}, "items": retained, "audit": audit, "limitations": ["规则清洗试验，尚未进行全面人工标注评估", "只有取得正文的条目才能核对正文日期；搜索片段可能截断", "正文头部发布日期置信度优先于搜索元数据，已校验正文日期的条目直接采用正文日期", "不同日期和不同编号的事件不因标题相似而合并", "机构观点、行情快讯和衍生品事件单独标记，不能当作公司经营公告", "保留新闻与行情并列展示，不据此推断新闻导致价格变化", "行业事件只表示间接关联，需结合目标公司的主营业务核验影响"]}
