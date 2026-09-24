"""Datasets, splits and getting outputs from a target.

Splits are hash-based per group of identical inputs:
  - a row never changes split as the dataset grows, so held-out and audit sets stay frozen;
  - duplicate inputs always land in the same split, so a test prompt can't also be a training prompt;
  - every stratum gets the same expected share.
"""
from __future__ import annotations

import hashlib
import os
import json
from pathlib import Path
from typing import Any

from dataclasses import dataclass

from .config import Config, DataSource, Splits, TracesSource
from .evaluators.base import Example, Target, post_json


def to_example(row: dict, where: str) -> Example:
    """Rows: {id?, messages | input | prompt, label?, ...meta}. A trailing assistant message becomes the reference."""
    row = dict(row)
    raw_id = row.pop("id", None)
    rid = str(raw_id) if raw_id not in (None, "") else \
        hashlib.sha1(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:16]
    label = row.pop("label", None)
    if row.get("messages") is not None:
        msgs = list(row.pop("messages"))
        if msgs and msgs[-1].get("role") == "assistant":
            row["reference"] = msgs[-1]
            msgs = msgs[:-1]
        inp: Any = msgs
    elif row.get("input") is not None or row.get("prompt") is not None:
        inp = row.pop("input", None) or row.pop("prompt")
    else:
        raise ValueError(f"{where}: a row needs messages, input or prompt")
    return Example(rid, inp, label, row)


def load_jsonl(path: Path) -> list[Example]:
    return [to_example(json.loads(line), f"{path}:{n}")
            for n, line in enumerate(path.read_text().splitlines(), 1) if line.strip()]


def load_source(src: DataSource, cfg: Config) -> tuple[list[Example], str]:
    """Examples and a content hash for any data source."""
    if src.jsonl:
        path = cfg.resolve(src.jsonl)
        return load_jsonl(path), dataset_hash(path)
    if src.parquet:
        try:
            import pyarrow.parquet as pq
        except ImportError:
            raise ImportError("parquet sources need pyarrow: pip install 'ookami[parquet]'") from None
        path = cfg.resolve(src.parquet)
        rows = pq.read_table(path).to_pylist()
        return [to_example(r, f"{path}:{i}") for i, r in enumerate(rows)], dataset_hash(path) if path.is_file() \
            else _rows_hash(rows)
    if src.hf:
        try:
            import datasets
        except ImportError:
            raise ImportError("Hugging Face sources need datasets: pip install 'ookami[hf]'") from None
        name, _, split = src.hf.partition(":")
        rows = list(datasets.load_dataset(name, split=split or "train"))
        return [to_example(r, f"{src.hf}:{i}") for i, r in enumerate(rows)], _rows_hash(rows)
    if src.traces:
        path = export_traces(src.traces, cfg)
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        out = []
        for i, r in enumerate(rows):
            meta = r.pop("metadata", None) or {}
            r.setdefault("id", f"{meta.get('episode_id', 'ep')}:{meta.get('step_idx', i)}")
            out.append(to_example({**meta, **r}, f"{path}:{i}"))
        return out, dataset_hash(path)
    raise ValueError("data.source has no source set")


def export_traces(src: "TracesSource", cfg: Config) -> Path:
    """Run Trajectory's `cc export -format chat` against the lake; returns the JSONL it wrote."""
    import subprocess
    import tempfile
    tr = cfg.platform.spec.tracing if cfg.platform else None
    lake = src.lake or (tr.lake if tr else None)
    if not lake:
        raise ValueError("traces source needs a lake: set data.source.traces.lake or Platform.tracing.lake")
    exe = tr.bin if tr else "cc"
    out = Path(tempfile.mkdtemp(prefix="ookami-traces-")) / "chat.jsonl"
    argv = [exe, "export", "-lake", str(cfg.resolve(lake)), "-format", "chat", "-out", str(out),
            f"-require-final={'true' if src.requireFinal else 'false'}"]
    if src.requireReward:
        argv.append("-require-reward")
    if src.minReward is not None:
        argv += ["-min-reward", str(src.minReward)]
    if src.verifier:
        argv += ["-verifier", src.verifier]
    if src.asOf:
        argv += ["-as-of", src.asOf]
    try:
        r = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError:
        raise FileNotFoundError(f"Trajectory CLI {exe!r} not found; install Trajectory or set Platform.tracing.bin") from None
    if r.returncode != 0:
        raise RuntimeError(f"cc export failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-800:]}")
    if not out.exists():
        out.write_text("")
    return out


