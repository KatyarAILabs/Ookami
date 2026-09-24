import json
import urllib.error
import urllib.request

import pytest

from ookami.config import load
from ookami.console.server import serve_in_thread
from ookami.gateway.keys import KeyStore, hash_key
from ookami.registry import Registry

CFG = """
apiVersion: ookami.dev/v1alpha1
kind: Platform
metadata: { name: t }
spec: { storage: { uri: ./store }, gateway: { mode: none } }
---
apiVersion: ookami.dev/v1alpha1
kind: Model
metadata: { name: m }
spec: { base: qwen3.5-4b }
---
apiVersion: ookami.dev/v1alpha1
kind: Model
metadata: { name: gpt }
spec: { provider: { name: openai, model: gpt-5-mini } }
"""


@pytest.fixture
def console(write, tmp_path):
    f = write("ookami.yaml", CFG)
    store = tmp_path / "store"
    ks = KeyStore(store)
    master = ks.master_key()
    k = ks.create("alice", "research", budget_usd=2)
    ks.record(hash_key(k), "research", "m", 100, 50, 0.5, 20, True)
    reg = Registry(store / "registry.db")
    v1 = reg.create("m", "qwen3.5-4b", "Qwen/Qwen3.5-4B")
    reg.update("m", v1.version, status="rejected", decision="fail")
    v2 = reg.create("m", "qwen3.5-4b", "Qwen/Qwen3.5-4B")
    rep = store / "reports" / "m" / "eval-1.json"
    rep.parent.mkdir(parents=True)
    rep.write_text(json.dumps({"model": "m", "decision": "pass"}))
    rep.with_suffix(".md").write_text("# ookami eval: m: PASS\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    reg.update("m", v2.version, status="passed", decision="pass", report=str(rep))
    httpd, port = serve_in_thread(f)
    yield f"http://127.0.0.1:{port}", master, store
    httpd.shutdown()


def call(base, path, key=None, body=None):
    req = urllib.request.Request(base + path, json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_static_shell_and_security_headers(console):
    base, _, _ = console
    with urllib.request.urlopen(base + "/") as r:
        html = r.read().decode()
        assert "Ookami" in html and "app.js" in html
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    with urllib.request.urlopen(base + "/../../etc/passwd") as r:   # never escapes the static dir
        assert "Ookami" in r.read().decode()


def test_api_requires_the_master_key(console):
    base, master, _ = console
    assert call(base, "/api/overview")[0] == 401
    assert call(base, "/api/overview", "ook-wrong")[0] == 401
    status, ov = call(base, "/api/overview", master)
    assert status == 200 and {m["name"] for m in ov["models"]} == {"m", "gpt"}
    assert ov["stats"]["active_keys"] == 1 and ov["stats"]["requests_24h"] == 1


def test_versions_report_and_promotion_rules(console):
    base, master, store = console
    _, versions = call(base, "/api/models/m/versions", master)
    assert [v["status"] for v in versions] == ["passed", "rejected"]
    s, rep = call(base, "/api/report?path=" + urllib.request.quote(versions[0]["report"]), master)
    assert s == 200 and "PASS" in rep["markdown"]
    assert call(base, "/api/report?path=/etc/passwd", master)[0] == 404
    assert call(base, "/api/models/m/promote", master, {"version": 1})[0] == 409          # failed its gate
    assert call(base, "/api/models/m/promote", master, {"version": 1, "force": True})[0] == 400   # needs a reason
    s, r = call(base, "/api/models/m/promote", master, {"version": 2})
    assert s == 200 and r["live"] == "m:v2"
    _, events = call(base, "/api/models/m/events", master)
    assert events[0]["action"] == "promoted"


def test_keys_usage_and_errors(console):
    base, master, _ = console
    s, created = call(base, "/api/keys", master, {"name": "bob", "team": "ops", "budget_usd": "5", "rpm": "60"})
    assert s == 200 and created["key"].startswith("ook-")
    _, keys = call(base, "/api/keys", master)
    assert {k["name"] for k in keys} == {"alice", "bob"} and all("key_hash" not in k for k in keys)
    assert call(base, "/api/keys/bob/revoke", master, {})[0] == 200
    assert call(base, "/api/keys/nobody/revoke", master, {})[0] == 404
    s, u = call(base, "/api/usage?since=7d&by=team", master)
    assert s == 200 and u["rows"][0]["who"] == "research" and u["daily"]
    assert call(base, "/api/usage?since=bad", master)[0] == 400
    assert call(base, "/api/chat", master, {"model": "m", "messages": []})[0] == 400   # no managed gateway here
    assert call(base, "/api/nope", master)[0] == 404
