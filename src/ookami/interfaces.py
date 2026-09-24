"""The three backend seams. Every backend (local, k8s, skypilot) implements these; the controller only
talks to them. Keep them narrow: anything a backend can't do the same way belongs in the controller.

The fourth seam, Evaluator, lives in ookami.evaluators.base.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------- JobRunner: runs training (and eval) jobs

class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PREEMPTED = "preempted"   # spot reclaim or evicted by serving; resumes from the last checkpoint
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class JobSpec:
    """One reproducible job. Everything needed to re-run it is in here or content-addressed in storage."""
    job_id: str
    model: str                     # Model name from ookami.yaml
    kind: str                      # "train_sft" | "train_rl" | "eval"
    image: str                     # pinned by digest
    args: dict[str, Any]           # recipe after overrides and memory planning
    dataset_uri: str               # versioned dataset in the customer's bucket
    output_uri: str                # checkpoints and the adapter land here
    gpu: str = "auto"
    gpu_count: int = 1
    capacity: tuple[str, ...] = ("spot", "on-demand")
    checkpoint_every_s: int = 900
    resume_from: str | None = None


@dataclass
class JobStatus:
    job_id: str
    state: JobState
    started_at: float | None = None
    finished_at: float | None = None
    last_checkpoint: str | None = None
    gpu_seconds: float = 0.0
    message: str = ""
    metrics: dict[str, float] = field(default_factory=dict)


@runtime_checkable
class JobRunner(Protocol):
    """Runs at most what the controller submits; the one-job-at-a-time policy is the controller's."""

    def submit(self, spec: JobSpec) -> JobStatus: ...
    def status(self, job_id: str) -> JobStatus: ...
    def cancel(self, job_id: str) -> None: ...
    def logs(self, job_id: str, tail: int = 200) -> str: ...


# ---------------------------------------------------------------- ModelServer: serves bases + adapters

@dataclass(frozen=True)
class AdapterRef:
    model: str          # Model name
    version: str        # registry version, e.g. "v7"
    base: str           # base model id
    uri: str            # adapter weights (bucket path or oci:// artefact)


@dataclass(frozen=True)
class Endpoint:
    """An OpenAI-compatible endpoint the gateway (or the eval runner) can call."""
    base_url: str
    model: str          # the name to put in the request's "model" field
    api_key_secret: str | None = None


@runtime_checkable
class ModelServer(Protocol):
    """Many adapters on one loaded base (vLLM multi-LoRA). Load/unload are controller-only calls."""

    def ensure_base(self, base: str) -> None: ...
    def load_adapter(self, ref: AdapterRef) -> Endpoint: ...
    def unload_adapter(self, ref: AdapterRef) -> None: ...
    def adapters(self) -> list[AdapterRef]: ...
    def healthy(self) -> bool: ...


# ---------------------------------------------------------------- Router: the gateway hand-off

@dataclass(frozen=True)
class RoutePlan:
    """Where traffic for one route goes. weight is the candidate's share of *served* traffic."""
    route_model: str                      # the model name agents call today
    match: dict[str, str]
    incumbent: str                        # gateway deployment serving today
    candidate: Endpoint | None = None
    stage: str = "off"                    # off | shadow | canary:N% | live
    weight: float = 0.0                   # 0 for off/shadow, N/100 for canary, 1 for live
    mirror: bool = False                  # shadow: copy requests to the candidate, never serve its answer
    fallback_to_incumbent: bool = True    # cold or unhealthy candidate -> incumbent answers


@runtime_checkable
class Router(Protocol):
    """Implemented as a LiteLLM router plugin or an Agent Router extension; never a new gateway."""

    def apply(self, plan: RoutePlan) -> None: ...
    def current(self, route_model: str) -> RoutePlan | None: ...
    def rollback(self, route_model: str, reason: str) -> None: ...