def _rows_hash(rows: list[dict]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps(r, sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


def training_target(ex: Example) -> dict | None:
    """The assistant turn to learn: the reference message, else the label as text."""
    ref = ex.meta.get("reference")
    if isinstance(ref, dict) and (ref.get("content") or ref.get("tool_calls")):
        return {k: v for k, v in ref.items() if k in ("role", "content", "tool_calls")}
    if ex.label is not None:
        return {"role": "assistant", "content": str(ex.label)}
    return None


@dataclass
class Snapshot:
    dir: Path
    hash: str
    train: int
    valid: int
    held_out: int
    audit: int
    skipped: int


def snapshot(cfg: Config, model: str) -> Snapshot:
    """Write the training split of a Model's data as chat JSONL. Held-out and audit rows never enter it."""
    spec = cfg.models[model].spec
    if not spec.data:
        raise ValueError(f"Model {model!r} has no data section")
    examples, src_hash = load_source(spec.data.source, cfg)
    splits = spec.eval.splits if spec.eval else Splits()
    split_of = assign_splits(examples, splits)
    train_rows, skipped = [], 0
    for ex in examples:
        if split_of[ex.id] != "train":
            continue
        target = training_target(ex)
        if target is None:
            skipped += 1
            continue
        msgs = ex.input if isinstance(ex.input, list) else [{"role": "user", "content": str(ex.input)}]
        row: dict[str, Any] = {"messages": [*msgs, target]}
        if ex.meta.get("tools"):
            row["tools"] = ex.meta["tools"]
        train_rows.append(row)
    if len(train_rows) < spec.data.minExamples:
        raise ValueError(f"Model {model!r} is not ready: {len(train_rows)} training rows, "
                         f"data.minExamples is {spec.data.minExamples}")
    # a small validation slice from the training split, for loss curves only
    n_valid = max(1, min(200, len(train_rows) // 20))
    digest = hashlib.sha256(f"{src_hash}:{splits.seed}:{splits.heldOut}:{splits.audit}".encode()).hexdigest()[:16]
    out = cfg.resolve(_storage(cfg)) / "datasets" / model / digest
    out.mkdir(parents=True, exist_ok=True)
    valid, train = train_rows[:n_valid], train_rows[n_valid:]
    (out / "train.jsonl").write_text("\n".join(json.dumps(r) for r in train) + "\n")
    (out / "valid.jsonl").write_text("\n".join(json.dumps(r) for r in valid) + "\n")
    counts = {"train": len(train), "valid": len(valid),
              "heldOut": sum(v == "heldOut" for v in split_of.values()),
              "audit": sum(v == "audit" for v in split_of.values()), "skipped_no_target": skipped}
    (out / "manifest.json").write_text(json.dumps({"source_hash": src_hash, "splits": splits.model_dump(),
                                                   "counts": counts}, indent=2))
    return Snapshot(out, digest, len(train), len(valid), counts["heldOut"], counts["audit"], skipped)


def _storage(cfg: Config) -> str:
    uri = cfg.platform.spec.storage.uri if cfg.platform else "./.ookami"
    if uri.startswith("file://"):
        return uri[len("file://"):]
    if "://" in uri:
        raise ValueError(f"local training needs local storage, got {uri}")
    return uri


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


def outputs_for(target: Target, examples: list[Example], timeout: float = 120.0, max_tokens: int = 1024,
                temperature: float = 0.0) -> dict[str, Any]:
    """id -> output message. openai targets are called now; results targets are JSONL {id, output}."""
    if target.kind == "results":
        rows = [json.loads(line) for line in target.path.read_text().splitlines() if line.strip()]
        return {str(r["id"]): r["output"] for r in rows if "output" in r}
    out = {}
    for ex in examples:
        msgs = ex.input if isinstance(ex.input, list) else [{"role": "user", "content": str(ex.input)}]
        body: dict[str, Any] = {"model": target.model, "messages": msgs, "temperature": temperature,
                                "max_tokens": max_tokens}
        if ex.meta.get("tools"):
            body["tools"] = ex.meta["tools"]
        key = os.environ.get("OOKAMI_API_KEY")
        resp = post_json(f"{target.base_url}/chat/completions", body, timeout=timeout,
                         headers={"Authorization": f"Bearer {key}"} if key else None)
        out[ex.id] = resp["choices"][0]["message"]
    return out
