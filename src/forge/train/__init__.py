"""Training: snapshot the data, plan the run, queue the job. The worker trains, then gates."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..config import Config
from ..data import Snapshot, snapshot
from ..interfaces import JobSpec
from ..local import runtime
from ..local.engines import resolve_weights
from ..registry import Version, open_registry
from .backends import detect_trainer
from .jobs import LocalJobRunner, new_job_id
from .planner import Plan, plan


@dataclass
class Submitted:
    job_id: str
    version: Version
    snapshot: Snapshot
    plan: Plan


def submit_training(cfg: Config, model: str) -> Submitted:
    if model not in cfg.models:
        raise KeyError(f"no Model {model!r}; have {sorted(cfg.models)}")
    doc = cfg.models[model]
    spec = doc.spec
    if not spec.train:
        raise ValueError(f"Model {model!r} has no train section")
    if spec.train.recipe != "sft":
        raise NotImplementedError(f"recipe {spec.train.recipe} arrives in 0.3; 0.2 trains sft")
    if cfg.platform and cfg.platform.spec.backend != "local":
        raise NotImplementedError(f"training on backend {cfg.platform.spec.backend} arrives in 0.4")

    trainer = detect_trainer() if spec.train.engine == "auto" else spec.train.engine
    serve_engine = {"mlx": "mlx", "trl": "vllm"}.get(trainer, "vllm")
    weights = spec.weights or resolve_weights(doc, serve_engine)
    snap = snapshot(cfg, model)
    p = plan(spec, trainer, snap.train, weights=weights)

    storage = runtime.storage_dir(cfg)
    reg = open_registry(storage)
    try:
        job_id = new_job_id(model)
        v = reg.create(model, spec.base, weights, dataset=str(snap.dir), dataset_hash=snap.hash, job_id=job_id,
                       config_sha=hashlib.sha256(cfg.path.read_bytes()).hexdigest()[:16])
        adapter_dir = storage / "models" / model / f"v{v.version}" / "adapter"
        adapter_dir.mkdir(parents=True, exist_ok=True)
        v = reg.update(model, v.version, adapter=str(adapter_dir))
    finally:
        reg.close()
    spec_ = JobSpec(job_id=job_id, model=model, kind="train_sft", image="local", args=p.as_dict(),
                    dataset_uri=str(snap.dir), output_uri=str(adapter_dir))
    LocalJobRunner(cfg).submit(spec_, meta={
        "version": v.version, "weights": weights, "dataset_dir": str(snap.dir), "adapter_dir": str(adapter_dir),
        "plan": p.as_dict(), "trainer": trainer, "command": spec.train.command,
    })
    return Submitted(job_id, v, snap, p)
