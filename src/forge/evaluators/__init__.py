from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path

from ..config import EvaluatorSpec
from .base import (Example, Observation, RolloutEvaluator, RowEvaluator, Score, Target, as_score, evaluator,
                   load_ref)
from .builtin import LabelsEvaluator, PythonEvaluator, StructuralEvaluator, WebhookEvaluator
from .command import CommandEvaluator

__all__ = ["Example", "Observation", "RolloutEvaluator", "RowEvaluator", "Score", "Target", "as_score",
           "evaluator", "load_ref", "build"]


def build(spec: EvaluatorSpec, base_dir: Path) -> RowEvaluator | RolloutEvaluator:
    p = spec.params
    if spec.kind == "structural":
        return StructuralEvaluator(spec.name)
    if spec.kind == "labels":
        return LabelsEvaluator(spec.name, **p)
    if spec.kind == "python":
        return PythonEvaluator(spec.name, base_dir=base_dir, **p)
    if spec.kind == "webhook":
        return WebhookEvaluator(spec.name, **p)
    if spec.kind == "command":
        return CommandEvaluator(spec.name, base_dir=base_dir, **p)
    if spec.kind == "plugin":
        return load_plugin(spec.name, base_dir, **p)
    raise NotImplementedError(f"{spec.kind} evaluators are in the schema but not implemented yet")


PLUGIN_GROUP = "forge.evaluators"


def load_plugin(name: str, base_dir: Path, use: str, **params):
    """Evaluators shipped as separate packages. A package registers a factory:

        [project.entry-points."forge.evaluators"]
        "acme.refund_check" = "acme_forge:refund_check"

    and forge.yaml uses it with `- plugin: { use: acme.refund_check, ...params }`.
    The factory is called as factory(name=..., base_dir=..., **params) and returns a Row or Rollout evaluator.
    """
    found = [ep for ep in entry_points(group=PLUGIN_GROUP) if ep.name == use]
    if not found:
        have = sorted(ep.name for ep in entry_points(group=PLUGIN_GROUP))
        raise ImportError(f"no evaluator plugin {use!r} installed; installed: {have or 'none'}")
    ev = found[0].load()(name=name, base_dir=base_dir, **params)
    if not isinstance(ev, (RowEvaluator, RolloutEvaluator)):
        raise TypeError(f"plugin {use!r} returned {type(ev).__name__}, not an evaluator")
    return ev
