# forge.yaml reference

_Generated from `src/forge/config.py` by `scripts/gen_config_reference.py`. Do not edit by hand._

A `forge.yaml` holds YAML documents, each with `apiVersion: forge.dev/v1alpha1`, a `kind`, `metadata.name` (lowercase letters, digits and `-`) and a `spec`. Unknown keys are errors.

## kind: Platform

### PlatformSpec

| Field | Type | Default | Description |
|---|---|---|---|
| `backend` | `local` \| `k8s` \| `skypilot` | `local` | local runs on this machine; k8s and skypilot are planned |
| `storage` | [Storage](#storage) | **required** | Where datasets, checkpoints, adapters, reports and the registry live. |
| `database` | [Database](#database) | see below | State database. SQLite in storage for the local backend; Postgres on k8s. |
| `gateway` | [Gateway](#gateway) | see below | The OpenAI-compatible gateway in front of every model. |
| `components` | [Components](#components) | see below | Switch on what you need. Each component also works on its own. |
| `compute` | [Compute](#compute) | see below | What compute to use and where it comes from. |
| `telemetry` | `off` \| `on` | `off` | opt-in usage telemetry (none is sent today) |

### Storage

Where datasets, checkpoints, adapters, reports and the registry live.

| Field | Type | Default | Description |
|---|---|---|---|
| `uri` | str | **required** | s3://, gs://, az://, file:// or a local path |

### Database

State database. SQLite in storage for the local backend; Postgres on k8s.

| Field | Type | Default | Description |
|---|---|---|---|
| `url` | str (optional) | - | Postgres URL (a secret reference) or sqlite:///path; default SQLite in storage |

### Gateway

The OpenAI-compatible gateway in front of every model.

| Field | Type | Default | Description |
|---|---|---|---|
| `mode` | `managed` \| `external` \| `none` | `managed` | managed: forge runs the gateway; external: use yours; none: no gateway |
| `type` | `litellm` \| `agent-router` | `litellm` | gateway implementation |
| `url` | str (optional) | - | required for an external gateway |
| `adminKey` | str (optional) | - | secret reference for the gateway's admin API |
| `port` | int | `4000` | managed gateway port |
| `command` | str (optional) | - | advanced: launch template for a managed gateway, with {config} {port} {host} |

### Components

Switch on what you need. Each component also works on its own.

| Field | Type | Default | Description |
|---|---|---|---|
| `serving` | [Component](#component) | see below | inference engines (vLLM, MLX or a command); on by default |
| `training` | [Component](#component) | see below | fine-tuning queue and trainers; on by default |
| `eval` | [Component](#component) | see below | held-out sets, the promotion gate, reports; on by default |
| `registry` | [Component](#component) | see below | versions, gate decisions, promotions; on by default |
| `tracing` | [Component](#component) | see below | capture gateway traffic into storage (planned); off by default |
| `console` | [Component](#component) | see below | web UI (planned); off by default |

### Component

Turn a component on or off.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `True` | run this component |

### Compute

What compute to use and where it comes from.

| Field | Type | Default | Description |
|---|---|---|---|
| `profile` | `local` \| `vm` \| `cloud-eks` \| `cloud-gke` \| `cloud-aks` \| `onprem` | `local` | where compute comes from; picks the autoscaler wiring on k8s |
| `training` | [TrainingCompute](#trainingcompute) | see below | Compute for training jobs. |
| `serving` | [ServingCompute](#servingcompute) | see below | Compute for inference engines. |

### TrainingCompute

Compute for training jobs.

| Field | Type | Default | Description |
|---|---|---|---|
| `gpu` | str | `auto` | GPU type for training nodes (cloud backends) |
| `count` | int | `1` | GPUs per training job |
| `capacity` | list of `spot` \| `on-demand` \| `reserved` | `['spot', 'on-demand']` | capacity types to try, in order |
| `budgetHoursPerWeek` | float (optional) | - | GPU-hour budget the queue schedules within (planned) |

### ServingCompute

Compute for inference engines.

| Field | Type | Default | Description |
|---|---|---|---|
| `gpu` | str | `auto` | GPU type for serving nodes (cloud backends) |
| `scaleToZero` | bool | `True` | scale engines to zero when idle (k8s, planned) |
| `coldFallback` | `incumbent` \| `error` | `incumbent` | what the gateway does while a model is cold (planned) |

## kind: Model

### ModelSpec

| Field | Type | Default | Description |
|---|---|---|---|
| `base` | str | **required** | catalog name, or any name when weights and licence are set |
| `licence` | str (optional) | - | required when base is not in forge's catalog |
| `weights` | str (optional) | - | Hugging Face id or local path; defaults to the catalog's |
| `serve` | [Serve](#serve) | see below | How the model is served. |
| `data` | [Data](#data) (optional) | - | training and row-eval data; needed for train |
| `train` | [Train](#train) (optional) | - | omit to only serve the base model |
| `eval` | [Eval](#eval) (optional) | - | required when train is set |
| `handoff` | [Handoff](#handoff) | see below | gateway hand-off (planned) |

### Serve

How the model is served.

| Field | Type | Default | Description |
|---|---|---|---|
| `engine` | `auto` \| `vllm` \| `mlx` \| `command` | `auto` | auto: vllm on NVIDIA GPUs, mlx on Apple silicon; command: any OpenAI-compatible server |
| `command` | str (optional) | - | engine command: launch template with {weights} {port} {host} {name} |
| `modelId` | str (optional) | - | engine command: the model id the server expects in requests |
| `port` | int (optional) | - | engine port; default: first free from 8100 |
| `host` | str | `127.0.0.1` | bind address for the engine |
| `args` | list of str | `[]` | extra engine flags, e.g. [--max-model-len, '8192'] |

### Data

Training and evaluation data.

| Field | Type | Default | Description |
|---|---|---|---|
| `source` | [DataSource](#datasource) | **required** | Where a Model's data comes from. Set exactly one of jsonl, parquet, hf, traces. |
| `minExamples` | int | `500` | training refuses to start with fewer training rows |

### DataSource

Where a Model's data comes from. Set exactly one of jsonl, parquet, hf, traces.

| Field | Type | Default | Description |
|---|---|---|---|
| `jsonl` | str (optional) | - | path to a JSONL file: rows with messages, input or prompt; optional label |
| `parquet` | str (optional) | - | path to a Parquet file or directory (needs forge-ml[parquet]) |
| `hf` | str (optional) | - | Hugging Face dataset id |
| `traces` | `gateway` (optional) | - | traffic captured by the tracing component |
| `match` | map | `{}` | filter for traces sources, e.g. {route: /support} |

### Train

How to fine-tune. The memory planner fills in everything not overridden.

| Field | Type | Default | Description |
|---|---|---|---|
| `engine` | `auto` \| `mlx` \| `trl` \| `command` | `auto` | auto: trl on NVIDIA GPUs, mlx on Apple silicon; command: your own trainer |
| `command` | str (optional) | - | engine command: template with {job} (path to job.json) |
| `recipe` | `sft` \| `dpo` \| `grpo` \| `sft-then-grpo` | `sft` | sft today; dpo and grpo are planned |
| `reward` | str (optional) | - | python evaluator ref used as the RL reward, e.g. ./evals/reward.py:score |
| `overrides` | map | `{}` | planner overrides: batch_size, epochs, grad_accumulation, iters, learning_rate, lora.alpha, lora.dropout, lora.layers, lora.rank, max_seq_len, quantize, seed |

### Eval

Evaluators, splits and the gate.

| Field | Type | Default | Description |
|---|---|---|---|
| `splits` | [Splits](#splits) | see below | Hash-based splits: a row never changes split as data grows, and duplicate inputs share a split. |
| `generation` | [Generation](#generation) | see below | Generation settings when Forge produces outputs for row evaluators. |
| `evaluators` | list of [EvaluatorSpec](#evaluatorspec) | **required** | at least one must gate |
| `gate` | [Gate](#gate) | see below | The promotion test: candidate vs incumbent, paired by item. |

### Splits

Hash-based splits: a row never changes split as data grows, and duplicate inputs share a split.

| Field | Type | Default | Description |
|---|---|---|---|
| `heldOut` | float | `0.1` | share of rows reserved for the gate, never trained on |
| `audit` | float | `0.05` | second reserved share, never used for training or RL reward |
| `stratifyBy` | list of str | `[]` | row fields to report and gate per slice |
| `seed` | int | `0` | changes which rows land in which split |
| `refreshAfter` | str (optional) | - | e.g. 30d; a refresh creates a new split version |

### Generation

Generation settings when Forge produces outputs for row evaluators.

| Field | Type | Default | Description |
|---|---|---|---|
| `maxTokens` | int | `1024` | max tokens per eval generation |
| `temperature` | float | `0.0` | sampling temperature for eval generations |

### EvaluatorSpec

One evaluator: labels, python, webhook, command, plugin, structural (lm-eval, inspect planned).

| Field | Type | Default | Description |
|---|---|---|---|
| `kind` | `labels` \| `python` \| `webhook` \| `command` \| `plugin` \| `lm-eval` \| `inspect` \| `structural` | **required** | written as the single key, e.g. `- labels: {column: x}` |
| `params` | map | `{}` | the value under the kind key |
| `name` | str | **required** | defaults from the kind; must be unique per Model |
| `gate` | bool | `True` | false: reported only, does not block promotion |

### Gate

The promotion test: candidate vs incumbent, paired by item.

| Field | Type | Default | Description |
|---|---|---|---|
| `vs` | `incumbent` | `incumbent` | the live version, or the base model when nothing is live |
| `test` | `non-inferiority` \| `superiority` \| `threshold` | `non-inferiority` | no worse by more than margin / better than / candidate mean above min |
| `margin` | float | `-0.01` | largest drop vs the incumbent we accept |
| `confidence` | float | `0.95` | one-sided bootstrap confidence |
| `perSlice` | bool | `True` | also judge each stratifyBy slice |
| `min` | float (optional) | - | absolute floor for the candidate's mean score |
| `minItems` | int | `20` | fewer paired items than this and the gate cannot pass |
| `resamples` | int | `10000` | bootstrap resamples |

### Handoff

Moving gateway traffic to the model: shadow, canary, live, rollback (planned).

| Field | Type | Default | Description |
|---|---|---|---|
| `route` | [RouteMatch](#routematch) (optional) | - | the gateway route this model takes over (hand-off is planned) |
| `stages` | list of str | `['shadow', 'canary:10%', 'live']` | shadow, canary:N% ascending, ending with live |
| `approve` | list of `shadow` \| `canary` \| `live` | `['live']` | stages that need a human |
| `rollback` | [Rollback](#rollback) (optional) | - | Automatic rollback rule (planned). |
| `shadowExecutor` | str (optional) | - | python ref that replays side-effecting calls in a sandbox |

### RouteMatch

The gateway route a Model takes over.

| Field | Type | Default | Description |
|---|---|---|---|
| `model` | str | **required** | the model name agents call today, as the gateway sees it |
| `match` | map | `{}` | request attributes that select the route |

### Rollback

Automatic rollback rule (planned).

| Field | Type | Default | Description |
|---|---|---|---|
| `metric` | str | **required** | an evaluator name |
| `below` | float | **required** | roll back when the metric stays below this |
| `window` | str | `1h` | how long it must stay below |
