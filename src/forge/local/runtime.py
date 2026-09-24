"""The local backend: `forge up` / `forge down` / `forge status` on one machine.

Services run as background processes. Their pids, ports and logs are recorded in
<storage>/run/state.json, so later commands can find them.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from ..config import Config
from .engines import EngineError, launch

ENGINE_PORTS = range(8100, 8200)


@dataclass
class Service:
    name: str
    kind: str              # "engine" | "gateway"
    pid: int
    port: int
    url: str               # OpenAI-compatible base URL, ends with /v1
    health: str            # URL that returns 200 when ready
    log: str
    argv: list[str]
    model_id: str = ""     # for engines: the id to send when calling the engine directly


class UpError(RuntimeError):
    """forge up/down/status could not do what was asked; the message says why."""


def storage_dir(cfg: Config) -> Path:
    if not cfg.platform:
        raise UpError("forge.yaml needs a Platform document")
    uri = cfg.platform.spec.storage.uri
    if uri.startswith("file://"):
        return Path(uri[len("file://"):])
    if "://" in uri:
        raise UpError(f"the local backend needs local storage (a path or file://), got {uri}")
    return cfg.resolve(uri)


def state_path(cfg: Config) -> Path:
    return storage_dir(cfg) / "run" / "state.json"


def read_state(cfg: Config) -> list[Service]:
    p = state_path(cfg)
    if not p.exists():
        return []
    return [Service(**s) for s in json.loads(p.read_text())["services"]]


def write_state(cfg: Config, services: list[Service]) -> None:
    p = state_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"services": [asdict(s) for s in services]}, indent=2))


# ---------------------------------------------------------------- processes

def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def healthy(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(taken: set[int]) -> int:
    for p in ENGINE_PORTS:
        if p not in taken and port_free(p):
            return p
    raise UpError(f"no free port in {ENGINE_PORTS.start}-{ENGINE_PORTS.stop - 1}")


def start(argv: list[str], log: Path, env: dict | None = None) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        out.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {shlex.join(argv)}\n".encode())
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, env={**os.environ, **(env or {})})
    return proc.pid


def stop(pid: int, grace: float = 10.0) -> None:
    if not alive(pid):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + grace
    while time.time() < deadline:
        if not alive(pid) or _reap(pid):
            return
        time.sleep(0.2)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _reap(pid)


def _reap(pid: int) -> bool:
    """Collect our own exited children so they don't linger as zombies."""
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == pid
    except ChildProcessError:
        return False


