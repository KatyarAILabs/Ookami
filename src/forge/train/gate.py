"""Gate a freshly trained version: serve it next to the incumbent, run the Model's evaluators, record the decision.

The incumbent is the version that is live now, or the untrained base model when nothing is live yet.
"""
from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Iterator

from ..config import Config
from ..eval_runner import Report, run_eval, save
from ..evaluators.base import Target
from ..local import runtime
from ..local.engines import engine_for, launch
from ..registry import Registry, Version


@contextlib.contextmanager
def serve_pair(cfg: Config, model: str, candidate: str, incumbent: str | None,
               timeout: float = 900.0) -> Iterator[tuple[Target, Target]]:
    """Serve candidate and incumbent adapters (incumbent None = base) and yield their targets."""
    doc = cfg.models[model]
    logs = runtime.storage_dir(cfg) / "logs"
    started: list[runtime.Service] = []
    host = doc.spec.serve.host

    def run(tag: str, port: int, **kw) -> runtime.Service:
        spec = launch(doc, port, **kw)
        log = logs / f"gate-{model}-{tag}.log"
        svc = runtime.Service(f"gate-{tag}", "engine", runtime.start(spec.argv, log), port,
                              f"http://{host}:{port}/v1", f"http://{host}:{port}/v1/models", str(log), spec.argv,
                              spec.model_id)
        started.append(svc)
        return svc

    try:
        taken = {s.port for s in runtime.read_state(cfg)}
        if engine_for(doc) == "vllm":
            # one process, both adapters: two vLLM servers would fight over GPU memory
            loras = {"candidate": candidate, **({"incumbent": incumbent} if incumbent else {})}
            svc = run("pair", runtime.pick_port(taken), loras=loras)
            runtime.wait_ready(svc, timeout)
            inc_id = "incumbent" if incumbent else f"{model}-base"
            yield (Target("candidate", "openai", base_url=svc.url, model="candidate"),
                   Target("incumbent", "openai", base_url=svc.url, model=inc_id))
            return
        cport = runtime.pick_port(taken)
        cand = run("candidate", cport, adapter=candidate)
        inc = run("incumbent", runtime.pick_port(taken | {cport}), adapter=incumbent)
        for svc in (cand, inc):
            runtime.wait_ready(svc, timeout)
        yield (Target("candidate", "openai", base_url=cand.url, model=cand.model_id),
               Target("incumbent", "openai", base_url=inc.url, model=inc.model_id))
    finally:
        for svc in reversed(started):
            runtime.stop(svc.pid)


def gate_version(cfg: Config, reg: Registry, v: Version, timeout: float = 900.0) -> Report:
    live = reg.live(v.model)
    reg.update(v.model, v.version, status="evaluating")
    try:
        with serve_pair(cfg, v.model, v.adapter, live.adapter if live else None, timeout) as (cand, inc):
            report = run_eval(cfg, v.model, cand, inc)
    except Exception:
        reg.update(v.model, v.version, status="failed")
        raise
    report.provenance.update(version=v.version, incumbent=live.tag if live else f"base:{v.base}")
    path = save(report, cfg)
    status = "passed" if report.decision == "pass" else "rejected"
    reg.update(v.model, v.version, status=status, decision=report.decision, report=str(path))
    return report


def adapter_ready(adapter_dir: Path) -> bool:
    return any(adapter_dir.glob("adapter*.safetensors")) or any(adapter_dir.glob("adapter_model.*"))
