"""An abstaining issuer baseline, offline and separate from industry relevance.

This narrow baseline supports selected primary issuer announcement families.
It is not a general entity extractor or an automatic news filter.
"""
import re
from urllib.parse import urlsplit

from .contract import digest, input_digest
from .pipeline import quality_reasons

VERSION = "issuer-shadow-v1.1"


def attribute(record, verified_sources=None):
    out = {"id": record["id"], "input_sha256": input_digest(record), "version": VERSION,
           "own_event": "ABSTAIN", "industry_relation": None, "evidence": "",
           "reason": "insufficient_issuer_event_evidence", "retained": True,
           "production_enabled": False, "automatic_deletion": False}
    if quality_reasons(record):
        return {**out, "reason": "material_pending"}
    source = record.get("source", {})
    # Registry is prepared from archived raw files and frozen alongside inputs;
    # caller-provided URL/hash strings alone do not constitute source verification.
    verified = (verified_sources or {}).get(source.get('url', ''))
    if not verified or verified.get('raw_sha256') != source.get('raw_sha256'):
        return {**out, 'reason':'unverified_raw_source'}
    if verified.get('news_sha256') != digest(record['state']['news']):
        return {**out, 'reason':'excerpt_disagrees_with_source_receipt'}
    try: url = urlsplit(source.get("url", ""))
    except ValueError: return out
    if (url.scheme != "https" or url.hostname != "static.cninfo.com.cn"
            or not re.fullmatch(r"/finalpage/\d{4}-\d{2}-\d{2}/\d+\.pdf", url.path, re.I)
            or not re.fullmatch(r"[0-9a-f]{64}", source.get("raw_sha256", ""))):
        return {**out, "reason": "unsupported_primary_source"}
    news = record["state"]["news"]
    text = news.get("text", "")
    head = re.sub(r"\s+", "", text[:1200])
    codes = set(re.findall(r"(?<![债H])(?:A股)?证券代码[:：](\d{6})(?=$|[^\dA-Za-z]|A股证券)", head))
    if len(codes) != 1: return {**out, "reason": "missing_or_ambiguous_issuer"}
    code = next(iter(codes))
    if code != verified.get('issuer_code'):
        return {**out, 'reason':'header_disagrees_with_verified_issuer'}
    event_patterns = (r"20\d{2}年第?[一二三四1-4]季度报告", r"20\d{2}年(?:半年度|年度)报告",
                      r"回购.{0,60}(?:进展|实施|首次|完成|结果)")
    report_event = any(re.search(pattern, head) for pattern in event_patterns)
    title = re.sub(r"\s+", "", news.get("title", ""))
    # Both a declared event family and substantive body evidence are needed.
    # Generic announcement text or a name/code mention alone is insufficient.
    body = re.sub(r"\s+", "", text)
    own_families = ((r"药品.*(?:注册批准|上市许可)", r"(?:公司|子公司).{0,100}(?:收到|获得|批准|受理)"),
                    (r"担保.*(?:进展|公告)", r"(?:公司|子公司).{0,100}(?:提供担保|担保金额|担保余额)"),
                    (r"销售简报", r"公司销售.{0,100}(?:收入|均价|万头|金额)"),
                    (r"高级管理人员变更", r"(?:公司|董事会).{0,100}(?:辞呈|辞任|聘任)"),
                    (r"公司.*共同投资", r"公司.{0,100}(?:签订|认缴出资)"))
    own_event = any(re.search(family, title) and re.search(evidence, body)
                    for family, evidence in own_families)
    if not report_event and not own_event: return out
    name = record["state"]["company"]["name"]
    if code != record["target_code"] and (record["target_code"] in text or name in text):
        return {**out, "reason": "target_role_requires_review"}
    return {**out, "own_event": "YES" if code == record["target_code"] else "NO",
            "issuer_code": code, "reason": "primary_issuer_substantive_event",
            "evidence": text[:1200]}
