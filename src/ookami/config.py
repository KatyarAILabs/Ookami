"""The ookami.yaml schema: one source of truth for the CLI, the CRDs and the docs.

A ookami.yaml holds YAML documents of two kinds:
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

API_VERSION = "ookami.dev/v1alpha1"
SECRET_RE = re.compile(r"^\$\{secret:([A-Za-z0-9_.-]+)\}$")
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
STAGE_RE = re.compile(r"^(shadow|live|canary:(\d{1,3})%)$")

from .catalog import ALLOWED_LICENCES, CATALOG

BASE_MODELS: dict[str, str] = {k: v.licence for k, v in CATALOG.items()}

# Recipe fields a Model may override; everything else comes from the recipe and the memory planner.
OVERRIDABLE = {"lora.rank", "lora.alpha", "lora.dropout", "lora.layers", "epochs", "iters", "learning_rate",
               "max_seq_len", "batch_size", "grad_accumulation", "seed", "quantize"}

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
    """Where datasets, checkpoints, adapters, reports and the registry live."""
    uri: str = Field(description="s3://, gs://, az://, file:// or a local path")


class Database(Strict):
    """State database. SQLite in storage for the local backend; Postgres on k8s."""
    url: str | None = Field(None, description="Postgres URL (a secret reference) or sqlite:///path; default SQLite in storage")

    @field_validator("url")
    @classmethod
    def _no_inline_credentials(cls, v: str | None) -> str | None:
        if v and not SECRET_RE.match(v) and "@" in v:
            raise ValueError("database credentials must come from a secret: use ${secret:name}")
        return v


class Gateway(Strict):
    """The OpenAI-compatible gateway in front of every model."""
    mode: Literal["managed", "external", "none"] = Field(
        "managed", description="managed: ookami runs the gateway; external: use yours; none: no gateway")
    type: Literal["litellm", "agent-router"] = Field("litellm", description="gateway implementation")
    url: str | None = Field(None, description="required for an external gateway")
    adminKey: str | None = Field(None, description="secret reference for the gateway's admin API")
    port: int = Field(4000, ge=1, le=65535, description="managed gateway port")
    auth: Literal["keys", "none"] = Field(
        "keys", description="keys: every call needs a Ookami key (ookami keys create) or the master key; none: open")
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


class Tracing(Strict):
    """Traffic capture with Trajectory (github.com/KatyarAILabs/trajectory): the collector, its lake, and the gateway hook."""
    mode: Literal["managed", "external"] = Field(
        "managed", description="managed: ookami up runs the collector; external: a collector you run elsewhere")
    config: str | None = Field(None, description="managed: your Trajectory collector config (redaction policy is yours)")
    lake: str = Field(description="the lake directory the collector writes; traces data sources export from it")
    webhook: str = Field("http://127.0.0.1:4320/v1/hooks/litellm",
                         description="collector endpoint the managed gateway sends LiteLLM callbacks to")
    tokenEnv: str | None = Field(None, description="env var holding the bearer token the collector's webhook expects")
    healthUrl: str = Field("http://127.0.0.1:9464/healthz", description="collector liveness URL (its telemetry listener)")
    bin: str = Field("cc", description="the Trajectory CLI")

    @model_validator(mode="after")
    def _config_needed(self) -> "Tracing":
        if self.mode == "managed" and not self.config:
            raise ValueError("managed tracing needs tracing.config: the Trajectory collector config to run")
        return self


class Langfuse(Strict):
    """Send every gateway call to Langfuse (over OpenTelemetry) for a trace and eval UI."""
    host: str = Field(description="your Langfuse URL, e.g. http://langfuse.internal:3000")
    publicKey: str = Field(description="secret reference to the Langfuse public key")
    secretKey: str = Field(description="secret reference to the Langfuse secret key")

    @field_validator("publicKey", "secretKey")
    @classmethod
    def _secrets(cls, v: str) -> str:
        return _secret(v)


class Observability(Strict):
    """Where the gateway sends traces for humans to browse. Trajectory (tracing) is for training data."""
    langfuse: Langfuse | None = None


class Component(Strict):
    """Turn a component on or off."""
    enabled: bool = Field(True, description="run this component")


class Components(Strict):
    """Switch on what you need. Each component also works on its own."""
    serving: Component = Field(Component(), description="inference engines (vLLM, MLX or a command); on by default")
    training: Component = Field(Component(), description="fine-tuning queue and trainers; on by default")
    eval: Component = Field(Component(), description="held-out sets, the promotion gate, reports; on by default")
    registry: Component = Field(Component(), description="versions, gate decisions, promotions; on by default")
    tracing: Component = Field(Component(enabled=False), description="capture gateway traffic with Trajectory (set Platform.tracing); off by default")
    console: Component = Field(Component(enabled=False), description="web UI (planned); off by default")


class TrainingCompute(Strict):
    """Compute for training jobs."""
    gpu: str = Field("auto", description="GPU type for training nodes (cloud backends)")
    count: int = Field(1, ge=1, description="GPUs per training job")
    capacity: list[Literal["spot", "on-demand", "reserved"]] = Field(["spot", "on-demand"], description="capacity types to try, in order")
    budgetHoursPerWeek: float | None = Field(None, gt=0, description="GPU-hour budget the queue schedules within (planned)")


class ServingCompute(Strict):
    """Compute for inference engines."""
    gpu: str = Field("auto", description="GPU type for serving nodes (cloud backends)")
    scaleToZero: bool = Field(True, description="scale engines to zero when idle (k8s, planned)")
    coldFallback: Literal["incumbent", "error"] = Field("incumbent", description="what the gateway does while a model is cold (planned)")


class Compute(Strict):
    """What compute to use and where it comes from."""
    profile: Literal["local", "vm", "cloud-eks", "cloud-gke", "cloud-aks", "onprem"] = Field(
        "local", description="where compute comes from; picks the autoscaler wiring on k8s")
    training: TrainingCompute = TrainingCompute()
    serving: ServingCompute = ServingCompute()


class PlatformSpec(Strict):
    backend: Literal["local", "k8s", "skypilot"] = Field("local", description="local runs on this machine; k8s and skypilot are planned")
    storage: Storage
    database: Database = Database()
    gateway: Gateway = Gateway()
    components: Components = Components()
    tracing: Tracing | None = None
    observability: Observability = Observability()
    compute: Compute = Compute()
    telemetry: Literal["off", "on"] = Field("off", description="opt-in usage telemetry (none is sent today)")

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

class TracesSource(Strict):
    """Training data exported from a Trajectory lake with `cc export -format chat`."""
    lake: str | None = Field(None, description="default: Platform.tracing.lake")
    minReward: float | None = Field(None, description="keep only episodes scored at or above this")
    requireReward: bool = Field(False, description="keep only episodes a scorer has rewarded")
    requireFinal: bool = Field(False, description="keep only episodes whose outcome labels are final")
    verifier: str | None = Field(None, description="whose rewards to use, if the lake has several")
    asOf: str | None = Field(None, description="RFC 3339 time; fix it to rebuild a dataset exactly")


class DataSource(Strict):
    """Where a Model's data comes from. Set exactly one of jsonl, parquet, hf, traces."""
    jsonl: str | None = Field(None, description="path to a JSONL file: rows with messages, input or prompt; optional label")
    parquet: str | None = Field(None, description="path to a Parquet file or directory (needs ookami[parquet])")
    hf: str | None = Field(None, description="Hugging Face dataset id")
    traces: TracesSource | None = Field(None, description="traffic captured by Trajectory")
    match: dict[str, str] = Field({}, description="filter for traces sources, e.g. {route: /support}")

    @model_validator(mode="after")
    def _one(self) -> "DataSource":
        given = [k for k in ("jsonl", "parquet", "hf", "traces") if getattr(self, k) is not None]
        if len(given) != 1:
            raise ValueError("set exactly one of jsonl, parquet, hf, traces")
        return self


