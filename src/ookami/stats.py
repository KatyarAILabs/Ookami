"""Gate statistics: paired comparison of candidate vs incumbent on the same items.

Pure Python on purpose (no numpy): item counts at the gate are hundreds to low thousands.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass

from .config import Gate


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def quantile(sorted_xs: list[float], q: float) -> float:
    if not sorted_xs:
        return float("nan")
    k = (len(sorted_xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)


def paired_bootstrap(diffs: list[float], confidence: float, resamples: int, seed: int = 0) -> tuple[float, float]:
    """One-sided bounds of the mean difference: (lower at 1-confidence, upper at confidence)."""
    if not diffs:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    return quantile(means, 1 - confidence), quantile(means, confidence)


@dataclass
class GateResult:
    evaluator: str
    split: str
    slice: str
    n: int
    candidate: float
    incumbent: float
    diff: float
    lower: float
    upper: float
    passed: bool
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def judge(evaluator: str, split: str, slice_: str, pairs: list[tuple[float, float]], gate: Gate) -> GateResult:
    cand = [c for c, _ in pairs]
    inc = [i for _, i in pairs]
    diffs = [c - i for c, i in pairs]
    lower, upper = paired_bootstrap(diffs, gate.confidence, gate.resamples)
    c, i, d = mean(cand), mean(inc), mean(diffs)
    n = len(pairs)
    pct = f"{gate.confidence:.0%}"
    if n < gate.minItems:
        passed, reason = False, f"only {n} paired items; the gate needs at least {gate.minItems}"
    elif gate.test == "non-inferiority":
        passed = lower >= gate.margin
        reason = (f"{pct} lower bound of the difference {lower:+.3f} "
                  f"{'>=' if passed else '<'} margin {gate.margin:+.3f}")
    elif gate.test == "superiority":
        passed = lower > 0
        reason = f"{pct} lower bound of the difference {lower:+.3f} {'>' if passed else '<='} 0"
    else:
        passed = c >= gate.min
        reason = f"candidate mean {c:.3f} {'>=' if passed else '<'} min {gate.min:.3f}"
    if passed and gate.min is not None and gate.test != "threshold" and c < gate.min:
        passed, reason = False, f"candidate mean {c:.3f} < min {gate.min:.3f}"
    return GateResult(evaluator, split, slice_, n, c, i, d, lower, upper, passed, reason)
