from __future__ import annotations

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
    raise NotImplementedError(f"{spec.kind} evaluators are in the schema but not implemented yet")
