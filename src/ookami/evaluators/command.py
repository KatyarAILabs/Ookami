"""Run any external benchmark or harness as an evaluator: agent simulators, internal test suites, anything
that can talk to an OpenAI-compatible endpoint and write per-item scores.

ookami.yaml:
  - command:
      run: "python bench.py --base-url {base_url} --model {model} --out {output}"
      cwd: ./bench          # optional, relative to ookami.yaml
      timeout: 3600         # seconds, optional
    name: my-bench

The command writes JSONL to {output}, one line per attempt:
  {"item_id": "task-7", "score": 1.0, "passed": true, "reason": "...", "slice": {"difficulty": "hard"}}
Several lines per item (trials) are averaged. Lines with "error": true are infrastructure failures and are
dropped, not scored.

Targets:
  openai:<base_url>#<model>   run the command now against that endpoint
  results:<path.jsonl>        replay a file the command wrote earlier
"""
from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .base import Observation, Score, Target


class CommandEvaluator:
    kind = "command"

    def __init__(self, name: str, base_dir: Path, run: str, cwd: str | None = None, timeout: float | None = None,
                 version: str = "1", **_: Any):
        self.name, self.run_template, self.timeout, self.version = name, run, timeout, str(version)
        self.cwd = (base_dir / cwd).resolve() if cwd else base_dir

    def run(self, target: Target) -> tuple[list[Observation], list[str]]:
        if target.kind == "results":
            return parse_scores(target.path)
        out = Path(tempfile.mkdtemp(prefix="ookami-eval-")) / f"{target.label}.jsonl"
        cmd = self.run_template.format(base_url=target.base_url, model=target.model, output=out, label=target.label)
        subprocess.run(shlex.split(cmd), cwd=self.cwd, timeout=self.timeout, check=True)
        if not out.exists():
            raise FileNotFoundError(f"{self.name}: the command did not write {out}")
        return parse_scores(out)


def parse_scores(path: Path) -> tuple[list[Observation], list[str]]:
    obs: list[Observation] = []
    errors = 0
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("error"):
            errors += 1
            continue
        if "score" not in row:
            continue            # e.g. {id, output} rows for row evaluators in the same file
        passed = row.get("passed")
        obs.append(Observation(str(row["item_id"]), Score(float(row["score"]), passed, row.get("reason")),
                               {str(k): str(v) for k, v in (row.get("slice") or {}).items()}))
    notes = [f"{Path(path).name}: dropped {errors} attempts marked as infrastructure errors"] if errors else []
    return obs, notes
