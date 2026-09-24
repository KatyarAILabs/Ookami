"""Standard harnesses as rollout evaluators: Inspect (UK AISI) and lm-evaluation-harness (EleutherAI).

Both drive the model themselves through its OpenAI-compatible endpoint and report per-sample scores, which
Ookami pairs across candidate and incumbent like any other evaluator. They run through `uvx` in their own
environments (no install needed if uv is present), or through OOKAMI_INSPECT_CMD / OOKAMI_LM_EVAL_CMD.

  - inspect: { task: path/to/task.py or registry name, limit: 50, epochs: 1, scorer: match }
  - lm-eval: { tasks: [gsm8k], limit: 100, metric: exact_match, filter: flexible-extract }

Targets: openai:<url>#<model> runs the harness now; results:<path> replays an Inspect JSON log or an
lm-eval output directory written earlier.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .base import Observation, Score, Target

INSPECT_VALUES = {"C": 1.0, "I": 0.0, "P": 0.5, "N": 0.0}   # correct, incorrect, partial, no answer
LM_EVAL_METRICS = ("exact_match", "acc", "acc_norm", "f1", "em", "pass@1", "bleu")


# Each harness runs in its own environment: inspect-ai needs openai>=3.1 while LiteLLM (the gateway) needs
# openai<3, so they can't share Ookami's. uvx builds and caches an isolated environment per tool.
TOOLS = {"inspect": ("inspect-ai", "inspect", ["openai>=3.1"]), "lm_eval": ("lm-eval[api]", "lm_eval", [])}


def _exe(name: str) -> list[str]:
    override = os.environ.get(f"OOKAMI_{name.upper()}_CMD")        # e.g. OOKAMI_INSPECT_CMD="/opt/inspect/bin/inspect"
    if override:
        return shlex.split(override)
    package, exe, extras = TOOLS[name]
    if shutil.which("uvx"):
        return ["uvx", "--quiet", "--from", package, *[a for x in extras for a in ("--with", x)], exe]
    found = shutil.which(exe)
    if not found:
        raise FileNotFoundError(f"{exe} not found: install uv (for uvx) or `pipx install '{package}'`, "
                                f"or set OOKAMI_{name.upper()}_CMD")
    return [found]


def _env(target: Target) -> dict[str, str]:
    key = os.environ.get("OOKAMI_API_KEY", "none")
    return {**os.environ, "OOKAMI_API_KEY": key, "OOKAMI_BASE_URL": target.base_url or "", "OPENAI_API_KEY": key}


class InspectEvaluator:
    kind = "inspect"
    version = "1"

    def __init__(self, name: str, base_dir: Path, task: str, limit: int | None = None, epochs: int = 1,
                 scorer: str | None = None, args: list[str] | None = None, **_: Any):
        path = base_dir / task
        self.name, self.limit, self.epochs, self.scorer = name, limit, epochs, scorer
        self.task = str(path) if path.exists() else task          # a file next to ookami.yaml, or a registry name
        self.args = args or []

    def run(self, target: Target) -> tuple[list[Observation], list[str]]:
        if target.kind == "results":
            return parse_inspect_log(target.path, self.scorer)
        out = Path(tempfile.mkdtemp(prefix="ookami-inspect-"))
        # openai-api/<provider>/<model> reads <PROVIDER>_API_KEY and <PROVIDER>_BASE_URL: provider "ookami"
        task, cwd = self.task, None
        if Path(task).is_file():          # inspect only accepts task paths relative to its working directory
            task, cwd = Path(task).name, str(Path(task).parent)
        argv = [*_exe("inspect"), "eval", task, "--model", f"openai-api/ookami/{target.model}",
                "--log-dir", str(out), "--log-format", "json", "--epochs", str(self.epochs), "--no-log-realtime",
                *(["--limit", str(self.limit)] if self.limit else []), *self.args]
        r = subprocess.run(argv, env=_env(target), capture_output=True, text=True, cwd=cwd)
        logs = sorted(out.glob("*.json"))
        if not logs:
            raise RuntimeError(f"inspect wrote no log (exit {r.returncode}): {(r.stderr or r.stdout)[-600:]}")
        return parse_inspect_log(logs[-1], self.scorer)


def parse_inspect_log(path: Path, scorer: str | None = None) -> tuple[list[Observation], list[str]]:
    log = json.loads(Path(path).read_text())
    notes = []
    if log.get("status") != "success":
        notes.append(f"inspect log status: {log.get('status')}")
    task = (log.get("eval") or {}).get("task", "inspect")
    obs = []
    errors = 0
    for s in log.get("samples") or []:
        if s.get("error"):
            errors += 1
            continue
        scores = s.get("scores") or {}
        if not scores:
            continue
        key = scorer if scorer in scores else next(iter(scores))
        v = scores[key].get("value")
        value = INSPECT_VALUES.get(v, v) if isinstance(v, str) else v
        if isinstance(value, bool):
            value = float(value)
        if not isinstance(value, (int, float)):
            continue
        obs.append(Observation(str(s.get("id")), Score(float(value), passed=float(value) >= 1.0,
                                                        reason=None if float(value) >= 1.0 else f"{key}={v}"),
                               {"task": str(task)}))
    if errors:
        notes.append(f"{Path(path).name}: dropped {errors} samples that errored")
    return obs, notes


class LmEvalEvaluator:
    kind = "lm-eval"
    version = "1"

    def __init__(self, name: str, base_dir: Path, tasks: list[str], limit: int | None = None,
                 metric: str | None = None, filter: str | None = None, fewshot: int | None = None,
                 args: list[str] | None = None, **_: Any):
        self.name, self.tasks, self.limit, self.metric, self.filter = name, list(tasks), limit, metric, filter
        self.fewshot, self.args = fewshot, args or []

    def run(self, target: Target) -> tuple[list[Observation], list[str]]:
        if target.kind == "results":
            return parse_lm_eval_samples(target.path, self.metric, self.filter)
        out = Path(tempfile.mkdtemp(prefix="ookami-lmeval-"))
        model_args = [f"model={target.model}", f"base_url={target.base_url}/chat/completions", "num_concurrent=1",
                      "max_retries=3", "tokenized_requests=False"]
        argv = [*_exe("lm_eval"), "run", "--model", "local-chat-completions", "--model_args", *model_args,
                "--tasks", *self.tasks, "--apply_chat_template", "--log_samples", "--output_path", str(out),
                *(["--limit", str(self.limit)] if self.limit else []),
                *(["--num_fewshot", str(self.fewshot)] if self.fewshot is not None else []), *self.args]
        r = subprocess.run(argv, env=_env(target), capture_output=True, text=True)
        if not list(out.rglob("samples_*.jsonl")):
            raise RuntimeError(f"lm-eval wrote no samples (exit {r.returncode}): {(r.stderr or r.stdout)[-600:]}")
        return parse_lm_eval_samples(out, self.metric, self.filter)


def parse_lm_eval_samples(root: Path, metric: str | None = None,
                          filter_: str | None = None) -> tuple[list[Observation], list[str]]:
    obs, notes = [], []
    for f in sorted(Path(root).rglob("samples_*.jsonl")):
        task = f.name[len("samples_"):].rsplit("_", 1)[0]
        rows = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        filters = [r.get("filter") for r in rows if r.get("filter")]
        chosen = filter_ or (filters[0] if filters else None)
        for r in rows:
            if chosen and r.get("filter") not in (None, chosen):
                continue
            key = metric if metric in r else next((m for m in LM_EVAL_METRICS if m in r), None)
            if key is None or not isinstance(r[key], (int, float)):
                continue
            v = float(r[key])
            obs.append(Observation(f"{task}:{r.get('doc_id')}", Score(v, passed=v >= 1.0,
                                                                      reason=None if v >= 1.0 else f"{key}={v:g}"),
                                   {"task": task}))
        if chosen:
            notes.append(f"lm-eval {task}: using filter {chosen}")
    return obs, notes
