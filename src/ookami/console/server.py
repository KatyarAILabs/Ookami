"""The Ookami console: a small JSON API over the registry, jobs, keys and usage, plus a static single-page app.

Standard library only (no web framework), no external assets, so it works air-gapped. Every action goes through
the same code as the CLI: promotions still need a passing gate (or a recorded --force reason), keys are shown once.
Sign-in is the gateway master key, sent as `Authorization: Bearer <key>` on every /api call.
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from ..config import Config, load
from ..gateway.keys import KeyStore, usage_report
from ..local import runtime
from ..registry import open_registry

STATIC = Path(__file__).with_name("static")
MAX_BODY = 1 << 20


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Console:
    """Holds the config path; reloads the config on each request so edits to ookami.yaml show up."""

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)

    def cfg(self) -> Config:
        cfg = load(self.config_path)
        if cfg.platform is None:
            raise ApiError(500, "ookami.yaml has no Platform document")
        return cfg

    def storage(self, cfg: Config) -> Path:
        return runtime.storage_dir(cfg)

    def master_key(self) -> str:
        cfg = self.cfg()
        ks = KeyStore(self.storage(cfg))
        try:
            return ks.master_key()
        finally:
            ks.close()

    # ------------------------------------------------------------ reads
    def overview(self) -> dict:
        cfg = self.cfg()
        services = [{"name": s.name, "kind": s.kind, "state": st, "url": s.url, "pid": s.pid}
                    for s, st in runtime.status(cfg)]
        models = []
        reg = open_registry(self.storage(cfg))
        try:
            for name, doc in cfg.models.items():
                live = reg.live(name)
                versions = reg.list(name)
                models.append({
                    "name": name, "kind": "api" if doc.spec.provider else "self-hosted",
                    "base": doc.spec.base, "provider": f"{doc.spec.provider.name}/{doc.spec.provider.model}"
                    if doc.spec.provider else None,
                    "live": live.tag if live else None, "versions": len(versions),
                    "trainable": doc.spec.train is not None,
                })
        finally:
            reg.close()
        ks = KeyStore(self.storage(cfg))
        try:
            day, _ = usage_report(ks, time.time() - 86400)
            month, _ = usage_report(ks, time.time() - 30 * 86400)
            keys = [k for k in ks.list() if not k.revoked_at]
        finally:
            ks.close()
        gw = cfg.platform.spec.gateway
        return {
            "platform": cfg.platform.metadata.name, "backend": cfg.platform.spec.backend,
            "gateway": {"mode": gw.mode, "auth": gw.auth, "url": f"http://127.0.0.1:{gw.port}/v1" if gw.mode == "managed" else gw.url},
            "services": services, "models": models, "issues": [str(i) for i in cfg.issues],
            "stats": {"requests_24h": sum(u.requests for u in day), "errors_24h": sum(u.errors for u in day),
                      "tokens_24h": sum(u.tokens for u in day), "spend_30d": round(sum(u.total for u in month), 4),
                      "active_keys": len(keys)},
        }

    def versions(self, model: str) -> list[dict]:
        cfg = self.cfg()
        if model not in cfg.models:
            raise ApiError(404, f"no Model {model!r}")
        reg = open_registry(self.storage(cfg))
        try:
            return [{**v.__dict__, "tag": v.tag} for v in reversed(reg.list(model))]
        finally:
            reg.close()

    def events(self, model: str) -> list[dict]:
        cfg = self.cfg()
        reg = open_registry(self.storage(cfg))
        try:
            return list(reversed(reg.events(model)))[:100]
        finally:
            reg.close()

    def report(self, path: str) -> dict:
        cfg = self.cfg()
        p = Path(path).resolve()
        root = (self.storage(cfg) / "reports").resolve()
        if root not in p.parents or p.suffix != ".json" or not p.exists():
            raise ApiError(404, "no such report")
        data = json.loads(p.read_text())
        md = p.with_suffix(".md")
        data["markdown"] = md.read_text() if md.exists() else ""
        return data

    def jobs(self) -> list[dict]:
        from ..train.jobs import LocalJobRunner
        cfg = self.cfg()
        return [{"id": jid, **raw} for jid, raw in reversed(LocalJobRunner(cfg).list())]

    def job_logs(self, job_id: str, lines: int = 200) -> dict:
        from ..train.jobs import LocalJobRunner
        cfg = self.cfg()
        runner = LocalJobRunner(cfg)
        if not (runner.root / job_id / "status.json").exists() or "/" in job_id or job_id.startswith("."):
            raise ApiError(404, "no such job")
        return {"id": job_id, "log": runner.logs(job_id, lines)}

    def keys(self) -> list[dict]:
        cfg = self.cfg()
        ks = KeyStore(self.storage(cfg))
        try:
            return [{"name": k.name, "team": k.team, "prefix": k.prefix, "budget_usd": k.budget_usd, "rpm": k.rpm,
                     "created_at": k.created_at, "revoked": bool(k.revoked_at),
                     "spent_this_month": round(ks.spend_this_month(k.key_hash), 4)} for k in ks.list()]
        finally:
            ks.close()

    def usage(self, since_s: float, by: str) -> dict:
        cfg = self.cfg()
        ks = KeyStore(self.storage(cfg))
        try:
            rows, per_model = usage_report(ks, time.time() - since_s, by=by)
            daily = [dict(r) for r in ks.db.execute(
                "SELECT CAST(ts / 86400 AS INT) * 86400 AS day, COUNT(*) AS requests, "
                "COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS tokens, COALESCE(SUM(cost_usd), 0) AS api_cost "
                "FROM usage WHERE ts >= ? GROUP BY day ORDER BY day", (time.time() - since_s,))]
        finally:
            ks.close()
        return {"rows": [{**u.__dict__, "total": u.total} for u in rows], "models": per_model, "daily": daily}

    # ------------------------------------------------------------ actions
    def create_key(self, body: dict) -> dict:
        name = str(body.get("name", "")).strip()
        if not name:
            raise ApiError(400, "name is required")
        cfg = self.cfg()
        ks = KeyStore(self.storage(cfg))
        try:
            key = ks.create(name, str(body.get("team") or "default"),
                            float(body["budget_usd"]) if body.get("budget_usd") not in (None, "") else None,
                            int(body["rpm"]) if body.get("rpm") not in (None, "") else None)
        finally:
            ks.close()
        return {"key": key, "note": "Shown once. Ookami stores only its hash."}

    def revoke_key(self, name: str) -> dict:
        cfg = self.cfg()
        ks = KeyStore(self.storage(cfg))
        try:
            n = ks.revoke(name)
        finally:
            ks.close()
        if not n:
            raise ApiError(404, f"no active key {name!r}")
        return {"revoked": n}

    def promote(self, model: str, body: dict) -> dict:
        cfg = self.cfg()
        reg = open_registry(self.storage(cfg))
        try:
            try:
                v = reg.promote(model, int(body["version"]), force=bool(body.get("force")), reason=body.get("reason"))
            except PermissionError as e:
                raise ApiError(409, str(e)) from None
            except (KeyError, ValueError) as e:
                raise ApiError(400, str(e)) from None
        finally:
            reg.close()
        restarted = False
        try:
            restarted = runtime.restart_engine(cfg, model, log=lambda *_: None)
        except Exception as e:  # the promotion stands; report the restart problem
            return {"live": v.tag, "restarted": False, "warning": f"engine restart failed: {e}"}
        return {"live": v.tag, "restarted": restarted}

    def train(self, body: dict) -> dict:
        from ..train import submit_training
        cfg = self.cfg()
        try:
            sub = submit_training(cfg, str(body.get("model", "")))
        except (KeyError, ValueError, NotImplementedError) as e:
            raise ApiError(400, str(e)) from None
        return {"job": sub.job_id, "version": sub.version.tag, "plan": sub.plan.as_dict(),
                "rows": {"train": sub.snapshot.train, "valid": sub.snapshot.valid,
                         "held_out": sub.snapshot.held_out, "audit": sub.snapshot.audit}}

    def chat(self, body: dict) -> dict:
        """Playground: call the gateway with the master key, so the browser never needs a model key."""
        cfg = self.cfg()
        gw = cfg.platform.spec.gateway
        if gw.mode != "managed":
            raise ApiError(400, "the playground needs the managed gateway")
        payload = {"model": body.get("model"), "messages": body.get("messages") or [],
                   "max_tokens": int(body.get("max_tokens") or 512), "temperature": float(body.get("temperature") or 0)}
        headers = {"Content-Type": "application/json"}
        if gw.auth == "keys":
            headers["Authorization"] = f"Bearer {self.master_key()}"
        req = urllib.request.Request(f"http://127.0.0.1:{gw.port}/v1/chat/completions",
                                     json.dumps(payload).encode(), headers)
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, e.read().decode(errors="replace")[:500]) from None
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(502, f"gateway unreachable ({e}); is `ookami up` running?") from None
        return {"message": data["choices"][0]["message"], "usage": data.get("usage"),
                "latency_ms": round((time.time() - started) * 1000)}


def make_handler(console: Console) -> type[BaseHTTPRequestHandler]:
    routes: list[tuple[str, str, Callable[..., Any]]] = [
        ("GET", "/api/overview", lambda q, b: console.overview()),
        ("GET", "/api/models/{}/versions", lambda q, b, m: console.versions(m)),
        ("GET", "/api/models/{}/events", lambda q, b, m: console.events(m)),
        ("POST", "/api/models/{}/promote", lambda q, b, m: console.promote(m, b)),
        ("GET", "/api/report", lambda q, b: console.report(q.get("path", [""])[0])),
        ("GET", "/api/jobs", lambda q, b: console.jobs()),
        ("GET", "/api/jobs/{}/logs", lambda q, b, j: console.job_logs(j, int(q.get("lines", ["200"])[0]))),
        ("POST", "/api/train", lambda q, b: console.train(b)),
        ("GET", "/api/keys", lambda q, b: console.keys()),
        ("POST", "/api/keys", lambda q, b: console.create_key(b)),
        ("POST", "/api/keys/{}/revoke", lambda q, b, n: console.revoke_key(n)),
        ("GET", "/api/usage", lambda q, b: console.usage(_since(q.get("since", ["30d"])[0]), q.get("by", ["key"])[0])),
        ("POST", "/api/chat", lambda q, b: console.chat(b)),
    ]

    class Handler(BaseHTTPRequestHandler):
        server_version = "ookami-console"

        def log_message(self, fmt, *args):  # keys never reach logs; keep the console quiet
            pass

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            if url.path == "/healthz":
                return self._send(200, {"ok": True})
            if not url.path.startswith("/api/"):
                return self._static(url.path) if method == "GET" else self._send(405, {"error": "method not allowed"})
            try:
                self._authorize()
                body = self._body() if method == "POST" else {}
                for m, pattern, fn in routes:
                    if m != method:
                        continue
                    args = _match(pattern, url.path)
                    if args is not None:
                        return self._send(200, fn(parse_qs(url.query), body, *args))
                raise ApiError(404, "no such endpoint")
            except ApiError as e:
                self._send(e.status, {"error": str(e)})
            except Exception as e:  # never leak a traceback to the browser
                self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def _authorize(self) -> None:
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            if not token or not hmac.compare_digest(token, console.master_key()):
                raise ApiError(401, "sign in with the master key (ookami keys master)")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ApiError(413, "request too large")
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                raise ApiError(400, "body must be JSON") from None
            if not isinstance(data, dict):
                raise ApiError(400, "body must be a JSON object")
            return data

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            f = (STATIC / name).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                f = STATIC / "index.html"          # single-page app: unknown paths get the shell
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)

        def _send(self, status: int, payload: Any) -> None:
            data = json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)

        def _security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
                             "connect-src 'self'; frame-ancestors 'none'")

    return Handler


def _match(pattern: str, path: str) -> list[str] | None:
    a, b = pattern.strip("/").split("/"), path.strip("/").split("/")
    if len(a) != len(b):
        return None
    args = []
    for x, y in zip(a, b):
        if x == "{}":
            if not y:
                return None
            args.append(urllib.request.unquote(y))
        elif x != y:
            return None
    return args


def _since(text: str) -> float:
    unit = {"h": 3600, "d": 86400, "m": 60}.get(text[-1:])
    if unit is None or not text[:-1].replace(".", "", 1).isdigit():
        raise ApiError(400, "since must look like 24h, 7d or 30d")
    return float(text[:-1]) * unit


def serve(config_path: str | Path, host: str = "127.0.0.1", port: int = 4100) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(Console(config_path)))
    httpd.daemon_threads = True
    return httpd


def run(config_path: str | Path, host: str, port: int) -> None:
    httpd = serve(config_path, host, port)
    print(f"console: http://{host}:{port}  (sign in with `ookami keys master`)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def serve_in_thread(config_path: str | Path, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    """For tests: start on a free port in a background thread."""
    httpd = serve(config_path, host, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]
