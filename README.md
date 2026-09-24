# Forge (working name)

**Open-source, self-hosted AI infrastructure in one package.** Gateway, model serving, fine-tuning, eval gates, model registry, trace capture and GPU autoscaling ship as one install and are driven by one config file. You switch on only what you need. It runs on one GPU box, on any Kubernetes cluster, in your cloud account, or air-gapped.

```yaml
# forge.yaml: serve an open model on your GPU behind an OpenAI-compatible gateway
apiVersion: forge.dev/v1alpha1
kind: Platform
metadata: { name: my-box }
spec:
  storage: { uri: ./.forge }
  components: { training: { enabled: false } }
---
apiVersion: forge.dev/v1alpha1
kind: Model
metadata: { name: gpt-oss }
spec:
  base: gpt-oss-20b
```

> **Status: 0.1, early.** What works today:
> - `forge up` / `status` / `logs` / `down` on one machine: open models served by vLLM (NVIDIA), MLX (Apple silicon) or any OpenAI-compatible server, behind a managed LiteLLM gateway;
> - the config schema, `forge validate` and `forge schema`;
> - `forge eval`, the promotion gate, with evaluator plugins.
>
> Training and gateway hand-off are next. See [docs/plan.md](docs/plan.md).

## Components

| Component | Default | Built on |
|---|---|---|
| gateway | managed (or `external`: bring your own) | LiteLLM / Agent Router |
| serving | on | vLLM multi-LoRA, KEDA scale-to-zero |
| training | on | TRL (SFT/DPO), prime-rl (GRPO), Kueue |
| eval | on | Forge gate + your evaluators |
| registry | on | Postgres + your bucket |
| tracing | off | OTel collector → Parquet |
| console | off | Forge UI |

## Try it

```bash
uv sync --extra gateway                              # or: pip install 'forge-ml[gateway]'
# engines are installed separately: pip install vllm (NVIDIA) or pip install mlx-lm (Apple silicon)
uv run forge up -f examples/serve-only.yaml           # model + gateway on this machine
curl localhost:4000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model": "gpt-oss", "messages": [{"role": "user", "content": "hi"}]}'
uv run forge down -f examples/serve-only.yaml

uv run forge validate -f examples/forge.yaml          # full loop on EKS
uv run forge validate -f examples/serve-only.yaml     # one GPU box
uv run forge schema > forge.schema.json               # editor autocomplete

# Gate two OpenAI-compatible endpoints on your own benchmark:
uv run forge eval -f examples/benchmark.yaml \
  --candidate openai:http://localhost:8000/v1#my-finetune \
  --incumbent openai:http://localhost:8001/v1#base-model
uv run pytest
```

## Evaluation: you bring the judgement, Forge brings the statistics

- **Evaluators:**
  - `labels`: a column in your data;
  - `python`: a function decorated with `@evaluator`;
  - `webhook`;
  - `command`: any harness that writes `{"item_id", "score"}` JSONL;
  - `plugin`: evaluators shipped as separate packages through the `forge.evaluators` entry point;
  - `lm-eval` and `inspect`: planned;
  - `forge/structural`: JSON and tool-call shape only.
- **What Forge adds:**
  - frozen held-out and audit splits (duplicates never straddle splits);
  - paired bootstrap confidence intervals per slice;
  - non-inferiority, superiority and threshold tests;
  - a report for every run.
- **Exit codes:** `0` means the gate passed, `3` means it did not pass, `1` means an error. This makes it drop-in for CI.
- **Built-in warnings:**
  - the RL reward is reused as a gate;
  - the gate uses structural checks alone;
  - shadow mode on multi-turn agents with side effects.

```python
from forge import Score, evaluator

@evaluator(name="task_check", version="2")
def score(example, output) -> Score:
    ok = output["content"].strip() == example.label
    return Score(float(ok), passed=ok)
```

## Layout

| Path | What |
|---|---|
| `src/forge/config.py` | `forge.yaml` schema (Pydantic) and lints. It is also the source for JSON Schema and, later, the CRDs |
| `src/forge/catalog.py` | Known base models: licence and weights per engine |
| `src/forge/local/` | The local backend: engines (vllm, mlx, command), the managed gateway, process supervision |
| `src/forge/interfaces.py` | `JobRunner`, `ModelServer`, `Router`: the seams each backend implements |
| `src/forge/evaluators/` | Evaluator SDK and built-ins |
| `src/forge/data.py` | Datasets, frozen splits, getting outputs from targets |
| `src/forge/stats.py` | Paired bootstrap and gate tests |
| `src/forge/eval_runner.py` | `forge eval` and reports |

Apache-2.0.