def tail(path: str, n: int = 40) -> str:
    try:
        return "\n".join(Path(path).read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def wait_ready(svc: Service, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if healthy(svc.health):
            return
        if not alive(svc.pid) or _reap(svc.pid):
            raise UpError(f"{svc.name} exited while starting. Last log lines:\n{tail(svc.log)}")
        time.sleep(1.0)
    raise UpError(f"{svc.name} not ready after {timeout:.0f}s (see {svc.log})")


# ---------------------------------------------------------------- up / down / status

def gateway_config(engines: list[Service], tracing: bool = False) -> dict:
    settings: dict = {"drop_params": True}
    if tracing:
        settings["callbacks"] = ["generic_api"]   # LiteLLM -> Trajectory webhook, one callback per completion
    return {
        "model_list": [{"model_name": s.name,
                        "litellm_params": {"model": f"openai/{s.model_id}", "api_base": s.url, "api_key": "none"}}
                       for s in engines],
        "litellm_settings": settings,
    }


def tracing_env(plat) -> dict[str, str]:
    """Environment that points LiteLLM's generic_api callback at the Trajectory collector."""
    tr = plat.tracing
    env = {"GENERIC_LOGGER_ENDPOINT": tr.webhook}
    if tr.tokenEnv:
        token = os.environ.get(tr.tokenEnv)
        if not token:
            raise UpError(f"tracing.tokenEnv {tr.tokenEnv} is not set in the environment")
        env["GENERIC_LOGGER_HEADERS"] = f"Authorization=Bearer {token}"
    return env


def litellm_bin() -> str | None:
    """Prefer the LiteLLM installed next to forge (forge-ml[gateway]) over whatever is on PATH."""
    beside = Path(sys.executable).parent / "litellm"
    return str(beside) if beside.exists() else shutil.which("litellm")


def up(cfg: Config, timeout: float = 900.0, log=print) -> list[Service]:
    plat = cfg.platform.spec if cfg.platform else None
    if plat is None:
        raise UpError("forge.yaml needs a Platform document")
    if plat.backend != "local":
        raise UpError(f"forge up runs the local backend; backend {plat.backend} is not implemented yet")
    running = [s for s in read_state(cfg) if alive(s.pid)]
    if running:
        raise UpError(f"already up ({', '.join(s.name for s in running)}); run forge down first")
    if not plat.components.serving.enabled:
        raise UpError("components.serving is disabled; nothing to run")
    if not cfg.models:
        raise UpError("no Model documents to serve")

    logs = storage_dir(cfg) / "logs"
    services: list[Service] = []
    tracing = plat.components.tracing.enabled and plat.tracing is not None
    try:
        if tracing and plat.tracing.mode == "managed":
            tr = plat.tracing
            exe = shutil.which(tr.bin) or (str(cfg.resolve(tr.bin)) if cfg.resolve(tr.bin).exists() else None)
            if not exe:
                raise UpError(f"Trajectory CLI {tr.bin!r} not found; install Trajectory or set tracing.bin")
            argv = [exe, "run", "-config", str(cfg.resolve(tr.config))]
            svc = Service("tracing", "tracing", start(argv, logs / "tracing.log"), 0, tr.webhook, tr.healthUrl,
                          str(logs / "tracing.log"), argv)
            services.append(svc)
            write_state(cfg, services)
            wait_ready(svc, min(timeout, 120.0))
            log(f"ready    tracing: Trajectory collector, lake {tr.lake}")
        taken: set[int] = {plat.gateway.port}
        for name, doc in cfg.models.items():
            port = doc.spec.serve.port or pick_port(taken)
            taken.add(port)
            live = live_version(cfg, name)
            try:
                spec = launch(doc, port, adapter=live.adapter if live else None)
            except (EngineError, KeyError) as e:
                raise UpError(str(e)) from None
            host = doc.spec.serve.host
            svc = Service(name, "engine", start(spec.argv, logs / f"{name}.log"), port,
                          f"http://{host}:{port}/v1", f"http://{host}:{port}/v1/models", str(logs / f"{name}.log"),
                          spec.argv, spec.model_id)
            services.append(svc)
            write_state(cfg, services)
            version = f" + {live.tag}" if live else ""
            log(f"starting {name}: {spec.engine} {spec.weights}{version} on :{port}")
        for svc in services:
            if svc.kind == "engine":
                wait_ready(svc, timeout)
                log(f"ready    {svc.name}: {svc.url}")

        gw = plat.gateway
        if gw.mode == "managed":
            conf = storage_dir(cfg) / "run" / "gateway.yaml"
            engines = [s for s in services if s.kind == "engine"]
            conf.write_text(yaml.safe_dump(gateway_config(engines, tracing), sort_keys=False))
            host = "127.0.0.1"
            if gw.command:
                argv = shlex.split(gw.command.format(config=conf, port=gw.port, host=host))
            else:
                exe = litellm_bin()
                if not exe:
                    raise UpError("the managed gateway needs LiteLLM: pip install 'forge-ml[gateway]'")
                argv = [exe, "--config", str(conf), "--host", host, "--port", str(gw.port)]
            if not port_free(gw.port):
                raise UpError(f"gateway port {gw.port} is busy; set gateway.port")
            env = tracing_env(plat) if tracing else None
            svc = Service("gateway", "gateway", start(argv, logs / "gateway.log", env), gw.port,
                          f"http://{host}:{gw.port}/v1", f"http://{host}:{gw.port}/health/liveliness",
                          str(logs / "gateway.log"), argv)
            services.append(svc)
            write_state(cfg, services)
            try:
                wait_ready(svc, min(timeout, 120.0))
            except UpError as e:
                raise UpError(f"{e}\nIf LiteLLM is missing its proxy extras: pip install 'forge-ml[gateway]'") from None
            log(f"ready    gateway: {svc.url}")
        elif gw.mode == "external":
            snippet = storage_dir(cfg) / "run" / "gateway-models.yaml"
            snippet.write_text(yaml.safe_dump(gateway_config([s for s in services if s.kind == "engine"], tracing),
                                              sort_keys=False))
            log(f"external gateway: add the model_list in {snippet} to your gateway at {gw.url}")
    except BaseException:
        for s in reversed(services):
            stop(s.pid)
        state_path(cfg).unlink(missing_ok=True)
        raise
    return services


def live_version(cfg: Config, model: str):
    """The promoted registry version for a Model, if any."""
    from ..registry import open_registry
    path = storage_dir(cfg) / "registry.db"
    if not path.exists():
        return None
    reg = open_registry(storage_dir(cfg))
    try:
        return reg.live(model)
    finally:
        reg.close()


def restart_engine(cfg: Config, model: str, timeout: float = 900.0, log=print) -> bool:
    """Restart a running Model's engine so it serves the current live version. False if it isn't running."""
    services = read_state(cfg)
    svc = next((s for s in services if s.kind == "engine" and s.name == model and alive(s.pid)), None)
    if svc is None:
        return False
    live = live_version(cfg, model)
    spec = launch(cfg.models[model], svc.port, adapter=live.adapter if live else None)
    stop(svc.pid)
    svc.pid, svc.argv, svc.model_id = start(spec.argv, Path(svc.log)), spec.argv, spec.model_id
    write_state(cfg, services)
    wait_ready(svc, timeout)
    log(f"ready    {model}: now serving {live.tag if live else 'the base model'}")
    return True


def down(cfg: Config, log=print) -> int:
    services = read_state(cfg)
    for s in reversed(services):
        stop(s.pid)
        log(f"stopped  {s.name}")
    state_path(cfg).unlink(missing_ok=True)
    return len(services)


def status(cfg: Config) -> list[tuple[Service, str]]:
    out = []
    for s in read_state(cfg):
        state = "ready" if alive(s.pid) and healthy(s.health) else "starting" if alive(s.pid) else "exited"
        out.append((s, state))
    return out