class Data(Strict):
    """Training and evaluation data."""
    source: DataSource
    minExamples: int = Field(500, ge=1, description="training refuses to start with fewer training rows")


class Train(Strict):
    """How to fine-tune. The memory planner fills in everything not overridden."""
    engine: Literal["auto", "mlx", "trl", "command"] = Field(
        "auto", description="auto: trl on NVIDIA GPUs, mlx on Apple silicon; command: your own trainer")
    command: str | None = Field(None, description="engine command: template with {job} (path to job.json)")
    recipe: Literal["sft", "dpo", "grpo", "sft-then-grpo"] = Field("sft", description="sft today; dpo and grpo are planned")
    reward: str | None = Field(None, description="python evaluator ref used as the RL reward, e.g. ./evals/reward.py:score")
    overrides: dict[str, Any] = Field({}, description="planner overrides: " + ", ".join(sorted(OVERRIDABLE)))

    @model_validator(mode="after")
    def _check(self) -> "Train":
        if self.engine == "command" and not self.command:
            raise ValueError("train.engine command needs train.command")
        if "grpo" in self.recipe and not self.reward:
            raise ValueError(f"recipe {self.recipe} needs train.reward")
        bad = set(self.overrides) - OVERRIDABLE
        if bad:
            raise ValueError(f"cannot override {sorted(bad)}; allowed: {sorted(OVERRIDABLE)}")
        return self


