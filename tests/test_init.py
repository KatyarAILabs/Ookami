from ookami import init as init_mod
from ookami.cli import main
from ookami.config import load


def test_init_writes_valid_config_for_each_machine(tmp_path, monkeypatch):
    for kind, engine in [("apple", "mlx"), ("nvidia", "vllm"), ("cpu", "llama.cpp")]:
        m = init_mod.Machine(kind, engine, True, "")
        f = tmp_path / f"{kind}.yaml"
        init_mod.write(f, m, api_model=True)
        cfg = load(f)
        assert cfg.ok, (kind, cfg.issues)
        assert set(cfg.models) == {"local", "gpt"}
        assert cfg.platform.spec.gateway.auth == "keys"


def test_init_cli_refuses_to_overwrite(tmp_path, capsys):
    f = tmp_path / "ookami.yaml"
    assert main(["init", "-f", str(f)]) == 0
    assert "ookami up" in capsys.readouterr().out
    assert main(["init", "-f", str(f)]) == 1
    assert main(["init", "-f", str(f), "--force"]) == 0
