"""The Evaluator seam: how customers tell Forge what "good" means. Forge never decides correctness itself.

Two shapes:
  RowEvaluator      score(example, output) -> Score. Forge produces the output (calls the model, or reads a
                    recorded one) for each held-out row.
  RolloutEvaluator  run(target) -> observations. The evaluator drives the model itself: agent simulators,
                    lm-eval-harness, Inspect. It also defines its own items.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class Score:
    value: float                   # higher is better; 0..1 for pass/fail evaluators
    passed: bool | None = None
    reason: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Example:
    id: str
    input: Any                     # chat messages (list of dicts) or a prompt string
    label: Any = None
    meta: dict[str, Any] = field(default_factory=dict)   # slice fields, tools, response_format, ...


@dataclass
class Observation:
    """One scored attempt at one item. Several per item (trials) are averaged before pairing."""
    item_id: str
    score: Score
    slice: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Target:
    """The thing being evaluated: a live OpenAI-compatible endpoint, or recorded results to replay."""
    label: str
    kind: str                      # "openai" | "results"
    base_url: str | None = None
    model: str | None = None
    path: Path | None = None

    @classmethod
    def parse(cls, spec: str, label: str) -> "Target":
        """openai:<base_url>#<model>   or   results:<path>"""
        if spec.startswith("openai:"):
            url, _, model = spec[len("openai:"):].partition("#")
            if not model:
                raise ValueError("openai target needs a model: openai:http://host:8000/v1#model-id")
            return cls(label, "openai", base_url=url.rstrip("/"), model=model)
        if spec.startswith("results:"):
            return cls(label, "results", path=Path(spec[len("results:"):]).expanduser().resolve())
        raise ValueError(f"target must start with openai: or results:, got {spec!r}")

    def describe(self) -> str:
        return f"{self.model} @ {self.base_url}" if self.kind == "openai" else str(self.path)


@runtime_checkable
class RowEvaluator(Protocol):
    name: str
    kind: str
    version: str

    def score(self, example: Example, output: Any) -> Score: ...


@runtime_checkable
class RolloutEvaluator(Protocol):
    name: str
    kind: str
    version: str

    def run(self, target: Target) -> tuple[list[Observation], list[str]]:
        """Return observations and notes (e.g. runs dropped for infrastructure errors)."""
        ...


# ---------------------------------------------------------------- customer SDK

def evaluator(name: str | None = None, version: str = "1") -> Callable[[Callable], Callable]:
    """Mark a customer function as a Forge evaluator:

        @evaluator(name="task_check", version="2")
        def score(example, output) -> Score: ...
    """
    def wrap(fn: Callable) -> Callable:
        fn.forge_name = name or fn.__name__
        fn.forge_version = version
        return fn
    return wrap


def load_ref(ref: str, base_dir: Path) -> Callable:
    """Import `path/to/file.py:function`, relative to forge.yaml."""
    file, sep, fn = ref.rpartition(":")
    if not sep:
        raise ValueError(f"python ref must be file.py:function, got {ref!r}")
    path = Path(file)
    path = path if path.is_absolute() else (base_dir / path).resolve()
    mod_name = f"forge_user_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    try:
        return getattr(module, fn)
    except AttributeError:
        raise ImportError(f"{path} has no function {fn!r}") from None


def as_score(x: Any) -> Score:
    """Customers may return a Score, a bool, a number, or a dict with value/passed/reason."""
    if isinstance(x, Score):
        return x
    if isinstance(x, bool):
        return Score(float(x), passed=x)
    if isinstance(x, (int, float)):
        return Score(float(x))
    if isinstance(x, dict) and "value" in x:
        return Score(float(x["value"]), x.get("passed"), x.get("reason"), x.get("evidence") or {})
    raise TypeError(f"evaluator returned {type(x).__name__}; return a Score, bool, number or {{value: ...}}")


def post_json(url: str, payload: dict, timeout: float = 60.0, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())