class Splits(Strict):
    """Hash-based splits: a row never changes split as data grows, and duplicate inputs share a split."""
    heldOut: float = Field(0.1, gt=0, lt=1, description="share of rows reserved for the gate, never trained on")
    audit: float = Field(0.05, ge=0, lt=1, description="second reserved share, never used for training or RL reward")
    stratifyBy: list[str] = Field([], description="row fields to report and gate per slice")
    seed: int = Field(0, description="changes which rows land in which split")
    refreshAfter: str | None = Field(None, description="e.g. 30d; a refresh creates a new split version")

    @model_validator(mode="after")
    def _room_to_train(self) -> "Splits":
        if self.heldOut + self.audit >= 0.5:
            raise ValueError("heldOut + audit must leave at least half the data for training")
        return self


class EvaluatorSpec(Strict):
    """One evaluator: labels, python, webhook, command, plugin, inspect, lm-eval, structural."""
    kind: Literal[EVALUATOR_KINDS] = Field(description="written as the single key, e.g. `- labels: {column: x}`")  # type: ignore[valid-type]
    params: dict[str, Any] = Field({}, description="the value under the kind key")
    name: str = Field(description="defaults from the kind; must be unique per Model")
    gate: bool = Field(True, description="false: reported only, does not block promotion")

    @model_validator(mode="before")
    @classmethod
    def _compact(cls, v: Any) -> Any:
        """Accept the compact YAML forms: `ookami/structural`, `{python: ./f.py:fn}`, `{labels: {column: x}}`."""
        if isinstance(v, str):
            if v == "ookami/structural":
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
    """The promotion test: candidate vs incumbent, paired by item."""
    vs: Literal["incumbent"] = Field("incumbent", description="the live version, or the base model when nothing is live")
    test: Literal["non-inferiority", "superiority", "threshold"] = Field(
        "non-inferiority", description="no worse by more than margin / better than / candidate mean above min")
    margin: float = Field(-0.01, le=0, description="largest drop vs the incumbent we accept")
    confidence: float = Field(0.95, gt=0.5, lt=1, description="one-sided bootstrap confidence")
    perSlice: bool = Field(True, description="also judge each stratifyBy slice")
    min: float | None = Field(None, description="absolute floor for the candidate's mean score")
    minItems: int = Field(20, ge=1, description="fewer paired items than this and the gate cannot pass")
    resamples: int = Field(10000, ge=1000, description="bootstrap resamples")

    @model_validator(mode="after")
    def _threshold(self) -> "Gate":
        if self.test == "threshold" and self.min is None:
            raise ValueError("test threshold needs gate.min")
        return self


class Generation(Strict):
    """Generation settings when Ookami produces outputs for row evaluators."""
    maxTokens: int = Field(1024, ge=1, description="max tokens per eval generation")
    temperature: float = Field(0.0, ge=0, description="sampling temperature for eval generations")


class Eval(Strict):
    """Evaluators, splits and the gate."""
    splits: Splits = Splits()
    generation: Generation = Generation()
    evaluators: list[EvaluatorSpec] = Field(min_length=1, description="at least one must gate")
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
    """The gateway route a Model takes over."""
    model: str = Field(description="the model name agents call today, as the gateway sees it")
    match: dict[str, str] = Field({}, description="request attributes that select the route")


class Rollback(Strict):
    """Automatic rollback rule (planned)."""
    metric: str = Field(description="an evaluator name")
    below: float = Field(description="roll back when the metric stays below this")
    window: str = Field("1h", description="how long it must stay below")


class Handoff(Strict):
    """Moving gateway traffic to the model: shadow, canary, live, rollback (planned)."""
    route: RouteMatch | None = Field(None, description="the gateway route this model takes over (hand-off is planned)")
    stages: list[str] = Field(["shadow", "canary:10%", "live"], description="shadow, canary:N% ascending, ending with live")
    approve: list[Literal["shadow", "canary", "live"]] = Field(["live"], description="stages that need a human")
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


