"""Explicit commands; importing this package never contacts a provider."""
import argparse
import hashlib
import json
from pathlib import Path

from .contract import QUESTIONS, VERSION, digest, input_digest
from .dataset import import_records, assemble
from .pipeline import render_review, route
from .storage import read_jsonl, write_json, write_jsonl
from .teacher import PROMPTS, label


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Development-only news processing; never deletes news")
    commands = parser.add_subparsers(dest="command", required=True)
    imp = commands.add_parser("import")
    imp.add_argument("--source", type=Path, required=True)
    imp.add_argument("--expected-sha", required=True)
    imp.add_argument("--group-roles", type=Path, required=True)
    imp.add_argument("--profiles", type=Path)
    imp.add_argument("--root", type=Path, required=True)
    lab = commands.add_parser("label")
    lab.add_argument("--root", type=Path, required=True)
    lab.add_argument("--config", type=Path, required=True, help="Existing server-local config; never exported")
    ass = commands.add_parser("assemble")
    ass.add_argument("--root", type=Path, required=True)
    rep = commands.add_parser("replay")
    rep.add_argument("--root", type=Path, required=True)
    rep.add_argument("--predictions", type=Path)
    args = parser.parse_args()
    root = args.root
    if args.command == "import":
        if sha(args.source) != args.expected_sha: raise ValueError("Historical input hash mismatch")
        if root.exists() and any(root.iterdir()): raise ValueError("Import requires a new experiment directory")
        root.mkdir(parents=True, exist_ok=True)
        profiles = json.loads(args.profiles.read_text()) if args.profiles else []
        roles = json.loads(args.group_roles.read_text())
        records = import_records(read_jsonl(args.source), roles, profiles)
        write_jsonl(root / "inputs.jsonl", records)
        write_json(root / "protocol.json", {"version": VERSION, "source_sha256": args.expected_sha,
            "profiles_sha256": sha(args.profiles) if args.profiles else None, "group_roles": roles,
            "input_sha256": sha(root / "inputs.jsonl"), "questions": QUESTIONS,
            "prompts": PROMPTS, "prior_labels_not_imported": True, "development_only": True,
            "teacher_policy": {"max_attempts": 100, "token_soft_stop": 500000, "workers": 3, "batch_size": 4,
                               "max_output_tokens": 3200, "attempts_per_batch": 2},
            "production_enabled": False, "automatic_deletion": False, "human_verified": False})
        print(json.dumps({"pairs": len(records), "inputs_sha256": sha(root / "inputs.jsonl")}, ensure_ascii=False))
        return
    protocol = json.loads((root / "protocol.json").read_text())
    if sha(root / "inputs.jsonl") != protocol["input_sha256"]: raise ValueError("Frozen inputs changed")
    if protocol["questions"] != QUESTIONS or protocol["prompts"] != PROMPTS: raise ValueError("Frozen contract changed")
    records = read_jsonl(root / "inputs.jsonl")
    if args.command == "label":
        print(json.dumps(label(root, records, args.config, protocol["teacher_policy"]), ensure_ascii=False))
    else:
        primary = {r["id"]: r for r in read_jsonl(root / "primary.jsonl")}
        audit = {r["id"]: r for r in read_jsonl(root / "audit.jsonl")}
        assembled = assemble(records, primary, audit)
        if args.command == "assemble":
            for role, rows in assembled["datasets"].items(): write_jsonl(root / (role + ".jsonl"), rows)
            write_json(root / "dataset_manifest.json", {k: v for k, v in assembled.items() if k != "datasets"})
            write_jsonl(root / "human_review_queue.jsonl", assembled["review_queue"])
            print(json.dumps(assembled["training_gate"], ensure_ascii=False))
        else:
            predictions = {r["id"]: r for r in read_jsonl(args.predictions)} if args.predictions else {}
            if set(predictions) - {r["id"] for r in records}: raise ValueError("Predictions contain foreign ids")
            for record in records:
                pred = predictions.get(record["id"])
                if pred and pred.get("input_sha256") != input_digest(record):
                    raise ValueError("Prediction does not match frozen input")
            refs = {r["id"]: {"primary": primary.get(r["id"]), "audit": audit.get(r["id"]), "consensus": r}
                    for r in assembled["review_queue"]}
            write_jsonl(root / "shadow_routes.jsonl", [route(r, predictions.get(r["id"], {}).get("prediction")) for r in records])
            (root / "human_review.html").write_text(render_review(records,
                {rid: p["prediction"] for rid, p in predictions.items()}, refs), encoding="utf-8")
            print(json.dumps({"retained": len(records), "deleted": 0, "human_verified": 0, "production_enabled": False}))


if __name__ == "__main__": main()
