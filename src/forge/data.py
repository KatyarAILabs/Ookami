"""Datasets, splits and getting outputs from a target.

Splits are hash-based per group of identical inputs:
  - a row never changes split as the dataset grows, so held-out and audit sets stay frozen;
  - duplicate inputs always land in the same split, so a test prompt can't also be a training prompt;
  - every stratum gets the same expected share.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import Splits
from .evaluators.base import Example, Target, post_json


def load_jsonl(path: Path) -> list[Example]:
    """Rows: {id?, messages | input | prompt, label?, ...meta}. A trailing assistant message becomes the reference."""
    out: list[Example] = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        rid = str(row.pop("id", None) or hashlib.sha1(line.encode()).hexdigest()[:16])
        label = row.pop("label", None)
        if "messages" in row:
            msgs = row.pop("messages")
            if msgs and msgs[-1].get("role") == "assistant":
                row["reference"] = msgs[-1]
                msgs = msgs[:-1]
            inp: Any = msgs
        elif "input" in row or "prompt" in row:
            inp = row.pop("input", None) or row.pop("prompt")
        else:
            raise ValueError(f"{path}:{n}: a row needs messages, input or prompt")
        out.append(Example(rid, inp, label, row))
    return out


def group_key(ex: Example) -> str:
    return hashlib.sha256(json.dumps(ex.input, sort_keys=True).encode()).hexdigest()


def assign_splits(examples: list[Example], splits: Splits) -> dict[str, str]:
    """id -> train | heldOut | audit."""
    out = {}
    for ex in examples:
        h = hashlib.sha256(f"{splits.seed}:{group_key(ex)}".encode()).digest()
        u = int.from_bytes(h[:8], "big") / 2**64
        out[ex.id] = "audit" if u < splits.audit else "heldOut" if u < splits.audit + splits.heldOut else "train"
    return out


def dataset_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def outputs_for(target: Target, examples: list[Example], timeout: float = 120.0) -> dict[str, Any]:
    """id -> output message. openai targets are called now; results targets are JSONL {id, output}."""
    if target.kind == "results":
        rows = [json.loads(line) for line in target.path.read_text().splitlines() if line.strip()]
        return {str(r["id"]): r["output"] for r in rows if "output" in r}
    out = {}
    for ex in examples:
        msgs = ex.input if isinstance(ex.input, list) else [{"role": "user", "content": str(ex.input)}]
        body: dict[str, Any] = {"model": target.model, "messages": msgs, "temperature": 0}
        if ex.meta.get("tools"):
            body["tools"] = ex.meta["tools"]
        resp = post_json(f"{target.base_url}/chat/completions", body, timeout=timeout)
        out[ex.id] = resp["choices"][0]["message"]
    return out
