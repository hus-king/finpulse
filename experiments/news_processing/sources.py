"""Source identity and event-group checks for historical development intake."""
import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from .contract import QUESTIONS


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
