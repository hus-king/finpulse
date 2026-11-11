"""Separate attribution from transmission. No reference labels enter model state."""
import hashlib
import json
import math

VERSION = "split-news-shadow-v1"
QUESTIONS = {
    "own_event": {
        "type": "choice",
        "instructions": "新闻是否描述目标公司自己的实质事件？公司背景不是新闻事件。行业价格、政策、同行事件不等于目标公司已发生事件。只据新闻正文或目标公司表格行判断。",
        "criteria": {"YES": "新闻明确归属于目标公司的经营、产品、合同、财务、公告或证券事件。",
                     "NO": "新闻没有证明目标公司自身发生事件，包括未具名公司的行业新闻。"},
    },
    "industry_relation": {
        "type": "choice",
        "instructions": "行业事件与目标公司的业务是否有明确传导？依据具体产品、原材料、下游用途及业务地区。公司自己的财报不自动构成行业传导。缺少暴露信息不等于无关。",
        "criteria": {"CLEAR": "有具体业务证据的一步行业成本、供需、竞争、技术或监管传导。",
                     "POSSIBLE": "需要补充地区、客户、产品或资格条件，或者只有较远传导或板块情绪，交给复核。",
                     "NONE": "没有实质行业连接，或仅为目标公司的自身事件而无行业传导。"},
    },
}
RELATIONS = {"DIRECT", "CONDITIONAL", "DISTANT", "SENTIMENT", "NONE", "UNKNOWN"}
COARSE = {"DIRECT": "CLEAR", "CONDITIONAL": "POSSIBLE", "DISTANT": "POSSIBLE",
          "SENTIMENT": "POSSIBLE", "NONE": "NONE"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def model_state(record):
    # Project explicitly, so expected labels, reviewer notes and quality statuses cannot leak.
    company = record["state"]["company"]
    news = record["state"]["news"]
    company_state = {k: company.get(k, "" if k in ("name", "industry") else [])
                     for k in ("name", "industry", "products", "upstream", "downstream", "regions")}
    company_state["code"] = record["target_code"]
    return {"company": company_state,
            "news": {k: news.get(k, [] if k == "market_rows" else "")
                     for k in ("title", "text", "market_rows")}}


def input_digest(record):
    return digest({"state": model_state(record), "questions": QUESTIONS})


def news_visible(record):
    news = record["state"]["news"]
    table = "\n".join(" | ".join(str(c) for c in row) for row in news.get("market_rows", []))
    return "\n".join((news.get("title", ""), news.get("text", ""), table))


def company_visible(record):
    return "\n".join(str(x) for k in ("products", "upstream", "downstream", "regions")
                     for x in record["state"]["company"].get(k, []))


def validate_reference(record, ref):
    if not isinstance(ref, dict) or ref.get("own_event") not in {"YES", "NO", "UNKNOWN"}:
        raise ValueError("Invalid own_event")
    if ref.get("industry_relation") not in RELATIONS:
        raise ValueError("Invalid industry_relation")
    for field in ("news_quote", "company_quote", "own_quote", "mechanism"):
        if not isinstance(ref.get(field), str):
            raise ValueError("Missing text field: " + field)
    conditions = ref.get("missing_conditions")
    if not isinstance(conditions, list) or any(not isinstance(x, str) or not x.strip() for x in conditions):
        raise ValueError("Invalid missing_conditions")
    if not ref["mechanism"].strip():
        raise ValueError("Missing mechanism or uncertainty explanation")
    visible = news_visible(record)
    quote = ref["news_quote"]
    uncertain = ref["own_event"] == "UNKNOWN" and ref["industry_relation"] == "UNKNOWN"
    if not uncertain or quote:
        if not 4 <= len(quote) <= 240 or quote not in visible:
            raise ValueError("News quote must be a contiguous visible excerpt")
    own_quote = ref["own_quote"]
    if ref["own_event"] == "YES":
        name = record["state"]["company"]["name"]
        identifiers = [x for x in (name, record["target_code"]) if x]
        if not 6 <= len(own_quote) <= 240 or own_quote not in visible:
            raise ValueError("Own event requires news-side attribution evidence")
        if not any(x in own_quote for x in identifiers):
            raise ValueError("Own evidence lacks target identity")
        remainder = own_quote
        for identity in identifiers: remainder = remainder.replace(identity, "")
        if len(remainder.strip(" ，。：:；;|\n")) < 4:
            raise ValueError("An isolated company name does not prove an event")
    elif own_quote and own_quote not in visible:
        raise ValueError("Own quote is not visible")
    company_quote = ref["company_quote"]
    if ref["industry_relation"] in {"DIRECT", "CONDITIONAL", "DISTANT"}:
        if len(company_quote) < 2 or company_quote not in company_visible(record):
            raise ValueError("Transmission requires a company business quote")
    elif company_quote and company_quote not in company_visible(record):
        raise ValueError("Company quote is not visible")
    if ref["industry_relation"] in {"CONDITIONAL", "DISTANT"} and not conditions:
        raise ValueError("Conditional/distant links must declare missing conditions")
    return {**ref, "human_verified": False, "label_source": "AI_weak_reference"}


def validate_prediction(prediction):
    if not isinstance(prediction, dict): raise ValueError("Prediction must be an object")
    result = {}
    for qid, question in QUESTIONS.items():
        item = prediction.get(qid)
        if not isinstance(item, dict): raise ValueError("Missing question: " + qid)
        probabilities = item.get("probabilities")
        labels = question["criteria"]
        if not isinstance(probabilities, dict) or set(probabilities) != set(labels):
            raise ValueError("Incomplete option probabilities")
        if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1 or not math.isfinite(p)
               for p in probabilities.values()): raise ValueError("Invalid probabilities")
        if abs(sum(probabilities.values()) - 1) > 1e-6: raise ValueError("Probabilities must sum to one")
        label = item.get("label")
        if not isinstance(label, str) or label not in labels or probabilities[label] < max(probabilities.values()) - 1e-9:
            raise ValueError("Selected label disagrees with probabilities")
        result[qid] = {"label": label, "confidence": probabilities[label], "probabilities": probabilities}
    return result
