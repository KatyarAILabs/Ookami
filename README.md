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

> **Status: 0.2, early.** What works today, on one machine:
> - `forge up` / `status` / `logs` / `down`: open models served by vLLM (NVIDIA), MLX (Apple silicon) or any OpenAI-compatible server, behind a managed LiteLLM gateway;
> - `forge train`: versioned data snapshot, memory-planned LoRA fine-tune (MLX or TRL), one job at a time, checkpoints and resume, then an automatic gate against the live version;
> - `forge models` / `promote`: a registry that refuses to put a version live unless it passed its gate;
> - `forge eval` with your own evaluators or plugins.
>
> - tracing with [Trajectory](https://github.com/KatyarAILabs/trajectory): the managed gateway sends every call to the collector, and `data.source.traces` trains on its lake.
>
> Gateway hand-off (shadow/canary/rollback) and GRPO are next. See [docs/plan.md](docs/plan.md).

## Quickstart: fine-tune, gate and serve on one machine

```bash
cd examples/ticket-routing && python make_data.py > tickets.jsonl
forge train router        # snapshot -> LoRA -> gate vs the base model
forge promote router      # refused unless the gate passed
forge up                  # serves the live version behind the gateway on :4000
```

On an M3 Max, `forge train` took about 6 minutes (Qwen3-4B, 4-bit, 2 epochs). The gate scored the trained version at 82% on held-out rows and 93% on audit rows, against 0% for the base model; the queue codes are made up, so the base can't know them.

## How it fits together

```mermaid
flowchart LR
    app["your apps"] -->|"OpenAI API"| gw["gateway"]
    gw --> eng["engines<br/>your GPUs"]
    gw -.-> traj["Trajectory<br/>capture"]
    traj -.-> data[("data")]
    data --> train["fine-tune"] --> gate{"gate<br/>your evals"}
    gate -->|"pass + promote"| eng
```

## Components

| Component | Default | Built on |
|---|---|---|
| gateway | managed (or `external`: bring your own) | LiteLLM / Agent Router |
| serving | on | vLLM multi-LoRA, KEDA scale-to-zero |
| training | on | TRL (SFT/DPO), prime-rl (GRPO), Kueue |
| eval | on | Forge gate + your evaluators |
| registry | on | Postgres + your bucket |
| tracing | off | [Trajectory](https://github.com/KatyarAILabs/trajectory): gateway callbacks → redacted Parquet lake |
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

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Install, serve, fine-tune, put live |
| [Architecture](docs/architecture.md) | Components, interfaces, lifecycle, on-disk layout |
| [forge.yaml reference](docs/reference/forge-yaml.md) | Every field, generated from the code |
| [CLI](docs/cli.md) · [Serving](docs/serving.md) · [Training](docs/training.md) · [Evaluation](docs/evaluation.md) · [Plugins](docs/plugins.md) | How each part works |
| [Deployment](docs/deployment.md) · [Verification log](docs/verification.md) | Where it runs, what has been tested |
| [Plan](docs/plan.md) | Why Forge exists, roadmap |

Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). Licensed under Apache-2.0.
