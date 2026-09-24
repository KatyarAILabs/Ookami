import json
import sys
from pathlib import Path

import pytest

from ookami.cli import main
from ookami.config import load
from ookami.data import snapshot
from ookami.interfaces import JobState, JobStatus
from ookami.local.runtime import read_state
from ookami.registry import Registry
from ookami.train.backends import argv_for
from ookami.train.jobs import LocalJobRunner, _recover
from ookami.train.planner import plan

HERE = Path(__file__).parent
PY = sys.executable


def config(write, n=1200, min_examples=100, extra_train=""):
    rows = [{"id": f"r{i}", "prompt": f"question {i}", "label": "ok"} for i in range(n)]
    write("data.jsonl", "\n".join(json.dumps(r) for r in rows))
    return write("f.yaml", f"""
apiVersion: ookami.dev/v1alpha1
kind: Platform
metadata: {{ name: t }}
spec:
  storage: {{ uri: ./store }}
  gateway: {{ mode: none }}
---
apiVersion: ookami.dev/v1alpha1
kind: Model
metadata: {{ name: m }}
spec:
  base: qwen3.5-4b
  serve: {{ engine: command, command: '{PY} {HERE / "fake_server.py"} {{port}} {{name}} {{adapter}}' }}
  data: {{ source: {{ jsonl: data.jsonl }}, minExamples: {min_examples} }}
  train: {{ engine: command, command: '{PY} {HERE / "fake_trainer.py"} {{job}}' {extra_train} }}
  eval:
    splits: {{ heldOut: 0.2, audit: 0.1 }}
    evaluators: [{{ labels: {{ column: label }} }}]
    gate: {{ minItems: 20, resamples: 1000 }}
""")


def test_snapshot_keeps_eval_rows_out(write):
    cfg = load(config(write))
    snap = snapshot(cfg, "m")
    trained = {json.loads(line)["messages"][0]["content"]
               for f in ("train.jsonl", "valid.jsonl") for line in (snap.dir / f).read_text().splitlines()}
    assert snap.held_out > 0 and snap.audit > 0
    assert len(trained) == snap.train + snap.valid == 1200 - snap.held_out - snap.audit
    row = json.loads((snap.dir / "train.jsonl").read_text().splitlines()[0])
    assert row["messages"][-1] == {"role": "assistant", "content": "ok"}
    assert snapshot(cfg, "m").hash == snap.hash


def test_snapshot_refuses_when_not_ready(write):
    with pytest.raises(ValueError, match="not ready"):
        snapshot(load(config(write, n=50, min_examples=100)), "m")


def test_planner_fits_memory_and_honours_overrides(write):
    spec = load(config(write)).models["m"].spec
    small = plan(spec, "trl", 1000, memory_gb=12)
    assert small.quantize and small.iters == 1000
    big = plan(spec, "trl", 1000, memory_gb=80)
    assert not big.quantize and not big.grad_checkpoint and big.iters == 500
    spec.train.overrides = {"lora.rank": 8, "iters": 42}
    o = plan(spec, "mlx", 1000, memory_gb=27, weights="mlx-community/x-4bit")
    assert (o.rank, o.alpha, o.iters) == (8, 16, 42) and "pre-quantized" in o.notes[0]


def test_mlx_argv_writes_config_and_resumes(tmp_path, monkeypatch):
    from ookami.train import backends
    monkeypatch.setattr(backends.shutil, "which", lambda exe: f"/bin/{exe}")
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    p = plan(load_spec_stub(), "mlx", 100, memory_gb=27)
    (tmp_path / "job.json").write_text(json.dumps({"weights": "w", "dataset_dir": "d", "adapter_dir": str(adapter),
                                                   "plan": p.as_dict()}))
    assert argv_for("mlx", tmp_path) == ["/bin/mlx_lm.lora", "-c", str(tmp_path / "mlx.yaml")]
    conf = (tmp_path / "mlx.yaml").read_text()
    assert "mask_prompt: true" in conf and "resume_adapter_file" not in conf
    (adapter / "adapters.safetensors").write_bytes(b"x")
    argv_for("mlx", tmp_path)
    assert "resume_adapter_file" in (tmp_path / "mlx.yaml").read_text()


def load_spec_stub():
    from ookami.config import ModelSpec
    return ModelSpec.model_validate({"base": "qwen3.5-4b"})


def test_registry_promotion_rules(tmp_path):
    reg = Registry(tmp_path / "r.db")
    a = reg.create("m", "b", "w")
    b = reg.create("m", "b", "w")
    assert (a.version, b.version) == (1, 2)
    reg.update("m", 1, status="passed")
    reg.update("m", 2, status="rejected")
    with pytest.raises(PermissionError):
        reg.promote("m", 2)
    with pytest.raises(ValueError, match="reason"):
        reg.promote("m", 2, force=True)
    reg.promote("m", 1)
    assert reg.live("m").version == 1
    reg.promote("m", 2, force=True, reason="incident rollback test")
    assert reg.live("m").version == 2 and reg.get("m", 1).status == "retired"
    assert any(e["action"] == "promoted" and "incident" in e["detail"] for e in reg.events("m"))


def test_train_gate_promote_serve(write, monkeypatch, capsys):
    f = config(write)
    monkeypatch.setenv("FAKE_ANSWER", "ok")
    assert main(["train", "m", "-f", str(f)]) == 0            # as good as the base: passes
    out = capsys.readouterr().out
    assert "m:v1: passed" in out and "held out" in out
    monkeypatch.setenv("FAKE_ANSWER", "wrong")
    assert main(["train", "m", "-f", str(f)]) == 3            # worse than the base: rejected
    assert "m:v2: rejected" in capsys.readouterr().out
    monkeypatch.setenv("FAKE_TRAIN_FAIL", "1")
    assert main(["train", "m", "-f", str(f)]) == 1            # trainer crash: failed, worker survives
    monkeypatch.delenv("FAKE_TRAIN_FAIL")

    assert main(["models", "m", "-f", str(f)]) == 0
    listing = capsys.readouterr().out
    assert "m:v1" in listing and "passed" in listing and "rejected" in listing and "failed" in listing
    assert main(["promote", "m", "2", "-f", str(f)]) == 3     # the gate said no
    assert main(["promote", "m", "-f", str(f)]) == 0          # newest passing version
    assert "m:v1 is live" in capsys.readouterr().out
    try:
        assert main(["up", "-f", str(f), "--timeout", "20"]) == 0
        engine = read_state(load(f))[0]
        assert any("models/m/v1/adapter" in a for a in engine.argv)
    finally:
        main(["down", "-f", str(f)])
    report = json.loads(Path(load(f).base_dir, "store", "reports", "m").glob("*.json").__next__().read_text())
    assert report["provenance"]["incumbent"] == "base:qwen3.5-4b"


def test_interrupted_job_is_requeued(write):
    cfg = load(config(write))
    runner = LocalJobRunner(cfg)
    (runner.root / "j1").mkdir(parents=True)
    runner._write(JobStatus("j1", JobState.RUNNING, message="training"), pid=999999, queued_at=1)
    _recover(runner)
    st = runner.status("j1")
    assert st.state is JobState.QUEUED and "resumes" in st.message
