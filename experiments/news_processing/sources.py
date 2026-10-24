"""Source identity and event-group checks for historical development intake."""
import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from .contract import QUESTIONS


def financial_table_reasons(rows, text):
    """Fail closed on unjoined physical rows in primary financial excerpts.

    This validates declared four-column or adjusted-comparative schemas. It does
    not guess how an extractor's merged cells or cross-page fragments align.
    Unknown metrics require source review instead of borrowing a blanket unit.
    """
    compact = lambda value: re.sub(r'\s+', '', value)
    if not isinstance(rows,list) or len(rows)<2: return ['missing_financial_rows']
    if any(not isinstance(r,list) or any(not isinstance(c,str) for c in r) for r in rows):
        return ['unresolved_financial_columns_or_cells']
    header = [compact(c) for c in rows[0]]
    width = len(header); start = 1
    if width==5 and len(rows)>=3 and [compact(c) for c in rows[1]]==['','','调整前','调整后','调整后'] and not header[3]:
        start = 2
    elif width!=4: return ['unresolved_financial_columns_or_cells']
    if header[0] not in ('项目','指标') or not header[1].startswith('本报告期') or not header[2].startswith('上年同期') or not header[-1].startswith('本报告期比上年同期'):
        return ['unresolved_financial_header']
    if any(len(r)!=width or any(not c.strip() for c in r) for r in rows[start:]):
        return ['unresolved_financial_columns_or_cells']
    reasons = []
    if not re.search(r'单位\s*[：:].+',text): reasons.append('missing_financial_unit')
    metrics = (r'营业收入|利润总额|归属于(?:上市公司|母公司)股东的(?:扣除非经常性损益的)?净利润|'
               r'经营活动产生的现金流量净额|(?:基本|稀释)每股收益|'
               r'归属于母公司股东的(?:基本|稀释)加权平均每股收益|'
               r'(?:归属于母公司股东的)?加权平均净资产收益率|总资产|'
               r'归属于(?:上市公司|母公司)股东的(?:所有者|股东)权益')
    for row in rows[start:]:
        name = compact(row[0])
        if not re.fullmatch('(?:'+metrics+r')(?:[（(][^）)]*[）)])?',name):
            reasons.append('unresolved_or_incomplete_financial_metric:'+name)
        if '每股收益' in name and not re.search(r'[（(]元(?:/股)?[）)]',name):
            reasons.append('missing_per_share_unit:'+name)
        if any(not re.search(r'\d|^[-—]$',value) for value in row[1:]):
            reasons.append('unresolved_financial_value:'+name)
    return reasons


def announcement_date(entry):
    value = entry.get("announcementTime")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0: return None
    try: return datetime.fromtimestamp(value/1000, timezone.utc).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    except (ValueError, OverflowError, OSError): return None


def select_announcement(slot, entries):
    start, end = slot["params"]["seDate"].split("~")
    eligible = []
    for entry in entries or []:
        if entry.get("secCode") != slot["code"]: continue
        title = re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]*>", "", entry.get("announcementTitle", ""))))
        day = announcement_date(entry)
        if not day or not start <= day <= end: continue
        url = entry.get("adjunctUrl", "")
        if not url.lower().endswith(".pdf"): continue
        if slot["slot_id"].startswith("own_q1_"):
            if not re.search(r"2026年第?一季度报告", title): continue
            if any(x in title for x in ("英文", "English", "更正", "摘要")): continue
        elif "回购" not in title or not any(x in title for x in ("进展", "实施", "首次", "完成", "结果")):
            continue
        eligible.append(entry)
    return min(eligible, key=lambda e: (e["announcementTime"], str(e.get("announcementId", "")))) if eligible else None


def source_identity(url):
    parsed = urlsplit(url)
    # Scheme/fragment variants represent the same source. Query stays intact:
    # official download endpoints often identify the PDF in their query string.
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.query, ""))


def assert_role_disjoint(rows):
    groups, sources, ids = {}, {}, set()
    for row in rows:
        if row["id"] in ids: raise ValueError("Duplicate pair id")
        ids.add(row["id"])
        role = row["split"]
        if role not in ("train", "validation", "calibration"): raise ValueError("Unknown development role")
        for identity, seen, description in ((row["group_id"], groups, "Event group"),
                                            (source_identity(row["source"]["url"]), sources, "Source")):
            if identity in seen and seen[identity] != role: raise ValueError(description + " crosses roles")
            seen[identity] = role


def class_group_gate(datasets, minimum=2):
    counts = {role: {qid: {label: len({r["group_id"] for r in rows if r["expected"][qid] == label})
                          for label in question["criteria"]} for qid, question in QUESTIONS.items()}
              for role, rows in datasets.items()}
    missing = [f"{role}/{qid}/{label}" for role, questions in counts.items()
               for qid, labels in questions.items() for label, count in labels.items() if count < minimum]
    return {"passed": not missing, "minimum_groups": minimum, "group_counts": counts, "insufficient_groups": missing}