class Provider(Strict):
    """An API model routed through the gateway next to your self-hosted models (any LiteLLM provider)."""
    name: str = Field(description="LiteLLM provider prefix, e.g. openai, anthropic, azure, bedrock, vertex_ai")
    model: str = Field(description="the provider's model id, e.g. gpt-5-mini")
    apiKey: str | None = Field(None, description="secret reference; ${secret:NAME} reads env var NAME locally")
    apiBase: str | None = Field(None, description="override the provider's base URL")

    @field_validator("apiKey")
    @classmethod
    def _key_is_secret(cls, v: str | None) -> str | None:
        return _secret(v)


class Serve(Strict):
    """How the model is served."""
    engine: Literal["auto", "vllm", "mlx", "command"] = Field(
        "auto", description="auto: vllm on NVIDIA GPUs, mlx on Apple silicon; command: any OpenAI-compatible server")
    command: str | None = Field(None, description="engine command: launch template with {weights} {port} {host} {name}")
    modelId: str | None = Field(None, description="engine command: the model id the server expects in requests")
    port: int | None = Field(None, ge=1, le=65535, description="engine port; default: first free from 8100")
    host: str = Field("127.0.0.1", description="bind address for the engine")
    args: list[str] = Field([], description="extra engine flags, e.g. [--max-model-len, '8192']")
    costPerHour: float | None = Field(None, ge=0, description="what the engine's hardware costs per hour (USD), "
                                                             "split across keys by token share in ookami usage")

    @model_validator(mode="after")
    def _command(self) -> "Serve":
        if self.engine == "command" and not self.command:
            raise ValueError("engine command needs serve.command")
        return self


class ModelSpec(Strict):
    base: str | None = Field(None, description="catalog name, or any name when weights and licence are set")
    provider: Provider | None = Field(None, description="route an API model instead of serving a base model")
    licence: str | None = Field(None, description="required when base is not in ookami's catalog")
    weights: str | None = Field(None, description="Hugging Face id or local path; defaults to the catalog's")
    serve: Serve = Serve()
    data: Data | None = Field(None, description="training and row-eval data; needed for train")
    train: Train | None = Field(None, description="omit to only serve the base model")
    eval: Eval | None = Field(None, description="required when train is set")
    handoff: Handoff = Field(Handoff(), description="gateway hand-off (planned)")

    @model_validator(mode="after")
    def _check(self) -> "ModelSpec":
        if (self.base is None) == (self.provider is None):
            raise ValueError("set exactly one of base (self-hosted) or provider (API model)")
        if self.provider is not None:
            if self.train or self.data:
                raise ValueError("an API model can't be trained here; set base to fine-tune an open model")
            return self
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
        """Paths in ookami.yaml are relative to the file."""
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
    if plat and plat.gateway.mode == "managed" and plat.gateway.auth == "none":
        out.append(Issue("warning", f"Platform/{cfg.platform.metadata.name}",
                         "gateway.auth none: anyone who can reach the gateway can use every model"))
    if plat and plat.components.tracing.enabled:
        where = f"Platform/{cfg.platform.metadata.name}"
        if not plat.tracing:
            out.append(Issue("error", where, "components.tracing needs Platform.tracing (the Trajectory collector)"))
        elif plat.tracing.mode == "managed" and not cfg.resolve(plat.tracing.config).exists():
            out.append(Issue("error", where, f"tracing.config not found: {plat.tracing.config}"))
        elif plat.gateway.mode == "external":
            out.append(Issue("warning", where, "tracing with an external gateway: point its LiteLLM generic_api "
                                               f"callback at {plat.tracing.webhook} yourself"))
    for name, m in cfg.models.items():
        where = f"Model/{name}"
        spec = m.spec
        if plat:
            c = plat.components
            needs = [("serving", spec.base is not None), ("training", spec.train is not None), ("eval", spec.eval is not None),
                     ("registry", spec.train is not None)]
            for comp, needed in needs:
                if needed and not getattr(c, comp).enabled:
                    out.append(Issue("error", where, f"needs the {comp} component; enable components.{comp}"))
            if spec.handoff.route and plat.gateway.mode == "none":
                out.append(Issue("error", where, "handoff.route needs a gateway (gateway.mode managed or external)"))
            tr = spec.data.source.traces if spec.data else None
            if tr is not None and not tr.lake and not plat.tracing:
                out.append(Issue("error", where, "data.source.traces needs a lake: set it, or Platform.tracing"))
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
        "title": "ookami.yaml document",
        "oneOf": [PlatformDoc.model_json_schema(), ModelDoc.model_json_schema()],
    }
