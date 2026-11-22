"""Optional GPU shadow replay. No training, weak labels or production mutation."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from .contract import QUESTIONS, input_digest, model_state, validate_prediction
from .pipeline import quality_reasons
from .storage import read_jsonl, write_json, write_jsonl


def inference_rows(records):
    # Targets only satisfy Laya's item constructor. They are not serialized into
    # state/head; calibration_records computes logits, never optimization loss.
    return [{"state": model_state(r), "questions": r.get("questions", QUESTIONS),
             "expected": {"own_event": "NO", "industry_relation": "NONE"}} for r in records]


def format_predictions(records, metadata, logits, checkpoint_sha):
    if len(metadata) != len(logits) or len(metadata) != 2*len(records):
        raise ValueError("Incomplete question logits")
    result = {r["id"]: {"id": r["id"], "input_sha256": input_digest(r), "checkpoint_sha256": checkpoint_sha,
                        "prediction": {}} for r in records}
    for (record, qid, labels), values in zip(metadata, logits):
        if len(labels) != len(values) or not all(math.isfinite(float(x)) for x in values):
            raise ValueError("Invalid option logits")
        top = max(values); exp = [math.exp(float(v)-top) for v in values]; total = sum(exp)
        probabilities = dict(zip(labels, [v/total for v in exp]))
        result[record["id"]]["prediction"][qid] = {"label": max(probabilities, key=probabilities.get), "probabilities": probabilities}
    for row in result.values(): validate_prediction(row["prediction"])
    return list(result.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--weight-sha", required=True)
    args = parser.parse_args(); root = args.root
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    if sha(args.checkpoint / "model.safetensors") != args.weight_sha: raise ValueError("Checkpoint hash mismatch")
    protocol = json.loads((root / "protocol.json").read_text())
    if protocol["input_sha256"] != sha(root / "inputs.jsonl") or protocol["questions"] != QUESTIONS:
        raise ValueError("Frozen inference inputs/questions changed")
    all_records = read_jsonl(root / "inputs.jsonl")
    records = [r for r in all_records if not quality_reasons(r)]
    if (root / "base_shadow_predictions.jsonl").exists(): raise ValueError("Shadow output exists; do not overwrite")
    with (root / "base_shadow_reservation.json").open("x") as stream:
        json.dump({"inputs_sha256": protocol["input_sha256"], "checkpoint_sha256": args.weight_sha,
                   "checkpoint_config_sha256": sha(args.checkpoint / "config.json"),
                   "temperature": 1, "calibration": "not fitted; raw logits for development diagnostics",
                   "trained": False, "independent_test": False, "max_len": 2048, "head_max_len": 512}, stream)
    import torch
    import laya.train as t
    from laya.common import build_head, encode_text, serialize_state
    torch.set_num_threads(6); torch.set_num_interop_threads(1)
    device = torch.device("cuda")
    model, tok, _ = t.load_checkpoint(str(args.checkpoint)); model.to(device)
    supplied = inference_rows(records)
    items, skipped = t.items_from_rows(tok, supplied, 2048, 512)
    if skipped or len(items) != 2*len(records): raise ValueError("Inference question skipped")
    metadata = [(r, qid) for r, row in zip(records, supplied) for qid in row["questions"]]
    for item, (record, qid) in zip(items, metadata):
        head = build_head(tok, item["q"], 512)[0]
        if len(head) != len(build_head(tok, item["q"], 8192)[0]): raise ValueError("Head would truncate")
        if any(len(encode_text(tok, text, add_special_tokens=False)['input_ids']) > 48 for text in item["q"]["crit"].values()):
            raise ValueError("Option would truncate")
        n = len(encode_text(tok, serialize_state(model_state(record)), add_special_tokens=False)['input_ids'])
        if n+len(head)+1 > 2048: raise ValueError("State would truncate")
    rec = t.calibration_records(model, tok, items, device, 2048, 512, batch_size=4)
    mapping = [(r, qid, list(item["q"]["crit"])) for (r, qid), item in zip(metadata, items)]
    predictions = format_predictions(records, mapping, [list(map(float, x[1])) for x in rec], args.weight_sha)
    write_jsonl(root / "base_shadow_predictions.jsonl", predictions)
    write_json(root / "base_shadow_summary.json", {"pairs": len(predictions), "pending_retained": len(all_records)-len(predictions),
               "trained": False, "production_enabled": False, "automatic_deletion": False, "independent_test_predictions": 0})
    print(json.dumps({"pairs": len(predictions), "trained": False, "production_enabled": False}))


if __name__ == "__main__": main()
