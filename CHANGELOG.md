# Changelog

## 0.3.0 (unreleased)

- **Renamed to Ookami:**
  - package, CLI and import `ookami`;
  - `ookami.yaml` with `apiVersion: ookami.dev/v1alpha1`;
  - `OOKAMI_*` environment variables;
  - `ook-` key prefix.
- **Gateway auth on by default:**
  - hashed keys in SQLite (no Postgres), plus a generated master key;
  - per-key monthly budgets and requests-per-minute limits;
  - `ookami keys` to manage them.
- **`ookami usage`:** spend per key or team, with API cost and self-hosted hardware cost (`serve.costPerHour`) split by token share.
- **API models** (`provider:`) routed next to self-hosted ones.
- **`ookami init`:** a working config for Apple silicon, NVIDIA or CPU.
- **New evaluators:** Inspect and lm-evaluation-harness, run in isolated environments via uvx.
- **Langfuse trace UI** over OpenTelemetry (`observability.langfuse`, `ookami[observability]`).
- **Security:** requires LiteLLM ≥1.83, since 1.82.7 and 1.82.8 on PyPI were backdoored.

## 0.2.0 (unreleased)

- **Tracing through Trajectory:**
  - `Platform.tracing` runs or points at a Trajectory collector;
  - the managed gateway sends every completion to it through LiteLLM's `generic_api` callback;
  - `data.source.traces` trains on the lake via `cc export -format chat`, with reward, finality, verifier and as-of filters.

- **`ookami train`:**
  - versioned data snapshots (held-out and audit rows never trained on);
  - memory planner;
  - trainers: MLX, TRL (LoRA / QLoRA, bf16 or fp16) or any command;
  - one-job background queue with checkpoint and resume;
  - an automatic gate against the live version.
- **Registry** (`ookami models`, `ookami promote`): versions, gate decisions, event log. Promotion is refused without a passing gate unless forced with a reason.
- **`ookami up`** serves each Model's live version. `promote` restarts the running engine.
- **Data sources:** Parquet and Hugging Face.
- **`eval.generation`** (max tokens, temperature).
- **Reports** warn when candidate and incumbent outputs are ≥95% identical.
- **Fix:** MLX trained versions are served as fused models (mlx-lm 0.31 ignores `--adapter-path` on the server).
- **Deployment kits:** Kubernetes CPU smoke test, Kubernetes GPU, AWS spot GPU.

## 0.1.0 (unreleased)

- `ookami up`, `status`, `logs`, `down`: engines (vLLM, MLX, command) behind a managed LiteLLM gateway.
- Model catalog with licences; any model via `weights` + `licence`.
- Evaluator plugins via the `ookami.evaluators` entry point.

## 0.0.1 (unreleased)

- The `ookami.yaml` schema, `ookami validate` and `ookami schema`.
- Backend interfaces.
- Evaluator SDK: labels, python, webhook, command, structural.
- `ookami eval`: frozen splits, paired bootstrap, non-inferiority, superiority and threshold tests, reports.
