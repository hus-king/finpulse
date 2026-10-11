"""Shadow routing and source-visible review export; no production side effects."""
import html
import json
from urllib.parse import urlsplit

from .contract import validate_prediction, news_visible


def quality_reasons(record):
    quality = record.get("quality", {})
    reasons = [key + "_pending" for key in ("body", "date", "profile") if quality.get(key) != "ready"]
    state = record.get("state", {})
    news = state.get("news", {})
    if not news.get("text", "").strip() and not news.get("market_rows"):
        reasons.append("empty_news")
    if not state.get("company", {}).get("products"):
        reasons.append("missing_products")
    return reasons


def route(record, prediction, confidence_threshold=.8):
    if not 0 <= confidence_threshold <= 1: raise ValueError("Invalid routing threshold")
    result = {"id": record["id"], "retained": True, "human_review_required": True,
              "production_enabled": False, "automatic_deletion": False,
              "threshold_is_development_heuristic": True, "reasons": quality_reasons(record)}
    if result["reasons"]:
        result["suggested_route"] = "supplement_material"
        return result
    try:
        checked = validate_prediction(prediction)
    except ValueError:
        result.update(suggested_route="large_model_review", reasons=["missing_or_invalid_prediction"])
        return result
    own, industry = checked["own_event"], checked["industry_relation"]
    if min(own["confidence"], industry["confidence"]) < confidence_threshold or industry["label"] == "POSSIBLE":
        selected = "large_model_review"
    elif own["label"] == "YES": selected = "own_company_analysis"
    elif industry["label"] == "CLEAR": selected = "industry_analysis"
    else: selected = "lower_priority_review"
    result.update(suggested_route=selected, prediction=checked)
    return result


def render_review(records, predictions, references):
    esc = lambda x: html.escape(str(x), quote=True)
    cards = []
    for record in records:
        rid = record["id"]
        decision = route(record, predictions.get(rid))
        url = record.get("source", {}).get("url", "")
        try: safe = urlsplit(url).scheme in ("http", "https") and bool(urlsplit(url).netloc)
        except ValueError: safe = False
        link = f'<a href="{esc(url)}" rel="noreferrer">原始来源</a>' if safe else "来源链接待核验"
        details = {"suggestion": decision, "references": references.get(rid, {}),
                   "source": record.get("source"), "quality": record.get("quality"),
                   "profile_provenance": record.get("profile_provenance"), "group_id": record["group_id"]}
        cards.append(f'<article><h2>{esc(record["state"]["company"]["name"])} · {esc(record["state"]["news"]["title"])}</h2>'
                     f'<p>{esc(rid)} · {esc(decision["suggested_route"])} · {link}</p>'
                     f'<h3>新闻正文及原始表格</h3><pre>{esc(news_visible(record))}</pre>'
                     f'<h3>公司背景</h3><pre>{esc(json.dumps(record["state"]["company"], ensure_ascii=False, indent=2))}</pre>'
                     f'<h3>待复核的 AI 弱参考与模型建议</h3><pre>{esc(json.dumps(details, ensure_ascii=False, indent=2))}</pre></article>')
    return ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>新闻处理人工复核材料</title>'
            '<style>body{background:#111827;color:#e5e7eb;max-width:1100px;margin:32px auto;font:16px/1.6 sans-serif;padding:20px}'
            'article{border:1px solid #475569;border-radius:12px;padding:20px;margin:24px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
            'a{color:#93c5fd}h2{font-size:21px}</style><h1>新闻处理人工复核材料</h1>'
            '<p>所有新闻保留。AI 一致意见不等于真值；当前 human_verified=false。请核验事件归属、业务证据、传导条件、正文表格和日期。'
            '此页面只展示材料，不能提交真人签名或自动改变训练标签。</p>' + ''.join(cards) + '</html>')
