"""forge eval: run the customer's evaluators on candidate and incumbent, pair by item, apply the gate, write a report.

Decision:
  pass     every gating evaluator passes on every split and slice
  partial  the overall slice passes but some slices don't; those slices stay on the incumbent
  fail     otherwise
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import data
from .config import Config
from .evaluators import build
from .evaluators.base import Observation, RolloutEvaluator, Target
from .stats import GateResult, judge, mean

ALL = "all"


@dataclass
class Report:
    model: str
    decision: str
    candidate: str
    incumbent: str
    results: list[GateResult]
    info: list[dict[str, Any]]               # non-gating evaluators, reported only
    evaluators: list[dict[str, str]]
    notes: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["results"] = [r.as_dict() for r in self.results]
        return d

    def markdown(self) -> str:
        badge = {"pass": "PASS", "partial": "PARTIAL", "fail": "FAIL"}[self.decision]
        lines = [f"# forge eval: {self.model}: {badge}", "",
                 f"- candidate: `{self.candidate}`", f"- incumbent: `{self.incumbent}`", ""]
        lines += ["| evaluator | split | slice | n | candidate | incumbent | diff | bounds | gate |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in self.results:
            lines.append(f"| {r.evaluator} | {r.split} | {r.slice} | {r.n} | {r.candidate:.3f} | {r.incumbent:.3f} | "
                         f"{r.diff:+.3f} | [{r.lower:+.3f}, {r.upper:+.3f}] | {'pass' if r.passed else 'FAIL'}: {r.reason} |")
        if self.info:
            lines += ["", "Reported, not gating:"]
            lines += [f"- {i['evaluator']} ({i['split']}): candidate {i['candidate']:.3f}, "
                      f"incumbent {i['incumbent']:.3f}, n={i['n']}" for i in self.info]
        labels = [e for e in self.evaluators if e["kind"] == "structural"]
        if labels:
            lines += ["", "Note: `structural` checks shape only (JSON, tool names and arguments), not correctness."]
        if self.notes:
            lines += ["", "Notes:"] + [f"- {n}" for n in self.notes]
        return "\n".join(lines)


def run_eval(cfg: Config, model: str, candidate: Target, incumbent: Target) -> Report:
    if model not in cfg.models:
        raise KeyError(f"no Model {model!r} in {cfg.path.name}; have {sorted(cfg.models)}")
    spec = cfg.models[model].spec
    ev_cfg = spec.eval
    if ev_cfg is None:
        raise ValueError(f"Model {model!r} has no eval section")
    evaluators = [(s, build(s, cfg.base_dir)) for s in ev_cfg.evaluators]
    notes: list[str] = []
    provenance: dict[str, Any] = {"config_sha": hashlib.sha256(cfg.path.read_bytes()).hexdigest()[:16]}

    # observations[evaluator][split][target_label] -> list[Observation]
    obs: dict[str, dict[str, dict[str, list[Observation]]]] = defaultdict(lambda: defaultdict(dict))

    row_evs = [(s, e) for s, e in evaluators if not isinstance(e, RolloutEvaluator)]
    if row_evs:
        if not spec.data:
            raise ValueError(f"Model {model!r}: row evaluators need a data section")
        examples, provenance["dataset_sha"] = data.load_source(spec.data.source, cfg)
        split_of = data.assign_splits(examples, ev_cfg.splits)
        chosen = [x for x in examples if split_of[x.id] != "train"]
        if not chosen:
            raise ValueError("no held-out or audit rows; the dataset is too small for these split fractions")
        all_outs: dict[str, dict[str, Any]] = {}
        for target in (candidate, incumbent):
            outs = all_outs[target.label] = data.outputs_for(target, chosen, max_tokens=ev_cfg.generation.maxTokens,
                                                             temperature=ev_cfg.generation.temperature)
            missing = [x.id for x in chosen if x.id not in outs]
            if missing:
                notes.append(f"{target.label}: no output for {len(missing)} of {len(chosen)} rows")
            for spec_e, e in row_evs:
                for x in chosen:
                    if x.id not in outs:
                        continue
                    sl = {k: str(x.meta.get(k, "?")) for k in ev_cfg.splits.stratifyBy}
                    obs[spec_e.name][split_of[x.id]].setdefault(target.label, []).append(
                        Observation(x.id, e.score(x, outs[x.id]), sl))

        same = _identical_share(all_outs.get(candidate.label, {}), all_outs.get(incumbent.label, {}))
        if same is not None and same >= 0.95:
            notes.append(f"candidate and incumbent gave identical outputs on {same:.0%} of rows: check that the "
                         "candidate endpoint really serves the trained weights")

    for spec_e, e in evaluators:
        if isinstance(e, RolloutEvaluator):
            for target in (candidate, incumbent):
                got, n = e.run(target)
                notes += [f"{spec_e.name} / {target.label}: {m}" for m in n]
                obs[spec_e.name][f"{e.kind}:{getattr(e, 'split', 'items')}"][target.label] = got

    results: list[GateResult] = []
    info: list[dict[str, Any]] = []
    for spec_e, e in evaluators:
        for split, by_target in obs[spec_e.name].items():
            per_item = {t: _item_means(by_target.get(t, [])) for t in (candidate.label, incumbent.label)}
            c_items, i_items = per_item[candidate.label], per_item[incumbent.label]
            common = sorted(set(c_items) & set(i_items))
            only = len(set(c_items) ^ set(i_items))
            if only:
                notes.append(f"{spec_e.name} / {split}: {only} items scored for only one target were left out")
            slices: dict[str, list[str]] = {ALL: common}
            if ev_cfg.gate.perSlice:
                for item in common:
                    key = c_items[item][1]
                    if key:
                        slices.setdefault(key, []).append(item)
            for sl, items in slices.items():
                pairs = [(c_items[k][0], i_items[k][0]) for k in items]
                if spec_e.gate:
                    results.append(judge(spec_e.name, split, sl, pairs, ev_cfg.gate))
                else:
                    info.append({"evaluator": spec_e.name, "split": split, "slice": sl, "n": len(pairs),
                                 "candidate": mean([c for c, _ in pairs]), "incumbent": mean([i for _, i in pairs])})

    overall = [r for r in results if r.slice == ALL]
    if results and all(r.passed for r in results):
        decision = "pass"
    elif overall and all(r.passed for r in overall):
        decision = "partial"
    else:
        decision = "fail"
    ev_meta = [{"name": s.name, "kind": s.kind, "version": getattr(e, "version", "1"), "gate": str(s.gate)}
               for s, e in evaluators]
    return Report(model, decision, candidate.describe(), incumbent.describe(), results, info, ev_meta, notes, provenance)


def _identical_share(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    common = [k for k in a if k in b]
    if len(common) < 10:
        return None
    return sum(json.dumps(a[k], sort_keys=True) == json.dumps(b[k], sort_keys=True) for k in common) / len(common)


def _item_means(observations: list[Observation]) -> dict[str, tuple[float, str]]:
    """item -> (mean score over trials, slice key)."""
    acc: dict[str, list[float]] = defaultdict(list)
    key: dict[str, str] = {}
    for o in observations:
        acc[o.item_id].append(o.score.value)
        key[o.item_id] = ",".join(f"{k}={v}" for k, v in sorted(o.slice.items()))
    return {k: (mean(v), key[k]) for k, v in acc.items()}


def report_dir(cfg: Config, model: str) -> Path:
    """Reports go to the customer's storage when it is a local path; otherwise next to forge.yaml."""
    uri = cfg.platform.spec.storage.uri if cfg.platform else ""
    if uri.startswith("file://"):
        base = Path(uri[len("file://"):])
    elif uri and "://" not in uri:
        base = cfg.resolve(uri)
    else:
        base = cfg.base_dir / "forge-reports"
    return base / "reports" / model


def save(report: Report, cfg: Config) -> Path:
    d = report_dir(cfg, report.model)
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(report.created_at))
    path = d / f"eval-{stamp}.json"
    path.write_text(json.dumps(report.as_dict(), indent=2, default=str))
    path.with_suffix(".md").write_text(report.markdown() + "\n")
    return path
