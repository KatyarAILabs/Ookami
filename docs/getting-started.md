# Getting started

## Install

```bash
pip install "ookami[gateway] @ git+https://github.com/KatyarAILabs/Ookami.git"
# or, to work on Ookami itself:
git clone https://github.com/KatyarAILabs/Ookami.git && cd Ookami && uv sync --extra gateway
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
| `ookami[train-cuda]` | TRL training on NVIDIA |
| `ookami[parquet]` | Parquet data sources |
| `ookami[hf]` | Hugging Face data sources |

## 0. The fastest path

```bash
ookami init                       # writes ookami.yaml for this machine
ookami up
export OOKAMI_API_KEY=$(ookami keys master)
curl localhost:4000/v1/chat/completions -H "Authorization: Bearer $OOKAMI_API_KEY" \
  -H 'Content-Type: application/json' -d '{"model": "local", "messages": [{"role": "user", "content": "hi"}]}'
```

On an M3 Max with the model already downloaded, this took **10 seconds** from `init` to the first reply.

## 1. Serve an open model

```yaml
# ookami.yaml
apiVersion: ookami.dev/v1alpha1
kind: Platform
metadata: { name: my-box }
spec:
  storage: { uri: ./.ookami }
  components: { training: { enabled: false } }
---
apiVersion: ookami.dev/v1alpha1
kind: Model
metadata: { name: qwen-4b }
spec:
  base: qwen3-4b-instruct-2507
```

```bash
ookami validate                   # checks the file; errors and warnings, all at once
ookami up                         # engine + gateway in the background
curl localhost:4000/v1/chat/completions -H "Authorization: Bearer $(ookami keys master)" \
  -H 'Content-Type: application/json' -d '{"model": "qwen-4b", "messages": [{"role": "user", "content": "hi"}]}'
ookami status
ookami down
```

- **The engine is picked for you:** MLX on a Mac, vLLM on NVIDIA.
- **Other models:** any model outside the catalog works if you set `weights:` (a Hugging Face id or local path) and `licence:`.

## 2. Fine-tune, gate, and put it live

The quickstart in `examples/ticket-routing` teaches a model made-up queue codes that a base model can't know:

```bash
cd examples/ticket-routing
python make_data.py > tickets.jsonl
ookami train router               # snapshot -> LoRA -> gate vs the base model
ookami models router              # every version, its status and gate decision
ookami promote router             # refused unless the gate passed
ookami up                         # serves the live version on :4000
```

**What `ookami train` prints:**
- the split counts, including the held-out and audit rows it never trains on;
- the plan the memory planner chose;
- progress lines;
- the gate decision and the path to its report.

**Exit codes:**
- `0`: the new version passed;
- `3`: it was rejected;
- `1`: training failed.

## 3. Gate any two endpoints

`ookami eval` works without training. It compares two OpenAI-compatible endpoints on your own evaluators:

```bash
ookami eval -f examples/benchmark.yaml \
  --candidate openai:http://localhost:8000/v1#my-finetune \
  --incumbent openai:http://localhost:8001/v1#base-model
```

It's usable in CI: exit `0` means pass, `3` means fail.

## Next

- [Configuration reference](reference/ookami-yaml.md)
- [Evaluation](evaluation.md)
- [Training](training.md)
