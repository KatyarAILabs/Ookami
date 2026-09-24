import asyncio
import os
import stat
import time

import pytest

from ookami.gateway.keys import KeyStore, hash_key, month_start, usage_report

litellm_hooks = pytest.importorskip("ookami.gateway.litellm_hooks", reason="needs ookami[gateway]")


@pytest.fixture
def hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("OOKAMI_STORAGE", str(tmp_path))
    monkeypatch.setattr(litellm_hooks, "_store", None)
    litellm_hooks._windows.clear()
    return litellm_hooks


def status(fn, *a):
    try:
        fn(*a)
    except Exception as e:  # ProxyException
        return int(e.code)
    return 200


def test_master_key_is_generated_private_and_stable(tmp_path):
    ks = KeyStore(tmp_path)
    m = ks.master_key()
    assert m.startswith("ook-master-") and ks.master_key() == m
    assert stat.S_IMODE(os.stat(tmp_path / "secrets" / "master_key").st_mode) == 0o600


def test_keys_are_stored_hashed_and_revocable(tmp_path):
    ks = KeyStore(tmp_path)
    key = ks.create("alice", "research", budget_usd=5, rpm=10)
    raw = (tmp_path / "registry.db").read_bytes()
    assert key.encode() not in raw and hash_key(key).encode() in raw
    assert ks.get(key).team == "research"
    assert ks.revoke("alice") == 1 and ks.get(key).revoked_at is not None


def test_auth_decisions(hooks, tmp_path):
    ks = hooks.store()
    master = ks.master_key()
    k = ks.create("alice", "research", rpm=2)
    broke = ks.create("bob", "ops", budget_usd=1.0)
    ks.record(hash_key(broke), "ops", "m", 10, 10, 1.5, 5, True)
    assert status(hooks.check, "") == 401
    assert status(hooks.check, "ook-nope") == 401
    assert hooks.check(f"Bearer {master}").team_id == "admin"
    now = time.time()
    assert [status(hooks.check, k, now), status(hooks.check, k, now + 1), status(hooks.check, k, now + 2)] == [200, 200, 429]
    assert status(hooks.check, k, now + 61) == 200          # the window slides
    assert status(hooks.check, broke) == 429                # over budget
    ks.revoke("alice")
    assert status(hooks.check, k, now + 200) == 401


def test_health_is_public(hooks):
    class Req:
        class url:
            path = "/health/liveliness"
    assert asyncio.run(hooks.user_api_key_auth(Req(), "")) is not None


def test_usage_splits_hardware_cost_by_tokens(tmp_path):
    ks = KeyStore(tmp_path)
    a, b = ks.create("a", "t1"), ks.create("b", "t2")
    t0 = time.time()
    ks.db.execute("INSERT INTO engine_runs VALUES (?,?,?,?)", ("local", t0 - 3600, t0, 2.0))   # $2 of GPU time
    ks.record(hash_key(a), "t1", "local", 300, 0, 0.0, 10, True)
    ks.record(hash_key(b), "t2", "local", 100, 0, 0.0, 10, True)
    ks.record(hash_key(b), "t2", "gpt-api", 10, 10, 0.25, 10, True)
    rows, per_model = usage_report(ks, t0 - 7200, t0 + 10)
    by = {r.who: r for r in rows}
    assert by["a (t1)"].self_hosted_cost == pytest.approx(1.5)
    assert by["b (t2)"].self_hosted_cost == pytest.approx(0.5) and by["b (t2)"].api_cost == pytest.approx(0.25)
    assert per_model["local"]["cost_per_million_tokens"] == pytest.approx(2.0 / 400 * 1e6)
    assert ks.spend_this_month(hash_key(b)) == pytest.approx(0.25)
    assert month_start() <= time.time()


def test_gateway_config_has_auth_usage_and_providers(write):
    from ookami.config import load
    from ookami.local.runtime import Service, gateway_config
    cfg = load(write("f.yaml", """
apiVersion: ookami.dev/v1alpha1
kind: Platform
metadata: { name: t }
spec: { storage: { uri: ./s } }
---
apiVersion: ookami.dev/v1alpha1
kind: Model
metadata: { name: gpt }
spec: { provider: { name: openai, model: gpt-5-mini, apiKey: "${secret:OPENAI_API_KEY}" } }
"""))
    assert cfg.ok, cfg.issues
    eng = Service("local", "engine", 1, 8100, "http://127.0.0.1:8100/v1", "", "", [], "default_model")
    conf = gateway_config([eng], providers=[cfg.models["gpt"]], master_key="ook-master-x", usage=True)
    names = {m["model_name"]: m["litellm_params"] for m in conf["model_list"]}
    assert names["gpt"] == {"model": "openai/gpt-5-mini", "api_key": "os.environ/OPENAI_API_KEY"}
    assert conf["general_settings"]["custom_auth"].endswith("user_api_key_auth")
    assert conf["litellm_settings"]["callbacks"] == ["ookami.gateway.litellm_hooks.usage_logger"]


def test_langfuse_wiring(write, monkeypatch):
    from ookami.config import load
    from ookami.local import runtime
    cfg = load(write("f.yaml", """
apiVersion: ookami.dev/v1alpha1
kind: Platform
metadata: { name: t }
spec:
  storage: { uri: ./s }
  observability:
    langfuse: { host: "http://lf:3000", publicKey: "${secret:LF_PK}", secretKey: "${secret:LF_SK}" }
"""))
    assert cfg.ok, cfg.issues
    conf = runtime.gateway_config([], langfuse=True)
    assert "langfuse_otel" in conf["litellm_settings"]["callbacks"]
    monkeypatch.setenv("LF_PK", "pk-1")
    monkeypatch.delenv("LF_SK", raising=False)
    try:
        import opentelemetry.exporter.otlp.proto.http  # noqa: F401
    except ImportError:
        pytest.skip("needs ookami[observability]")
    with pytest.raises(runtime.UpError, match="LF_SK"):
        runtime.langfuse_env(cfg.platform.spec)
    monkeypatch.setenv("LF_SK", "sk-1")
    assert runtime.langfuse_env(cfg.platform.spec) == {"LANGFUSE_HOST": "http://lf:3000", "LANGFUSE_PUBLIC_KEY": "pk-1",
                                                       "LANGFUSE_SECRET_KEY": "sk-1"}
