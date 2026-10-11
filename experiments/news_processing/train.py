"""One frozen two-question GPU experiment, using development weak references only.

Run with the GPU's existing Laya environment. The web server need not install Torch.
"""
import argparse
import copy
import gc
import hashlib
import json
import math
import os
import random
import statistics
import time
from collections import Counter
from pathlib import Path

from .contract import QUESTIONS, input_digest, model_state
from .fusion import assert_weak_training_row, assert_training_support
from .storage import read_jsonl, write_json, write_jsonl


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metrics(predictions):
    result = {}
    for qid, question in QUESTIONS.items():
        labels = list(question["criteria"])
        conf = {a: {b: 0 for b in labels} for a in labels}
        for row in predictions: conf[row["expected"][qid]][row["prediction"][qid]["label"]] += 1
        n = len(predictions)
        recalls, f1 = {}, []
        for a in labels:
            tp = conf[a][a]; support = sum(conf[a].values()); selected = sum(conf[b][a] for b in labels)
            recalls[a] = {"correct": tp, "support": support, "recall": tp/support if support else None}
            f1.append(2*tp/(support+selected) if support+selected else 0)
        result[qid] = {"n": n, "accuracy": sum(conf[a][a] for a in labels)/n if n else None,
                       "macro_f1": statistics.mean(f1), "confusion": conf, "class_recall": recalls}
    result["mean_question_macro_f1"] = statistics.mean(result[q]["macro_f1"] for q in QUESTIONS)
    relevant = [p for p in predictions if p["expected"]["industry_relation"] == "CLEAR"]
    unrelated = [p for p in predictions if p["expected"]["industry_relation"] == "NONE"]
    result["industry_clear_retained"] = {"n": len(relevant), "retained": sum(p["prediction"]["industry_relation"]["label"] != "NONE" for p in relevant)}
    result["industry_none_promoted"] = {"n": len(unrelated), "promoted": sum(p["prediction"]["industry_relation"]["label"] == "CLEAR" for p in unrelated)}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(); root = args.root
    freeze = json.loads((root / "training_freeze.json").read_text())
    for name, value in freeze["files"].items():
        if sha(root / name) != value: raise ValueError("Training freeze changed: " + name)
    config_raw = json.loads((root / "training_config.json").read_text())
    base = Path(config_raw["base_checkpoint"])
    if sha(base / "model.safetensors") != config_raw["base_weight_sha256"]: raise ValueError("Base model changed")
    if not json.loads((root / "dataset_manifest.json").read_text())["training_gate"]["passed"]:
        raise ValueError("Missing class support; do not force labels or reuse test material")
    rows = {role: read_jsonl(root / (role + ".jsonl")) for role in ("train", "validation", "calibration")}
    assert_training_support(rows, json.loads((root / "dataset_manifest.json").read_text())["training_gate"])
    groups = {role: {r["group_id"] for r in rs} for role, rs in rows.items()}
    for a in groups:
        for b in groups:
            if a != b and groups[a] & groups[b]: raise ValueError("Event group crosses roles")
    for rs in rows.values():
        for r in rs:
            assert_weak_training_row(r)
    # O_EXCL creates the irreversible one-run reservation before any student prediction.
    with (root / "run_started.json").open("x") as stream:
        json.dump({"time": time.time(), "freeze_sha256": sha(root / "training_freeze.json")}, stream)
    import numpy as np
    import torch
    import laya
    import laya.train as t
    from laya.common import build_head, encode_text, serialize_state
    from laya.calibrate import fit_temperature_map
    torch.set_num_threads(6); torch.set_num_interop_threads(1); torch.manual_seed(config_raw["seed"])
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported(): raise ValueError("This declared run requires BF16 CUDA")
    torch.set_float32_matmul_precision("high")
    original_forward = t._forward
    def bf16_forward(model, batch, device, amp, detach_encoder):
        if amp:
            with torch.autocast("cuda", dtype=torch.bfloat16): return original_forward(model, batch, device, False, detach_encoder)
        return original_forward(model, batch, device, False, detach_encoder)
    t._forward = bf16_forward
    device = torch.device("cuda"); max_len = config_raw["max_len"]; head_len = config_raw["head_max_len"]
    config = t.TrainConfig(**{k: v for k, v in config_raw.items() if k in t.TrainConfig.__dataclass_fields__})
    config.shuffle_options = tuple(config.shuffle_options)
    model, tok, cfg = t.load_checkpoint(str(base)); model.to(device)

    def items_for(rs, tokenizer, smoothing=0):
        supplied = [{"state": model_state(r), "questions": r["questions"], "expected": r["expected"]} for r in rs]
        items, skipped = t.items_from_rows(tokenizer, supplied, max_len, head_len, label_smoothing=smoothing)
        metadata = [(r, qid) for r in rs for qid in r["questions"]]
        if skipped or len(items) != len(metadata): raise ValueError("Question item dropped: " + str(skipped))
        for item, (r, _) in zip(items, metadata):
            head = build_head(tokenizer, item["q"], head_len)[0]
            # Never train on a silently truncated state/head.
            full_head = build_head(tokenizer, item["q"], 8192)[0]
            state_n = len(encode_text(tokenizer, serialize_state(model_state(r)), add_special_tokens=False)["input_ids"])
            if len(head) != len(full_head) or state_n + len(head) + 1 > max_len:
                raise ValueError("Input would truncate: " + r["id"])
        return items, metadata

    def predict(m, tokenizer, mcfg, rs):
        items, metadata = items_for(rs, tokenizer)
        rec = t.calibration_records(m, tokenizer, items, device, max_len, head_len, batch_size=4)
        predictions = {r["id"]: {"id": r["id"], "group_id": r["group_id"], "input_sha256": input_digest(r),
                                  "expected": r["expected"], "prediction": {}} for r in rs}
        for (qt, logits, _, k), item, (r, qid) in zip(rec, items, metadata):
            from laya.common import temp_bucket
            temperature = mcfg.get("temperature_by_options", {}).get(temp_bucket(qt, k), mcfg.get("temperature", [1, 1, 1])[qt])
            z = np.asarray(logits, dtype=float) / temperature; z -= z.max(); probs = np.exp(z); probs /= probs.sum()
            labels = list(item["q"]["crit"])
            predictions[r["id"]]["prediction"][qid] = {"label": labels[int(probs.argmax())],
                                                        "probabilities": dict(zip(labels, map(float, probs)))}
        return list(predictions.values())

    out = root / "trained"; out.mkdir()
    baseline = predict(model, tok, cfg, rows["validation"])
    write_jsonl(out / "base_validation_predictions.jsonl", baseline)
    train_items, metadata = items_for(rows["train"], tok, config.label_smoothing)
    buckets = {}
    for item, (r, qid) in zip(train_items, metadata): buckets.setdefault((qid, r["expected"][qid]), []).append(item)
    balanced, sampled = [], {}; rng = random.Random(config.seed)
    for qid in QUESTIONS:
        size = max(len(buckets[qid, label]) for label in QUESTIONS[qid]["criteria"])
        for label in QUESTIONS[qid]["criteria"]:
            values = buckets[qid, label]; balanced += values*(size//len(values)) + rng.sample(values, size%len(values))
            sampled[qid+"/"+label] = size
    rng.shuffle(balanced)
    best = {"mean_question_macro_f1": -1, "epoch": None}; progress = []
    start = time.perf_counter(); torch.cuda.reset_peak_memory_stats()
    def epoch_done(epoch, loss):
        pred = predict(model, tok, {"temperature": [1, 1, 1]}, rows["validation"])
        score = metrics(pred)["mean_question_macro_f1"]
        entry = {"epoch": epoch+1, "loss": loss, "development_macro_f1": score, "seconds": time.perf_counter()-start}
        progress.append(entry); print(json.dumps(entry), flush=True)
        if score > best["mean_question_macro_f1"]:
            best.update(mean_question_macro_f1=score, epoch=epoch+1)
            t.save_checkpoint(model, tok, dict(cfg, max_len=max_len, head_max_len=head_len), str(out / "best"))
        write_json(out / "progress.json", {"best": best, "epochs": progress}); model.train()
    t.train_model(model, tok, balanced, config, device, max_len, head_len, on_epoch_end=epoch_done)
    seconds = time.perf_counter()-start; peak = torch.cuda.max_memory_reserved()/1024**3
    del model; gc.collect(); torch.cuda.empty_cache()
    model, tok, cfg = t.load_checkpoint(str(out / "best")); model.to(device)
    selected_sha = sha(out / "best/model.safetensors")
    cal_items, _ = items_for(rows["calibration"], tok)
    cal_records = t.calibration_records(model, tok, cal_items, device, max_len, head_len, batch_size=4)
    fitted = fit_temperature_map(cal_records)
    cfg.update(max_len=max_len, head_max_len=head_len, temperature=fitted["temperature"], fine_tuned=True)
    cfg.pop("temperature_by_options", None)
    if fitted["temperature_by_options"]: cfg["temperature_by_options"] = fitted["temperature_by_options"]
    cfg["training"] = {"development_only": True, "weak_references": True, "production_enabled": False, "selected_epoch": best["epoch"]}
    t.save_checkpoint(model, tok, cfg, str(out / "checkpoint")); write_json(out / "checkpoint/questions.json", QUESTIONS)
    if sha(out / "checkpoint/model.safetensors") != selected_sha: raise ValueError("Export changed selected tensors")
    candidate = predict(model, tok, cfg, rows["validation"])
    write_jsonl(out / "candidate_validation_predictions.jsonl", candidate)
    # Retain quality-pending inputs as placeholders in replay; no inference for them.
    from .pipeline import quality_reasons
    all_rows = [{**r, "expected": {"own_event": "NO", "industry_relation": "NONE"}} for r in read_jsonl(root / "inputs.jsonl") if not quality_reasons(r)]
    shadow = predict(model, tok, cfg, all_rows)
    for row in shadow: row.pop("expected", None)
    write_jsonl(root / "student_shadow_predictions.jsonl", shadow)
    result = {"completed": True, "training_seconds": seconds, "peak_reserved_gib": peak, "gpu": torch.cuda.get_device_name(0),
              "laya_version": laya.__version__, "torch_version": torch.__version__, "selected_epoch": best["epoch"],
              "selected_weight_sha256": selected_sha, "sampled_counts": sampled, "calibration": fitted,
              "base": metrics(baseline), "candidate": metrics(candidate), "production_enabled": False,
              "independent_test_predictions": 0, "human_verified": False, "automatic_deletion": False,
              "limitation": "Development agreement with same-provider weak references; validation used for epoch selection; sparse own-event support; replay includes training cases and is not evaluation."}
    write_json(out / "results.json", result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__": main()
