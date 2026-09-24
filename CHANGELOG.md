# Changelog

## 0.2.0 (unreleased)

- **Tracing through Trajectory:**
  - `Platform.tracing` runs or points at a Trajectory collector;
  - the managed gateway sends every completion to it through LiteLLM's `generic_api` callback;
  - `data.source.traces` trains on the lake via `cc export -format chat`, with reward, finality, verifier and as-of filters.

- **`forge train`:**
  - versioned data snapshots (held-out and audit rows never trained on);
  - memory planner;
  - trainers: MLX, TRL (LoRA / QLoRA, bf16 or fp16) or any command;
  - one-job background queue with checkpoint and resume;
  - an automatic gate against the live version.
- **Registry** (`forge models`, `forge promote`): versions, gate decisions, event log. Promotion is refused without a passing gate unless forced with a reason.
- **`forge up`** serves each Model's live version. `promote` restarts the running engine.
- **Data sources:** Parquet and Hugging Face.
- **`eval.generation`** (max tokens, temperature).
- **Reports** warn when candidate and incumbent outputs are ≥95% identical.
- **Fix:** MLX trained versions are served as fused models (mlx-lm 0.31 ignores `--adapter-path` on the server).
- **Deployment kits:** Kubernetes CPU smoke test, Kubernetes GPU, AWS spot GPU.

## 0.1.0 (unreleased)

- `forge up`, `status`, `logs`, `down`: engines (vLLM, MLX, command) behind a managed LiteLLM gateway.
- Model catalog with licences; any model via `weights` + `licence`.
- Evaluator plugins via the `forge.evaluators` entry point.

## 0.0.1 (unreleased)

- The `forge.yaml` schema, `forge validate` and `forge schema`.
- Backend interfaces.
- Evaluator SDK: labels, python, webhook, command, structural.
- `forge eval`: frozen splits, paired bootstrap, non-inferiority, superiority and threshold tests, reports.
