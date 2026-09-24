"""The forge.yaml schema: one source of truth for the CLI, the CRDs and the docs.

A forge.yaml holds YAML documents of two kinds:
  Platform  one per install: which components to run (gateway, serving, training, eval, registry, tracing,
            console), where state lives, and what compute to use. Everything is optional except storage.
  Model     one per model you run: a base model to serve, and optionally data + training + eval gate +
            gateway hand-off to turn it into your own fine-tuned model.

Unknown keys are errors. Secrets are references (${secret:name}), never values.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

API_VERSION = "forge.dev/v1alpha1"
SECRET_RE = re.compile(r"^\$\{secret:([A-Za-z0-9_.-]+)\}$")
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
STAGE_RE = re.compile(r"^(shadow|live|canary:(\d{1,3})%)$")

from .catalog import ALLOWED_LICENCES, CATALOG

BASE_MODELS: dict[str, str] = {k: v.licence for k, v in CATALOG.items()}

# Recipe fields a Model may override; everything else comes from the recipe and the memory planner.
OVERRIDABLE = {"lora.rank", "lora.alpha", "epochs", "learning_rate", "max_seq_len", "batch_size", "seed"}

EVALUATOR_KINDS = ("labels", "python", "webhook", "command", "plugin", "lm-eval", "inspect", "structural")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _secret(v: str | None) -> str | None:
    if v is not None and not SECRET_RE.match(v):
        raise ValueError("must be a secret reference like ${secret:name}, never an inline value")
    return v


class Metadata(Strict):
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("lowercase letters, digits and '-', 1-63 chars")
        return v


# ---------------------------------------------------------------- Platform

class Storage(Strict):
    uri: str = Field(description="s3://, gs://, az://, file:// or a local path")


class Database(Strict):
    url: str | None = Field(None, description="Postgres URL (a secret reference) or sqlite:///path; default SQLite in storage")

    @field_validator("url")
    @classmethod
    def _no_inline_credentials(cls, v: str | None) -> str | None:
        if v and not SECRET_RE.match(v) and "@" in v:
            raise ValueError("database credentials must come from a secret: use ${secret:name}")
        return v


class Gateway(Strict):
    mode: Literal["managed", "external", "none"] = Field(
        "managed", description="managed: forge runs the gateway; external: use yours; none: no gateway")
    type: Literal["litellm", "agent-router"] = "litellm"
    url: str | None = None
    adminKey: str | None = None
    port: int = Field(4000, ge=1, le=65535, description="managed gateway port")
    command: str | None = Field(None, description="advanced: launch template for a managed gateway, "
                                                  "with {config} {port} {host}")

    @field_validator("adminKey")
    @classmethod
    def _key_is_secret(cls, v: str | None) -> str | None:
        return _secret(v)

    @model_validator(mode="after")
    def _url_needed(self) -> "Gateway":
        if self.mode == "external" and not self.url:
            raise ValueError("an external gateway needs gateway.url")
        return self


class Component(Strict):
    enabled: bool = True


class Components(Strict):
    """Switch on what you need. Each component also works on its own."""
    serving: Component = Component()                  # vLLM, multi-LoRA, scale-to-zero
    training: Component = Component()                 # one-job queue, SFT / DPO / GRPO
    eval: Component = Component()                     # held-out sets, gate, reports
    registry: Component = Component()                 # models, adapters, lineage
    tracing: Component = Component(enabled=False)     # OTel collector: gateway traffic -> your bucket
    console: Component = Component(enabled=False)     # web UI


class TrainingCompute(Strict):
    gpu: str = "auto"
    count: int = Field(1, ge=1)
    capacity: list[Literal["spot", "on-demand", "reserved"]] = ["spot", "on-demand"]
    budgetHoursPerWeek: float | None = Field(None, gt=0)


class ServingCompute(Strict):
    gpu: str = "auto"
    scaleToZero: bool = True
    coldFallback: Literal["incumbent", "error"] = "incumbent"


class Compute(Strict):
    profile: Literal["local", "vm", "cloud-eks", "cloud-gke", "cloud-aks", "onprem"] = "local"
    training: TrainingCompute = TrainingCompute()
    serving: ServingCompute = ServingCompute()


class PlatformSpec(Strict):
    backend: Literal["local", "k8s", "skypilot"] = "local"
    storage: Storage
    database: Database = Database()
    gateway: Gateway = Gateway()
    components: Components = Components()
    compute: Compute = Compute()
    telemetry: Literal["off", "on"] = "off"

    @model_validator(mode="after")
    def _backend_fits(self) -> "PlatformSpec":
        if self.backend == "k8s":
            if self.compute.profile in ("local", "vm"):
                raise ValueError("backend k8s needs compute.profile cloud-eks|cloud-gke|cloud-aks|onprem")
            if not self.database.url or self.database.url.startswith("sqlite"):
                raise ValueError("backend k8s needs an external Postgres: database.url: ${secret:...}")
        if self.backend == "local" and self.compute.profile not in ("local", "vm"):
            raise ValueError("backend local runs on one machine: compute.profile local|vm")
        return self


# ---------------------------------------------------------------- Model

class DataSource(Strict):
    jsonl: str | None = None
    parquet: str | None = None
    hf: str | None = Field(None, description="Hugging Face dataset id")
    traces: Literal["gateway"] | None = Field(None, description="traffic captured by the tracing component")
    match: dict[str, str] = {}

    @model_validator(mode="after")
    def _one(self) -> "DataSource":
        given = [k for k in ("jsonl", "parquet", "hf", "traces") if getattr(self, k) is not None]
        if len(given) != 1:
            raise ValueError("set exactly one of jsonl, parquet, hf, traces")
        return self


class Data(Strict):
    source: DataSource
    minExamples: int = Field(500, ge=1)


class Train(Strict):
    recipe: Literal["sft", "dpo", "grpo", "sft-then-grpo"] = "sft"
    reward: str | None = Field(None, description="python evaluator ref used as the RL reward, e.g. ./evals/reward.py:score")
    overrides: dict[str, Any] = {}

    @model_validator(mode="after")
    def _check(self) -> "Train":
        if "grpo" in self.recipe and not self.reward:
            raise ValueError(f"recipe {self.recipe} needs train.reward")
        bad = set(self.overrides) - OVERRIDABLE
        if bad:
            raise ValueError(f"cannot override {sorted(bad)}; allowed: {sorted(OVERRIDABLE)}")
        return self


class Splits(Strict):
    heldOut: float = Field(0.1, gt=0, lt=1)
    audit: float = Field(0.05, ge=0, lt=1)
    stratifyBy: list[str] = []
    seed: int = 0
    refreshAfter: str | None = Field(None, description="e.g. 30d; a refresh creates a new split version")

    @model_validator(mode="after")
    def _room_to_train(self) -> "Splits":
        if self.heldOut + self.audit >= 0.5:
            raise ValueError("heldOut + audit must leave at least half the data for training")
        return self


class EvaluatorSpec(Strict):
    kind: Literal[EVALUATOR_KINDS]  # type: ignore[valid-type]
    params: dict[str, Any] = {}
    name: str
    gate: bool = True

    @model_validator(mode="before")
    @classmethod
    def _compact(cls, v: Any) -> Any:
        """Accept the compact YAML forms: `forge/structural`, `{python: ./f.py:fn}`, `{labels: {column: x}}`."""
        if isinstance(v, str):
            if v == "forge/structural":
                return {"kind": "structural", "name": "structural"}
            raise ValueError(f"unknown evaluator {v!r}")
        if not isinstance(v, dict) or "kind" in v:
            return v
        kinds = [k for k in v if k in EVALUATOR_KINDS]
        if len(kinds) != 1:
            raise ValueError(f"an evaluator needs exactly one of {list(EVALUATOR_KINDS)}")
        kind = kinds[0]
        body = v[kind]
        extra = {k: x for k, x in v.items() if k != kind}
        if kind == "python":
            params = {"ref": body} if isinstance(body, str) else dict(body)
            default = Path(params.get("ref", "python").rsplit(":", 1)[0]).stem  # ./evals/task_check.py:score -> task_check
        elif kind == "lm-eval":
            params = {"tasks": body} if isinstance(body, list) else dict(body)
            default = "lm-eval"
        elif kind == "inspect":
            params = {"task": body} if isinstance(body, str) else dict(body)
            default = "inspect"
        else:
            params = dict(body or {})
            default = {"labels": f"labels:{params.get('column', 'label')}",
                       "plugin": str(params.get("use", "plugin"))}.get(kind, kind)
        return {"kind": kind, "params": params, "name": extra.pop("name", default), **extra}

    @model_validator(mode="after")
    def _params(self) -> "EvaluatorSpec":
        need = {"python": "ref", "webhook": "url", "command": "run", "plugin": "use", "lm-eval": "tasks",
                "inspect": "task"}.get(self.kind)
        if need and need not in self.params:
            raise ValueError(f"{self.kind} evaluator needs {need}")
        return self


class Gate(Strict):
    vs: Literal["incumbent"] = "incumbent"
    test: Literal["non-inferiority", "superiority", "threshold"] = "non-inferiority"
    margin: float = Field(-0.01, le=0, description="largest drop vs the incumbent we accept")
    confidence: float = Field(0.95, gt=0.5, lt=1)
    perSlice: bool = True
    min: float | None = Field(None, description="absolute floor for the candidate's mean score")
    minItems: int = Field(20, ge=1, description="fewer paired items than this and the gate cannot pass")
    resamples: int = Field(10000, ge=1000)

    @model_validator(mode="after")
    def _threshold(self) -> "Gate":
        if self.test == "threshold" and self.min is None:
            raise ValueError("test threshold needs gate.min")
        return self


class Eval(Strict):
    splits: Splits = Splits()
    evaluators: list[EvaluatorSpec] = Field(min_length=1)
    gate: Gate = Gate()

    @model_validator(mode="after")
    def _unique(self) -> "Eval":
        names = [e.name for e in self.evaluators]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            raise ValueError(f"evaluator names must be unique; set name: on {sorted(dup)}")
        if not any(e.gate for e in self.evaluators):
            raise ValueError("at least one evaluator must gate (gate: true)")
        return self


class RouteMatch(Strict):
    model: str = Field(description="the model name agents call today, as the gateway sees it")
    match: dict[str, str] = {}


class Rollback(Strict):
    metric: str
    below: float
    window: str = "1h"


class Handoff(Strict):
    route: RouteMatch | None = None
    stages: list[str] = ["shadow", "canary:10%", "live"]
    approve: list[Literal["shadow", "canary", "live"]] = ["live"]
    rollback: Rollback | None = None
    shadowExecutor: str | None = Field(None, description="python ref that replays side-effecting calls in a sandbox")

    @field_validator("stages")
    @classmethod
    def _stages(cls, v: list[str]) -> list[str]:
        last = -1
        for s in v:
            m = STAGE_RE.match(s)
            if not m:
                raise ValueError(f"bad stage {s!r}: use shadow, canary:N%, live")
            pct = 0 if s == "shadow" else 100 if s == "live" else int(m.group(2))
            if s.startswith("canary") and not 0 < pct < 100:
                raise ValueError(f"canary share must be 1-99%, got {s}")
            if pct <= last and s != "shadow":
                raise ValueError("stages must increase: shadow, then canary shares ascending, then live")
            last = pct
        if not v or v[-1] != "live":
            raise ValueError("stages must end with live")
        return v


class Serve(Strict):
    engine: Literal["auto", "vllm", "mlx", "command"] = Field(
        "auto", description="auto: vllm on NVIDIA GPUs, mlx on Apple silicon; command: any OpenAI-compatible server")
    command: str | None = Field(None, description="engine command: launch template with {weights} {port} {host} {name}")
    modelId: str | None = Field(None, description="engine command: the model id the server expects in requests")
    port: int | None = Field(None, ge=1, le=65535)
    host: str = "127.0.0.1"
    args: list[str] = Field([], description="extra engine flags, e.g. [--max-model-len, '8192']")

    @model_validator(mode="after")
    def _command(self) -> "Serve":
        if self.engine == "command" and not self.command:
            raise ValueError("engine command needs serve.command")
        return self


class ModelSpec(Strict):
    base: str = Field(description="catalog name, or any name when weights and licence are set")
    licence: str | None = Field(None, description="required when base is not in forge's catalog")
    weights: str | None = Field(None, description="Hugging Face id or local path; defaults to the catalog's")
    serve: Serve = Serve()
    data: Data | None = None
    train: Train | None = None
    eval: Eval | None = None
    handoff: Handoff = Handoff()

    @model_validator(mode="after")
    def _check(self) -> "ModelSpec":
        if self.base not in BASE_MODELS:
            if self.licence is None:
                raise ValueError(f"base {self.base!r} is not in the catalog; set licence to its licence")
            if self.licence not in ALLOWED_LICENCES:
                raise ValueError(f"licence {self.licence!r} is not allowed; use one of {sorted(ALLOWED_LICENCES)}")
        if self.train and not self.data:
            raise ValueError("train needs data")
        if self.train and not self.eval:
            raise ValueError("train needs eval: a trained model is promoted only through the gate")
        rb = self.handoff.rollback
        if rb and (not self.eval or rb.metric not in {e.name for e in self.eval.evaluators}):
            raise ValueError(f"handoff.rollback.metric {rb.metric!r} is not an evaluator name")
        return self


class PlatformDoc(Strict):
    apiVersion: Literal[API_VERSION]  # type: ignore[valid-type]
    kind: Literal["Platform"]
    metadata: Metadata
    spec: PlatformSpec


class ModelDoc(Strict):
    apiVersion: Literal[API_VERSION]  # type: ignore[valid-type]
    kind: Literal["Model"]
    metadata: Metadata
    spec: ModelSpec


DOC_TYPES = {"Platform": PlatformDoc, "Model": ModelDoc}


# ---------------------------------------------------------------- loading and linting

@dataclass
class Issue:
    level: Literal["error", "warning"]
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.where}: {self.message}"


@dataclass
class Config:
    path: Path
    platform: PlatformDoc | None = None
    models: dict[str, ModelDoc] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def base_dir(self) -> Path:
        return self.path.parent

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    def resolve(self, p: str) -> Path:
        """Paths in forge.yaml are relative to the file."""
        q = Path(p).expanduser()
        return q if q.is_absolute() else (self.base_dir / q).resolve()


def load(path: str | Path) -> Config:
    """Parse and validate every document; collect all issues instead of stopping at the first."""
    path = Path(path)
    cfg = Config(path=path)
    try:
        docs = list(yaml.safe_load_all(path.read_text()))
    except (OSError, yaml.YAMLError) as e:
        cfg.issues.append(Issue("error", str(path), str(e)))
        return cfg
    for i, raw in enumerate(d for d in docs if d is not None):
        where = f"{path.name} doc {i + 1}"
        if not isinstance(raw, dict) or raw.get("kind") not in DOC_TYPES:
            cfg.issues.append(Issue("error", where, f"kind must be one of {list(DOC_TYPES)}"))
            continue
        name = (raw.get("metadata") or {}).get("name", "?")
        where = f"{raw['kind']}/{name}"
        try:
            doc = DOC_TYPES[raw["kind"]].model_validate(raw)
        except ValidationError as e:
            for err in e.errors():
                loc = ".".join(str(x) for x in err["loc"] if not str(x).startswith("function-"))
                cfg.issues.append(Issue("error", f"{where} {loc}".strip(), err["msg"].removeprefix("Value error, ")))
            continue
        if isinstance(doc, PlatformDoc):
            if cfg.platform:
                cfg.issues.append(Issue("error", where, "only one Platform per install"))
            cfg.platform = doc
        else:
            if name in cfg.models:
                cfg.issues.append(Issue("error", where, "duplicate Model name"))
            cfg.models[name] = doc
    if cfg.ok:
        cfg.issues.extend(lint(cfg))
    return cfg


def lint(cfg: Config) -> list[Issue]:
    """Cross-document errors (a Model needs components the Platform turned off) and warnings: valid config
    that is probably a mistake. The evaluation guardrails live here."""
    out: list[Issue] = []
    if cfg.platform is None:
        out.append(Issue("warning", cfg.path.name, "no Platform document; commands that need storage or a gateway will fail"))
    plat = cfg.platform.spec if cfg.platform else None
    for name, m in cfg.models.items():
        where = f"Model/{name}"
        spec = m.spec
        if plat:
            c = plat.components
            needs = [("serving", True), ("training", spec.train is not None), ("eval", spec.eval is not None),
                     ("registry", spec.train is not None)]
            for comp, needed in needs:
                if needed and not getattr(c, comp).enabled:
                    out.append(Issue("error", where, f"needs the {comp} component; enable components.{comp}"))
            if spec.handoff.route and plat.gateway.mode == "none":
                out.append(Issue("error", where, "handoff.route needs a gateway (gateway.mode managed or external)"))
            if spec.data and spec.data.source.traces and not c.tracing.enabled:
                out.append(Issue("error", where, "data.source.traces needs components.tracing"))
        for p in [spec.data.source.jsonl, spec.data.source.parquet] if spec.data else []:
            if p and "://" not in p and not cfg.resolve(p).exists():
                out.append(Issue("warning", where, f"data source not found yet: {p}"))
        reward = spec.train.reward if spec.train else None
        refs = [reward, spec.handoff.shadowExecutor]
        ev = spec.eval
        if ev:
            refs += [e.params.get("ref") for e in ev.evaluators if e.kind == "python"]
            gating = [e for e in ev.evaluators if e.gate]
            if all(e.kind == "structural" for e in gating):
                out.append(Issue("warning", where,
                                 "the gate uses structural checks only; it cannot tell whether answers are right"))
            if reward and any(e.kind == "python" and e.params.get("ref") == reward for e in gating):
                out.append(Issue("warning", where,
                                 "train.reward is also a gate evaluator; RL will exploit its blind spots and the gate "
                                 "cannot see them. Gate on a different evaluator, or rely on the audit split"))
            if reward and ev.splits.audit == 0:
                out.append(Issue("warning", where,
                                 "RL with eval.splits.audit 0: nothing independent of the reward checks the model"))
        if spec.handoff.route and "shadow" in spec.handoff.stages and not spec.handoff.shadowExecutor:
            out.append(Issue("warning", where,
                             "shadow scores are valid only for single-turn or read-only routes; multi-turn agents "
                             "with side effects need handoff.shadowExecutor"))
        for ref in refs:
            if ref and not cfg.resolve(ref.rsplit(":", 1)[0]).exists():
                out.append(Issue("error", where, f"python ref not found: {ref}"))
    return out


def json_schema() -> dict[str, Any]:
    """JSON Schema for editor autocomplete; the CRDs are generated from the same models."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "forge.yaml document",
        "oneOf": [PlatformDoc.model_json_schema(), ModelDoc.model_json_schema()],
    }
