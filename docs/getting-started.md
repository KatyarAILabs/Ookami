# Getting started

## Install

```bash
git clone <repo> forge && cd forge
uv sync --extra gateway          # or: pip install 'forge-ml[gateway]'
```

Install an inference engine for your hardware:

| Hardware | Engine | Install |
|---|---|---|
| NVIDIA GPU (Linux) | vLLM | `pip install vllm` |
| Apple silicon | MLX | `pip install mlx-lm` |
| Anything else | Any OpenAI-compatible server (llama.cpp, SGLang, TGI) | Set `serve.engine: command` |

Optional extras:

| Extra | For |
|---|---|
| `forge-ml[train-cuda]` | TRL training on NVIDIA |
| `forge-ml[parquet]` | Parquet data sources |
| `forge-ml[hf]` | Hugging Face data sources |

## 1. Serve an open model

```yaml
# forge.yaml
apiVersion: forge.dev/v1alpha1
kind: Platform
metadata: { name: my-box }
spec:
  storage: { uri: ./.forge }
  components: { training: { enabled: false } }
---
apiVersion: forge.dev/v1alpha1
kind: Model
metadata: { name: qwen-4b }
spec:
  base: qwen3-4b-instruct-2507
```

```bash
forge validate                   # checks the file; errors and warnings, all at once
forge up                         # engine + gateway in the background
curl localhost:4000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model": "qwen-4b", "messages": [{"role": "user", "content": "hi"}]}'
forge status
forge down
```

- **The engine is picked for you:** MLX on a Mac, vLLM on NVIDIA.
- **Other models:** any model outside the catalog works if you set `weights:` (a Hugging Face id or local path) and `licence:`.

## 2. Fine-tune, gate, and put it live

The quickstart in `examples/ticket-routing` teaches a model made-up queue codes that a base model can't know:

```bash
cd examples/ticket-routing
python make_data.py > tickets.jsonl
forge train router               # snapshot -> LoRA -> gate vs the base model
forge models router              # every version, its status and gate decision
forge promote router             # refused unless the gate passed
forge up                         # serves the live version on :4000
```

**What `forge train` prints:**
- the split counts, including the held-out and audit rows it never trains on;
- the plan the memory planner chose;
- progress lines;
- the gate decision and the path to its report.

**Exit codes:**
- `0`: the new version passed;
- `3`: it was rejected;
- `1`: training failed.

## 3. Gate any two endpoints

`forge eval` works without training. It compares two OpenAI-compatible endpoints on your own evaluators:

```bash
forge eval -f examples/benchmark.yaml \
  --candidate openai:http://localhost:8000/v1#my-finetune \
  --incumbent openai:http://localhost:8001/v1#base-model
```

It's usable in CI: exit `0` means pass, `3` means fail.

## Next

- [Configuration reference](reference/forge-yaml.md)
- [Evaluation](evaluation.md)
- [Training](training.md)
