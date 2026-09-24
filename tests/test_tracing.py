import json
import os
import socket
import sys
from pathlib import Path

from ookami.config import load
from ookami.data import load_source, snapshot
from ookami.local.runtime import down, up

HERE = Path(__file__).parent
PY = sys.executable


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def chat_lake(d: Path, n: int = 400) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"messages": [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": "a"}],
             "metadata": {"episode_id": f"ep{i}", "step_idx": 0, "reward": 1.0}} for i in range(n)]
    (d / "chat.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    return d


def platform(extra: str) -> str:
    return f"""
apiVersion: ookami.dev/v1alpha1
kind: Platform
metadata: {{ name: t }}
spec:
  storage: {{ uri: ./store }}
{extra}
"""


def test_traces_source_exports_chat_from_the_lake(write, tmp_path):
    chat_lake(tmp_path / "lake")
    f = write("f.yaml", platform(f"  tracing: {{ mode: external, lake: ./lake, bin: '{HERE / 'fake_cc.py'}' }}") + """---
apiVersion: ookami.dev/v1alpha1
kind: Model
metadata: { name: m }
spec:
  base: qwen3.5-4b
  data: { source: { traces: { minReward: 1, asOf: '2026-09-25T00:00:00Z' } }, minExamples: 100 }
  train: { recipe: sft }
  eval: { evaluators: [ { labels: { column: label } } ] }
""")
    cfg = load(f)
    assert cfg.ok, cfg.issues
    examples, _ = load_source(cfg.models["m"].spec.data.source, cfg)
    assert len(examples) == 400
    ex = examples[0]
    assert ex.id == "ep0:0" and ex.meta["reward"] == 1.0 and ex.meta["reference"]["content"] == "a"
    argv = json.loads((tmp_path / "lake" / "argv.json").read_text())
    assert ["-format", "chat"] == argv[argv.index("-format"):argv.index("-format") + 2]
    assert "-require-final=false" in argv and "1.0" in argv and "2026-09-25T00:00:00Z" in argv
    snap = snapshot(cfg, "m")
    assert snap.train + snap.valid + snap.held_out + snap.audit == 400


def test_up_runs_the_collector_and_wires_the_gateway(write, tmp_path):
    hp, gp = free_port(), free_port()
    write("collector.yaml", f"health_port: {hp}\n")
    gateway_cmd = f"sh -c 'echo $GENERIC_LOGGER_ENDPOINT > {{config}}.env; exec {PY} {HERE / 'fake_server.py'} {{port}}'"
    f = write("f.yaml", platform(f"""  components: {{ tracing: {{ enabled: true }} }}
  tracing:
    config: ./collector.yaml
    lake: ./lake
    bin: '{HERE / 'fake_cc.py'}'
    healthUrl: http://127.0.0.1:{hp}/healthz
    webhook: http://127.0.0.1:4320/v1/hooks/litellm
  gateway: {{ port: {gp}, command: "{gateway_cmd}" }}""") + f"""---
apiVersion: ookami.dev/v1alpha1
kind: Model
metadata: {{ name: m }}
spec:
  base: gpt-oss-20b
  serve: {{ engine: command, port: {free_port()}, command: '{PY} {HERE / "fake_server.py"} {{port}} {{name}}' }}
""")
    cfg = load(f)
    assert cfg.ok, cfg.issues
    services = up(cfg, timeout=20, log=lambda *_: None)
    try:
        assert [s.kind for s in services] == ["tracing", "engine", "gateway"]
        run = tmp_path / "store" / "run"
        assert "generic_api" in (run / "gateway.yaml").read_text()
        assert (run / "gateway.yaml.env").read_text().strip() == "http://127.0.0.1:4320/v1/hooks/litellm"
    finally:
        assert down(cfg, log=lambda *_: None) == 3


def test_token_env_must_be_set(write, tmp_path, monkeypatch):
    from ookami.local.runtime import UpError, tracing_env
    cfg = load(write("f.yaml", platform("  tracing: { mode: external, lake: ./l, tokenEnv: CC_TOKEN_X }")))
    monkeypatch.delenv("CC_TOKEN_X", raising=False)
    import pytest
    with pytest.raises(UpError, match="CC_TOKEN_X"):
        tracing_env(cfg.platform.spec)
    monkeypatch.setenv("CC_TOKEN_X", "s3cret")
    assert tracing_env(cfg.platform.spec)["GENERIC_LOGGER_HEADERS"] == "Authorization=Bearer s3cret"
