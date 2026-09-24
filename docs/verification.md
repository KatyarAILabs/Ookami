# Verification log

This page records what has been run, where, and what happened. It's updated whenever an environment is tested.

## Automated tests

- **Command:** `uv run pytest`: 57 tests, about 20 seconds, with no GPU or network needed.
- **Fakes:** a fake OpenAI-compatible server (`tests/fake_server.py`) and a fake trainer (`tests/fake_trainer.py`). The fake server answers from what the fake trainer "learned", so a trained version can be made better or worse.
- **Coverage:**
  - schema and lints;
  - splits and snapshots;
  - statistics;
  - every built-in evaluator and plugin loading;
  - engine command lines;
  - process supervision (up, status, down, failure cleanup);
  - the train → gate → promote → serve loop with its failure paths;
  - job recovery;
  - the registry's promotion rules.

## Environments

| Date | Environment | What ran | Result |
|---|---|---|---|
| 2026-09-25 | MacBook, M3 Max, 36 GB, MLX 0.31.3 | `ookami up`: Qwen3-4B-Instruct-2507 (4-bit) behind LiteLLM | **Pass.** Chat through the gateway answered correctly. `ookami eval` gateway vs direct engine passed. `ookami down` freed the ports |
| 2026-09-25 | Same | `examples/ticket-routing`: `ookami train` → gate → `promote` → `ookami up` | **Pass after one fix** (below). Trained v3 scored **81.7%** on held-out (n=93) and **92.5%** on audit (n=40), against **0%** for the base model. Training took about 5 minutes. `promote` refused the rejected v2. Gateway routing was correct on 3 of 3 checks |
| 2026-09-25 | AKS (Kubernetes 1.34), CPU nodes | `deploy/k8s-smoke`: wheel install in a pod, llama.cpp + Qwen2.5-0.5B, LiteLLM, `ookami eval` | **Pass** after adding `LD_LIBRARY_PATH=/app` for the llama.cpp image. The gateway answered "Paris."; the gate passed. The namespace was deleted afterwards |
| 2026-09-25 | MacBook, M3 Max, Trajectory collector (real `cc`), MLX, LiteLLM | `ookami up` with managed tracing; 3 calls in 2 sessions through the gateway; `data.source.traces` export | **Pass.** Collector, engine and gateway came up. The gateway's `generic_api` callback reached the collector. The lake got episodes and steps partitioned by `task_type=support`. The two calls of one session formed one episode. Ookami built 3 examples with ids `<episode>:<step>` |
| 2026-09-25 | MacBook, real LiteLLM proxy, fake engine | Gateway auth: no key, bad key, master, rpm, budget, revoke; `ookami usage` | **Pass:** 401 / 401 / 200 / 200-200-429 / 429 `budget_exceeded` / 401 after revoke; usage recorded per key and team. `langfuse_otel` enabled with an unreachable host: calls still 200 |
| 2026-09-25 | MacBook, MLX Qwen3-4B | `ookami init` → `up` → first authenticated call | **10 s** with cached weights |
| 2026-09-25 | MacBook, MLX Qwen3-4B behind the authenticated gateway | `ookami eval` with Inspect (arithmetic, 20 samples) and lm-eval (GSM8K, 20 problems) | **Pass:** Inspect 20/20, GSM8K 65% (strict-match), identical on both sides as expected for the same model. Found and fixed: inspect-ai and LiteLLM need incompatible `openai` versions (harnesses now run through uvx), and Inspect rejects absolute task paths |
| - | NVIDIA GPU (vLLM + TRL) | `deploy/aws-gpu` or `deploy/k8s-gpu` | **Not yet run.** The Azure subscription has 0 GPU quota; the AWS GPU quota request is pending |

## Issues found by running on real hardware

| Issue | How it showed up | Fix |
|---|---|---|
| mlx-lm 0.31's server ignores `--adapter-path` unless each request also passes `adapters` | The gate scored the trained model and the base identically (0% vs 0%), even though `mlx_lm.generate` with the same adapter answered correctly | After MLX training, fuse the adapter (`mlx_lm.fuse`) and serve the fused model; clients send `default_model` |
| The quickstart was undertrained | 300 iterations with 8-step gradient accumulation was under half an epoch. The gate correctly rejected v1 | The quickstart uses 2 epochs; the default learning rate is 2e-4 |
| A system LiteLLM without its proxy extras | The gateway crashed at start (`No module named 'fastapi'`) | Ship the `ookami[gateway]` extra and prefer the LiteLLM installed next to Ookami |
| TRL assumed bf16 | Would fail on T4 and older GPUs | bf16 only where `torch.cuda.is_bf16_supported()`; fp16 otherwise |
| Rows flagged `error` were dropped before being counted | Found by the tests | Count errors before skipping rows without a score |

## Safeguards added because of these issues

- **Identical outputs:** the report warns when the candidate and incumbent gave identical outputs on ≥95% of rows. This would have caught the MLX adapter bug immediately.

## Known limitations

| Limitation | Status |
|---|---|
| The vLLM LoRA serving path and the TRL trainer haven't run on a GPU | GPU test kits ready (`deploy/aws-gpu`, `deploy/k8s-gpu`) |
| Promotion restarts the engine; no runtime adapter loading | Planned for 0.3 (vLLM) |
| Nothing coordinates GPU use between serving and training on one machine | Planned with the queue's resource budget |
| `ookami up` checks health only at start; no restart on crash | Planned |
| Row evaluators and `command` evaluators in the same Model replaying one `results:` file | Supported, but both need their row types in the same file |
