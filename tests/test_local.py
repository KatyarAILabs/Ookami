import sys
import urllib.request
from pathlib import Path

import pytest

from forge.cli import main
from forge.config import load
from forge.local import engines
from forge.local.engines import EngineError, launch
from forge.local.runtime import UpError, alive, down, read_state, status, up

FAKE = Path(__file__).with_name("fake_server.py")


def doc(write, model_extra: str, platform_extra: str = ""):
    return load(write("f.yaml", f"""
apiVersion: forge.dev/v1alpha1
kind: Platform
metadata: {{ name: t }}
spec:
  storage: {{ uri: ./store }}
{platform_extra}
---
apiVersion: forge.dev/v1alpha1
kind: Model
metadata: {{ name: m }}
spec:
{model_extra}
"""))


def test_engine_commands(write, monkeypatch):
    monkeypatch.setattr(engines.shutil, "which", lambda exe: f"/bin/{exe}")
    cfg = doc(write, "  base: qwen3-4b-instruct-2507\n  serve: { engine: vllm, args: [--max-model-len, '8192'] }")
    v = launch(cfg.models["m"], 8100)
    assert v.argv[:3] == ["/bin/vllm", "serve", "Qwen/Qwen3-4B-Instruct-2507"]
    assert "--served-model-name" in v.argv and v.argv[-2:] == ["--max-model-len", "8192"] and v.model_id == "m"
    cfg = doc(write, "  base: qwen3-4b-instruct-2507\n  serve: { engine: mlx }")
    m = launch(cfg.models["m"], 8101)
    assert m.weights == "mlx-community/Qwen3-4B-Instruct-2507-4bit" and m.model_id == m.weights
    cfg = doc(write, "  base: my/model\n  licence: MIT\n  weights: ./w\n"
                     "  serve: { engine: command, command: 'srv --w {weights} --p {port}', modelId: x }")
    c = launch(cfg.models["m"], 9000)
    assert c.argv == ["srv", "--w", "./w", "--p", "9000"] and c.model_id == "x"


def test_weights_and_binaries_errors(write, monkeypatch):
    monkeypatch.setattr(engines.shutil, "which", lambda exe: None)
    with pytest.raises(EngineError, match="pip install vllm"):
        launch(doc(write, "  base: gpt-oss-20b\n  serve: { engine: vllm }").models["m"], 1)
    with pytest.raises(EngineError, match="no weights"):
        launch(doc(write, "  base: gemma-4-e4b\n  serve: { engine: vllm }").models["m"], 1)


def test_command_engine_needs_command(write):
    cfg = doc(write, "  base: gpt-oss-20b\n  serve: { engine: command }")
    assert not cfg.ok and any("serve.command" in str(i) for i in cfg.issues)


def fake_model(port: int) -> str:
    return (f"  base: gpt-oss-20b\n  serve: {{ engine: command, port: {port}, "
            f"command: '{sys.executable} {FAKE} {{port}} {{name}}' }}")


def free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_up_status_down_with_managed_gateway(write):
    gw = free_port()
    cfg = doc(write, fake_model(free_port()),
              f"  gateway: {{ port: {gw}, command: '{sys.executable} {FAKE} {{port}}' }}")
    assert cfg.ok, cfg.issues
    services = up(cfg, timeout=20, log=lambda *_: None)
    try:
        assert [s.kind for s in services] == ["engine", "gateway"]
        assert all(state == "ready" for _, state in status(cfg))
        conf = (Path(cfg.base_dir) / "store" / "run" / "gateway.yaml").read_text()
        assert "model_name: m" in conf and services[0].url in conf
        with urllib.request.urlopen(f"{services[1].url}/models", timeout=5) as r:
            assert r.status == 200
        with pytest.raises(UpError, match="already up"):
            up(cfg, timeout=5, log=lambda *_: None)
    finally:
        assert down(cfg, log=lambda *_: None) == 2
    assert read_state(cfg) == [] and not any(alive(s.pid) for s in services)


def test_failed_start_cleans_up(write):
    cfg = doc(write, f"  base: gpt-oss-20b\n  serve: {{ engine: command, command: '{sys.executable} -c \"import sys; sys.exit(4)\"' }}",
              "  gateway: { mode: none }")
    with pytest.raises(UpError, match="exited while starting"):
        up(cfg, timeout=10, log=lambda *_: None)
    assert read_state(cfg) == []


def test_non_local_backend_and_storage_rejected(write):
    cfg = doc(write, fake_model(free_port()), "  gateway: { mode: none }")
    cfg.platform.spec.storage.uri = "s3://bucket"
    with pytest.raises(UpError, match="local storage"):
        up(cfg, timeout=1, log=lambda *_: None)


def test_cli_up_status_down(write, capsys):
    f = write("c.yaml", f"""
apiVersion: forge.dev/v1alpha1
kind: Platform
metadata: {{ name: t }}
spec:
  storage: {{ uri: ./store }}
  gateway: {{ mode: none }}
---
apiVersion: forge.dev/v1alpha1
kind: Model
metadata: {{ name: m }}
spec:
{fake_model(free_port())}
""")
    try:
        assert main(["up", "-f", str(f), "--timeout", "20"]) == 0
        assert main(["status", "-f", str(f)]) == 0
        assert "ready" in capsys.readouterr().out
        assert main(["logs", "m", "-f", str(f)]) == 0
    finally:
        assert main(["down", "-f", str(f)]) == 0
    assert main(["status", "-f", str(f)]) == 0 and "nothing running" in capsys.readouterr().out
