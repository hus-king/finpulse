"""Explicit historical imports and group-disjoint development datasets."""
from collections import Counter
from datetime import date
import re

from .contract import QUESTIONS, input_digest, model_state
from .pipeline import quality_reasons
from .teacher import reference_consensus

ROLES = ("train", "validation", "calibration")


def import_records(rows, group_roles, profiles=None):
    profiles = {p["code"]: p for p in (profiles or [])}
    if any(role not in ROLES for role in group_roles.values()): raise ValueError("Unknown dataset role")
    seen, result = set(), []
    for row in rows:
        if row["id"] in seen: raise ValueError("Duplicate input id")
        seen.add(row["id"])
        if row["group_id"] not in group_roles: raise ValueError("Undeclared event group")
        source = row.get("source", {})
        try:
            date.fromisoformat(source.get("published_date", "")); date_status = "ready"
        except (ValueError, TypeError): date_status = "pending"
        state = model_state(row)
        text, table = state["news"]["text"], state["news"]["market_rows"]
        valid_rows = isinstance(table, list) and all(isinstance(r, list) for r in table)
        units = "单位" in text or (valid_rows and any("单位" in str(c) for r in table for c in r))
        data_rows = [r for r in table if len(r) >= 2] if valid_rows else []
        table_ready = (bool(state["news"]["title"].strip()) and units and len(data_rows) >= 2
                       and any(re.search(r"\d", str(c)) for r in data_rows[1:] for c in r))
        body_ready = len(text.strip()) >= 80 or table_ready
        record = {"id": row["id"], "group_id": row["group_id"], "target_code": row["target_code"], "state": state,
                  "questions": QUESTIONS, "split": group_roles[row["group_id"]], "source": source,
                  "quality": {"body": "ready" if body_ready else "pending", "date": date_status,
                              "profile": "ready" if state["company"]["products"] else "pending"},
                  "development_only": True, "human_verified": False,
                  "profile_provenance": profiles.get(row["target_code"], {}).get("selected_fact_evidence", []),
                  "material_provenance": "previously_seen_historical_AI_reviewed_excerpt_not_new_full_body"}
        record["input_sha256"] = input_digest(record)
        result.append(record)
    return result


def assemble(records, primary, audit, model_identity=None):
    datasets = {role: [] for role in ROLES}
    groups, queue = {}, []
    for record in records:
        role = record["split"]
        if role not in ROLES: raise ValueError("Unknown role")
        group = record["group_id"]
        if group in groups and groups[group] != role: raise ValueError("Event group crosses roles")
        groups[group] = role
        consensus = reference_consensus(record, primary.get(record["id"]), audit.get(record["id"]), model_identity)
        queue.append({**consensus, "split": role, "quality_reasons": quality_reasons(record)})
        if consensus["status"] == "weak_consensus":
            datasets[role].append({**record, "expected": consensus["expected"], "reference": consensus})
    support = {role: {qid: dict(Counter(r["expected"][qid] for r in rows)) for qid in QUESTIONS}
               for role, rows in datasets.items()}
    missing = [f"{role}/{qid}/{label}" for role in ROLES for qid, q in QUESTIONS.items()
               for label in q["criteria"] if not support[role][qid].get(label)]
    return {"datasets": datasets, "review_queue": queue,
            "training_gate": {"passed": not missing, "missing_support": missing, "support": support},
            "development_only": True, "independent_test": False, "human_verified": False}
