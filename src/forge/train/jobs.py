"""The local job queue: one training job at a time, run by a background worker.

<storage>/jobs/<job_id>/
    job.json      model, version, weights, dataset, adapter dir, plan, trainer
    status.json   state, pid, times, message
    train.log     trainer output

`forge train` enqueues and starts a worker if none is running. The worker drains the queue one job at a
time and runs each job's gate as soon as training finishes. A job whose worker died is re-queued and
resumes from its last checkpoint.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from ..config import Config, load
from ..interfaces import JobSpec, JobState, JobStatus
from ..local import runtime
from ..registry import open_registry
from .backends import argv_for, post_train
from .gate import adapter_ready, gate_version

TERMINAL = {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}


def jobs_dir(cfg: Config) -> Path:
    return runtime.storage_dir(cfg) / "jobs"


class LocalJobRunner:
    """JobRunner for one machine. The one-job-at-a-time policy is enforced by the single worker."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = jobs_dir(cfg)

    def submit(self, spec: JobSpec, meta: dict | None = None) -> JobStatus:
        d = self.root / spec.job_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "job.json").write_text(json.dumps({**spec.__dict__, **(meta or {})}, indent=2, default=str))
        st = JobStatus(spec.job_id, JobState.QUEUED, message="waiting for the worker")
        self._write(st, queued_at=time.time())
        ensure_worker(self.cfg)
        return st

    def status(self, job_id: str) -> JobStatus:
        raw = json.loads((self.root / job_id / "status.json").read_text())
        return JobStatus(job_id, JobState(raw["state"]), raw.get("started_at"), raw.get("finished_at"),
                         raw.get("last_checkpoint"), raw.get("gpu_seconds", 0.0), raw.get("message", ""),
                         raw.get("metrics", {}))

    def cancel(self, job_id: str) -> None:
        raw = self._raw(job_id)
        if raw.get("pid") and runtime.alive(raw["pid"]):
            runtime.stop(raw["pid"])
        self._write(JobStatus(job_id, JobState.CANCELLED, message="cancelled"), **_keep(raw))

    def logs(self, job_id: str, tail: int = 200) -> str:
        return runtime.tail(str(self.root / job_id / "train.log"), tail)

    def list(self) -> list[tuple[str, dict]]:
        if not self.root.exists():
            return []
        out = [(d.name, self._raw(d.name)) for d in self.root.iterdir() if (d / "status.json").exists()]
        return sorted(out, key=lambda x: x[1].get("queued_at", 0))

    def _raw(self, job_id: str) -> dict:
        return json.loads((self.root / job_id / "status.json").read_text())

    def _write(self, st: JobStatus, **extra) -> None:
        path = self.root / st.job_id / "status.json"
        prev = json.loads(path.read_text()) if path.exists() else {}
        raw = {**prev, **extra, "state": st.state.value, "message": st.message}
        for k in ("started_at", "finished_at", "last_checkpoint"):
            if getattr(st, k) is not None:
                raw[k] = getattr(st, k)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, indent=2))
        tmp.replace(path)


def _keep(raw: dict) -> dict:
    return {k: raw[k] for k in ("queued_at", "pid") if k in raw}


def new_job_id(model: str) -> str:
    return f"{model}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


# ---------------------------------------------------------------- worker

def worker_lock(cfg: Config):
    path = jobs_dir(cfg) / "worker.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def worker_running(cfg: Config) -> bool:
    fh = worker_lock(cfg)
    if fh is None:
        return True
    fh.close()
    return False


def ensure_worker(cfg: Config) -> None:
    if worker_running(cfg):
        return
    log = jobs_dir(cfg) / "worker.log"
    with open(log, "ab") as out:
        subprocess.Popen([sys.executable, "-m", "forge.cli", "_worker", "-f", str(cfg.path)],
                         stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)


def run_worker(cfg_path: str) -> int:
    cfg = load(cfg_path)
    lock = worker_lock(cfg)
    if lock is None:
        return 0        # another worker owns the queue
    runner = LocalJobRunner(cfg)
    try:
        _recover(runner)
        while True:
            queued = [(jid, raw) for jid, raw in runner.list() if raw["state"] == JobState.QUEUED.value]
            if not queued:
                return 0
            run_job(cfg, runner, queued[0][0])
    finally:
        lock.close()


def _recover(runner: LocalJobRunner) -> None:
    """A job still marked running has no worker (we hold the lock): re-queue it to resume from its checkpoint."""
    for jid, raw in runner.list():
        if raw["state"] == JobState.RUNNING.value:
            if raw.get("pid") and runtime.alive(raw["pid"]):
                runtime.stop(raw["pid"])
            runner._write(JobStatus(jid, JobState.QUEUED, message="re-queued after an interrupted run; "
                                                                  "resumes from the last checkpoint"), **_keep(raw))


def run_job(cfg: Config, runner: LocalJobRunner, job_id: str) -> None:
    d = runner.root / job_id
    job = json.loads((d / "job.json").read_text())
    reg = open_registry(runtime.storage_dir(cfg))
    model, version = job["model"], job["version"]
    started = time.time()
    try:
        argv = argv_for(job["trainer"], d, job.get("command"))
        with open(d / "train.log", "ab") as out:
            out.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(argv)}\n".encode())
            proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env={**os.environ, "FORGE_JOB": str(d / "job.json")})
        runner._write(JobStatus(job_id, JobState.RUNNING, started_at=started, message="training"),
                      pid=proc.pid, **_keep(json.loads((d / "status.json").read_text())))
        code = proc.wait()
        if runner._raw(job_id)["state"] == JobState.CANCELLED.value:
            reg.update(model, version, status="failed")
            return
        adapter = Path(job["adapter_dir"])
        if code != 0 or not adapter_ready(adapter):
            why = f"trainer exited {code}" if code else "trainer wrote no adapter"
            runner._write(JobStatus(job_id, JobState.FAILED, finished_at=time.time(), message=why))
            reg.update(model, version, status="failed")
            return
        runner._write(JobStatus(job_id, JobState.RUNNING, message="packaging the adapter for serving"))
        post_train(job["trainer"], job, d / "train.log")
        runner._write(JobStatus(job_id, JobState.RUNNING, message="gating: candidate vs incumbent"))
        report = gate_version(cfg, reg, reg.get(model, version))
        runner._write(JobStatus(job_id, JobState.SUCCEEDED, finished_at=time.time(),
                                message=f"gate {report.decision}"), gate=report.decision)
    except Exception as e:  # the worker must survive a bad job and move on to the next
        runner._write(JobStatus(job_id, JobState.FAILED, finished_at=time.time(), message=f"{type(e).__name__}: {e}"))
        try:
            reg.update(model, version, status="failed")
        except KeyError:
            pass
    finally:
        reg.close()
