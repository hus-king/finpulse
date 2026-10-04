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

SHANGHAI = timezone(timedelta(hours=8))
STOCK_ENTITIES = {
    "贵州茅台": {"code": "600519", "aliases": ["贵州茅台", "茅台", "600519"]},
    "宁德时代": {"code": "300750", "aliases": ["宁德时代", "寧德時代", "Contemporary Amperex", "CATL", "300750"]},
    "中芯国际": {"code": "688981", "aliases": ["中芯国际", "中芯國際", "SMIC", "688981"]},
    "平安银行": {"code": "000001", "aliases": ["平安银行", "平安銀行", "000001"]},
    "比亚迪": {"code": "002594", "aliases": ["比亚迪", "比亞迪", "BYD", "002594"]},
}
REASONS = {
    "quote_page": "行情、历史价格或评级聚合页面，不是独立新闻",
    "not_primary_entity": "标题的主要对象不是这只股票",
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


def normalize_url(url):
    try:
        parts = urlsplit(url)
    except (ValueError, TypeError):
        return ""
    if parts.scheme not in ("https", "http") or not parts.netloc or parts.username or parts.password:
        return ""
    tracking = {"from", "spm", "gubaurl", "guba", "name", "source", "ref", "oid", "vt", "cid", "node_id", "clickid"}
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_") and k.lower() not in tracking]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def source_page_kind(url):
    """Known quote/financial/profile paths are not individual news articles."""
    parts = urlsplit(url)
    host, path = parts.hostname or '', parts.path.lower()
    if host == 'www.qcc.com' and path.startswith('/firm/'):
        return 'company_profile'
    if host.endswith('.finance.sina.com.cn') and '/vci_corpmanager/' in path:
        return 'company_profile'
    if host == 'data.eastmoney.com' and re.fullmatch(r'/notice/\d{6}\.html', path):
        return 'news_index'
    if (host.endswith('.finance.sina.com.cn') and ('/quotes_service/' in path or '/vfd_' in path)
        or host == 'www.cnyes.com' and path.startswith('/astock/quote/')
        or (host == 'investing.com' or host.endswith('.investing.com')) and path.startswith('/equities/')
        or host == 'stockanalysis.com' and path.startswith('/quote/')
        or (host == 'yahoo.com' or host.endswith('.yahoo.com')) and path.startswith('/quote/')
        or (host == 'futunn.com' or host.endswith('.futunn.com')) and path.startswith('/stock/')
        or (host == 'moomoo.com' or host.endswith('.moomoo.com')) and re.match(r'^/(?:[a-z]{2,8}/)?stock/', path)
        or host == 'data.eastmoney.com' and path.startswith('/zjlx/')
        or host.endswith('.finance.sina.com.cn') and '/vcb_allbulletin/' in path):
        return 'quote_page'
    return None


def core_title(title):
    title = unicodedata.normalize("NFKC", html.unescape(title))
    title = re.sub(r"_新浪财经_新浪网.*$|\s*[—–]\s*Noticias.*$", "", title)
    title = re.sub(r"\s*-\s*(?:财闻网|海报新闻|21经济网|CFi.*|雪球|moomoo).*$", "", title, flags=re.I)
    return title.strip()


def clean_text(raw, title=""):
    """Keep source text order; record removed characters and fragment gaps."""
    raw = raw or ""
    text = unicodedata.normalize("NFKC", html.unescape(raw))
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
    matches = [re.search(r"(?:发布时间|发布日期|时间[：:]|来源[：:])[^\n]{0,80}?"+pattern, head), re.search(r"(?m)^\s*"+pattern, head), re.search(r"(?m)^[^。\n]{0,20}\s+"+pattern+r"\s+\d{1,2}:\d{2}", head[:500])]
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
    matched = left["url"] == right["url"] or (jaccard >= .40 and sequence >= .65) or (distance <= 8 and sequence >= .65)
    return matched, {"title_jaccard": round(jaccard, 3), "title_sequence": round(sequence, 3), "simhash_distance": distance}


def clean_report(raw_report, extracts=None, entities=None):
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
            extracted_date = body_publication_date(text) if full_text is not None else None
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
            # discovered in the extracted page. Conflicts stay quarantined.
            meta, body = metadata_date(raw.get("published_date")), body_publication_date(text) or extracted_date
            effective = body or meta
            kind, label, tier = category(raw.get("title", ""), text)
            item = {"id": f"g{group_index+1}-r{rank}", "stock": stock, "stock_code": entities[stock]["code"], "title": raw.get("title", ""), "url": url, "original_url": raw.get("url"), "search_rank": rank, "search_group": group_index+1, "search_relevance": raw.get("score"), "published_date_metadata": meta.isoformat() if meta else None, "published_date_body": body.isoformat() if body else None, "effective_date": effective.isoformat() if effective else None, "date_status": "conflict" if body and meta and body != meta else "body_verified" if body else "metadata_only" if meta else "missing", "text_source": text_source, "cleaned_text": text, "text_stats": stats, "category": kind, "category_label": label, "tier": tier}
            reasons = []
            source_kind = source_page_kind(url)
            if source_kind:
                reasons.append(source_kind)
            title = raw.get("title", "")
            if not re.search(r'[\u4e00-\u9fff]{2,}', title) and not any(alias.isascii() and not alias.isdigit() and alias.lower() in title.lower() for alias in entities[stock]['aliases']):
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
            if not any(alias.lower() in title.lower() for alias in entities[stock]["aliases"]):
                reasons.append("not_primary_entity")
            if re.search(r"股票股价|股价行情|历史行情|详细报价|詳細報價|即時報價|股票预测|股票預測|RT Quote", title, flags=re.I):
                if 'quote_page' not in reasons:
                    reasons.append("quote_page")
            if re.search(r"规模最大|公司概况|公司简介|公司介紹|企业介绍", title):
                if 'company_profile' not in reasons:
                    reasons.append("company_profile")
            if not effective:
                reasons.append("missing_date")
            if body and meta and body != meta:
                reasons.append("date_conflict")
            if effective and not start <= effective <= end:
                reasons.append("out_of_range")
            entry = {**item, "status": "filtered" if reasons else "retained", "reason_codes": reasons, "reasons": [REASONS[key] for key in reasons]}
            if not reasons:
                for previous in retained:
                    matched, metrics = same_event(item, previous)
                    if matched:
                        previous["sources"].append({"id": item["id"], "title": title, "url": url, "date": item["effective_date"]})
                        entry.update(status="merged", canonical_id=previous["id"], reason_codes=["duplicate_event"], reasons=[REASONS["duplicate_event"]], duplicate_metrics=metrics)
                        break
                else:
                    retained.append({**item, "sources": [{"id": item["id"], "title": title, "url": url, "date": item["effective_date"]}]})
            audit.append(entry)
    counts = Counter(entry["status"] for entry in audit)
    return {"date_range": raw_report["date_range"], "summary": {"input": len(audit), "retained": counts["retained"], "filtered": counts["filtered"], "merged": counts["merged"], "company_events": sum(item["tier"] == "company" for item in retained), "auxiliary_events": sum(item["tier"] == "auxiliary" for item in retained)}, "items": retained, "audit": audit, "limitations": ["规则清洗试验，尚未进行全面人工标注评估", "只有取得正文的条目才能核对正文日期；搜索片段可能截断", "日期冲突保守隔离，不自动改正来源日期", "不同日期和不同编号的事件不因标题相似而合并", "机构观点、行情快讯和衍生品事件单独标记，不能当作公司经营公告", "保留新闻与行情并列展示，不据此推断新闻导致价格变化"]}
