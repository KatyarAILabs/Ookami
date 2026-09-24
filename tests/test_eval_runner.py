import json

from conftest import model_doc, score_rows

from ookami.cli import main
from ookami.config import load
from ookami.eval_runner import run_eval, save
from ookami.evaluators.base import Target


def rows_fixture(write, n=1200):  # audit slices need >= minItems rows
    rows = [{"id": f"r{i}", "prompt": f"q{i}", "label": "yes" if i % 2 else "no", "route": f"route{i % 2}"}
            for i in range(n)]
    write("data.jsonl", "\n".join(json.dumps(r) for r in rows))
    good = [{"id": r["id"], "output": {"role": "assistant", "content": r["label"]}} for r in rows]
    bad = [{"id": r["id"], "output": {"role": "assistant", "content": "yes" if r["route"] == "route0" else r["label"]}}
           for r in rows]
    write("inc.jsonl", "\n".join(json.dumps(r) for r in good))
    write("bad.jsonl", "\n".join(json.dumps(r) for r in bad))


EV = """
splits: { heldOut: 0.2, audit: 0.1, stratifyBy: [route] }
evaluators:
  - labels: { column: label }
  - ookami/structural
gate: { minItems: 20, resamples: 1000 }
"""


def test_row_eval_pass_and_per_slice_block(write, tmp_path):
    rows_fixture(write)
    cfg = load(write("f.yaml", model_doc(EV)))
    assert cfg.ok, cfg.issues
    inc = Target.parse(f"results:{tmp_path / 'inc.jsonl'}", "incumbent")
    same = run_eval(cfg, "m", Target.parse(f"results:{tmp_path / 'inc.jsonl'}", "candidate"), inc)
    assert same.decision == "pass"
    assert any("identical outputs" in n for n in same.notes)
    assert {r.split for r in same.results} == {"heldOut", "audit"}
    worse = run_eval(cfg, "m", Target.parse(f"results:{tmp_path / 'bad.jsonl'}", "candidate"), inc)
    assert worse.decision == "fail"
    blocked = {r.slice for r in worse.results if not r.passed and r.evaluator == "labels:label"}
    assert "route=route0" in blocked and "route=route1" not in blocked
    path = save(worse, cfg)
    assert path.exists() and path.parent == tmp_path / "store" / "reports" / "m"
    assert "`structural` checks shape only" in path.with_suffix(".md").read_text()


def test_non_gating_evaluator_is_reported_only(write, tmp_path):
    rows_fixture(write)
    ev = EV.replace("  - ookami/structural", "  - ookami/structural\n  - labels: { column: route }\n    gate: false")
    cfg = load(write("f.yaml", model_doc(ev)))
    t = Target.parse(f"results:{tmp_path / 'inc.jsonl'}", "candidate")
    rep = run_eval(cfg, "m", t, Target.parse(f"results:{tmp_path / 'inc.jsonl'}", "incumbent"))
    assert rep.info and all(r.evaluator != "labels:route" for r in rep.results)


def test_command_eval_and_cli_exit_codes(write, tmp_path):
    ev = """
    evaluators:
      - command: { run: "true" }
        name: bench
    gate: { margin: -0.05, minItems: 20, resamples: 1000 }
    """
    f = write("t.yaml", model_doc(ev))
    strong = {str(i): [1.0, 1.0] if i % 3 else [0.0, 1.0] for i in range(40)}
    weak = {str(i): [0.0, 0.0] if i % 2 else [1.0, 0.0] for i in range(40)}
    inc = score_rows(tmp_path / "inc.jsonl", strong, errors=2)
    bad = score_rows(tmp_path / "bad.jsonl", weak)
    assert main(["eval", "-f", str(f), "--candidate", f"results:{inc}", "--incumbent", f"results:{inc}"]) == 0
    assert main(["eval", "-f", str(f), "--candidate", f"results:{bad}", "--incumbent", f"results:{inc}"]) == 3
    assert main(["eval", "-f", str(f), "--model", "nope", "--candidate", f"results:{bad}",
                 "--incumbent", f"results:{inc}"]) == 1


def test_validate_cli(write):
    assert main(["validate", "-f", str(write("ok.yaml", model_doc(EV)))]) == 0
    assert main(["validate", "-f", str(write("bad.yaml", model_doc(EV).replace("m }", "BAD }")))]) == 1


def test_schema_cli(capsys):
    assert main(["schema"]) == 0
    assert "Platform" in capsys.readouterr().out
