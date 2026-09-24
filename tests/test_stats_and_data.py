import json

from forge.config import Gate, Splits
from forge.data import assign_splits, load_jsonl
from forge.evaluators.base import Example
from forge.stats import judge, paired_bootstrap


def test_bootstrap_is_deterministic_and_brackets_mean():
    diffs = [0.1, -0.05, 0.2, 0.0, 0.05] * 10
    lo, hi = paired_bootstrap(diffs, 0.95, 2000)
    assert (lo, hi) == paired_bootstrap(diffs, 0.95, 2000)
    assert lo < sum(diffs) / len(diffs) < hi


def test_non_inferiority_passes_equal_models_and_fails_worse():
    gate = Gate(margin=-0.05, minItems=20, resamples=2000)
    same = [(1.0, 1.0)] * 30 + [(0.0, 0.0)] * 30
    assert judge("e", "s", "all", same, gate).passed
    worse = [(0.0, 1.0)] * 20 + [(1.0, 1.0)] * 40
    r = judge("e", "s", "all", worse, gate)
    assert not r.passed and r.lower < -0.05


def test_too_few_items_cannot_pass():
    r = judge("e", "s", "all", [(1.0, 0.0)] * 5, Gate(minItems=20, resamples=1000))
    assert not r.passed and "at least 20" in r.reason


def test_threshold_and_min():
    pairs = [(0.8, 0.9)] * 30
    assert judge("e", "s", "all", pairs, Gate(test="threshold", min=0.7, resamples=1000)).passed
    assert not judge("e", "s", "all", pairs, Gate(test="threshold", min=0.85, resamples=1000)).passed
    assert not judge("e", "s", "all", [(0.5, 0.5)] * 30, Gate(min=0.6, resamples=1000)).passed


def test_splits_are_stable_and_keep_duplicates_together():
    exs = [Example(str(i), f"prompt {i % 400}") for i in range(2000)]
    s = Splits(heldOut=0.1, audit=0.05)
    a = assign_splits(exs, s)
    assert a == assign_splits(list(reversed(exs)), s)
    by_input = {}
    for x in exs:
        by_input.setdefault(x.input, set()).add(a[x.id])
    assert all(len(v) == 1 for v in by_input.values())
    share = sum(v != "train" for v in a.values()) / len(a)
    assert 0.08 < share < 0.25
    grown = exs + [Example(f"n{i}", f"new {i}") for i in range(500)]
    assert all(assign_splits(grown, s)[k] == v for k, v in a.items())


def test_load_jsonl_moves_final_assistant_to_reference(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"id": "a", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}], "route": "r1"},
        {"prompt": "2+2?", "label": "4"},
    ]))
    a, b = load_jsonl(p)
    assert a.input == [{"role": "user", "content": "hi"}] and a.meta["reference"]["content"] == "yo"
    assert a.meta["route"] == "r1"
    assert b.input == "2+2?" and b.label == "4" and len(b.id) == 16
