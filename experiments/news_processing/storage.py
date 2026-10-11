"""Durable local artifacts, without credentials or production storage."""
import json
import os
from pathlib import Path

from .contract import canonical


def read_jsonl(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def append_jsonl(path, value):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)


def write_jsonl(path, values):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        for value in values: stream.write(canonical(value) + "\n")
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)
