from pathlib import Path

from ookami.evaluators import harness
from ookami.evaluators.base import Target
from ookami.evaluators.harness import InspectEvaluator, LmEvalEvaluator, parse_inspect_log, parse_lm_eval_samples

FIX = Path(__file__).with_name("fixtures")


def test_inspect_log_maps_letter_scores_and_drops_errors():
    obs, notes = parse_inspect_log(FIX / "inspect_log.json")
    assert len(obs) == 4
    assert [o.score.value for o in obs][:2] == [1.0, 0.0]          # "C" -> 1, "I" -> 0
    assert obs[0].slice == {"task": "arithmetic"}
    assert any("errored" in n for n in notes)


def test_lm_eval_samples_use_one_filter_and_the_metric():
    obs, notes = parse_lm_eval_samples(FIX / "lmeval")
    assert obs and all(o.item_id.startswith("gsm8k:") for o in obs)
    assert len({o.item_id for o in obs}) == len(obs)                # one row per doc, not one per filter
    assert "strict-match" in notes[0] or "flexible-extract" in notes[0]
    flex, _ = parse_lm_eval_samples(FIX / "lmeval", filter_="flexible-extract")
    assert len(flex) == len(obs)


def test_results_targets_replay(tmp_path):
    ins = InspectEvaluator("a", tmp_path, task="t.py")
    assert len(ins.run(Target("c", "results", path=FIX / "inspect_log.json"))[0]) == 4
    lm = LmEvalEvaluator("g", tmp_path, tasks=["gsm8k"])
    assert lm.run(Target("c", "results", path=FIX / "lmeval"))[0]


def test_harness_commands_are_isolated_or_overridden(monkeypatch):
    monkeypatch.setattr(harness.shutil, "which", lambda exe: "/bin/uvx" if exe == "uvx" else None)
    assert harness._exe("inspect") == ["uvx", "--quiet", "--from", "inspect-ai", "--with", "openai>=3.1", "inspect"]
    assert harness._exe("lm_eval")[:4] == ["uvx", "--quiet", "--from", "lm-eval[api]"]
    monkeypatch.setenv("OOKAMI_INSPECT_CMD", "/opt/inspect/bin/inspect")
    assert harness._exe("inspect") == ["/opt/inspect/bin/inspect"]
