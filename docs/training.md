# Training

`forge train MODEL` does four things, in this order.

## 1. Snapshot the data

`data.source` is one of:

| Source | Form |
|---|---|
| `jsonl` | A JSONL file |
| `parquet` | A Parquet file or directory (needs `forge-ml[parquet]`) |
| `hf` | A Hugging Face dataset, `name` or `name:split` (needs `forge-ml[hf]`) |
| `traces` | Traffic captured by Trajectory: `cc export -format chat` on the lake (see below) |

**Row format:**
- `messages` (chat), `input` or `prompt`;
- an optional `label`;
- an optional `id`;
- any other keys become metadata, which evaluators can read and `stratifyBy` can slice on.
- A trailing assistant message in `messages` becomes the **reference** answer.

**How the training split is written** (`data.snapshot`):
- **Assigning splits:** every row gets a split from a hash of its input. Duplicate inputs always land together, and a row never moves as the dataset grows.
- **What's excluded:** held-out and audit rows are never written.
- **Training target:** the reference message, or else the label as text. Rows with neither are skipped and counted.
- **Output:** a `train.jsonl`, plus a small `valid.jsonl` (used only for loss curves), written to `<storage>/datasets/<model>/<hash>/` with a manifest.
- **Minimum size:** training refuses to start when there are fewer training rows than `data.minExamples`.

### Training on captured traffic (Trajectory)

```yaml
data:
  source:
    traces: { minReward: 1 }        # lake defaults to Platform.tracing.lake
```

**What Forge runs:** `cc export -lake <lake> -format chat -require-final=false` before each snapshot, plus these filters when set:

| Field | Keeps |
|---|---|
| `minReward` | Episodes scored at or above this |
| `requireReward` | Only episodes a scorer has rewarded |
| `requireFinal` | Only episodes whose outcome labels are final |
| `verifier` | Rewards from this scorer, when the lake has several |
| `asOf` | The lake as it stood at this time |

**Each exported model call becomes one example:**
- the messages the model saw, with its answer as the reference;
- `episode_id`, `step_idx` and `reward` as metadata, so `stratifyBy` and evaluators can use them;
- ids of the form `<episode_id>:<step_idx>`.

**Why filter:** without a reward filter, every captured answer is trained on, including wrong ones. Load outcomes and score them in Trajectory (`cc outcomes`, `cc score`), then set `minReward`.

## 2. Plan

The memory planner (`train/planner.py`) picks LoRA settings that fit the machine.

**Memory it plans against:**
- Apple silicon: 75% of unified memory;
- NVIDIA: GPU 0's memory, from `nvidia-smi`.

**How it chooses:**
- **Estimates:** 16-bit LoRA needs about params×2×1.3 + 4 GB; 4-bit QLoRA needs about params×0.6×1.3 + 4 GB.
- **Pre-quantized weights** (4-bit, 8-bit, AWQ, GPTQ, MXFP4) are trained as QLoRA.
- **With 48 GB or more:** gradient checkpointing is off, and the batch is 2×4 instead of 1×8.

**Defaults:**

| Setting | Default |
|---|---|
| rank | 16 |
| alpha | 2 × rank |
| dropout | 0.05 |
| layers (MLX) | 16 |
| learning rate | 2e-4 |
| epochs | 1 |
| iters | epochs × rows ÷ batch |
| max sequence length | 4096 |

**Overrides** (`train.overrides`): `lora.rank`, `lora.alpha`, `lora.dropout`, `lora.layers`, `epochs`, `iters`, `learning_rate`, `max_seq_len`, `batch_size`, `grad_accumulation`, `seed`, `quantize`. Any other key is an error.

The plan and its reasoning are printed before training starts.

## 3. Queue and train

- **Queue:** jobs go to `<storage>/jobs/`. `forge train` starts a background worker if none is running.
- **One job at a time:** the worker holds `jobs/worker.lock` and runs jobs one after another, so peak memory stays at one job's size.

| Trainer (`train.engine`) | Runs | Writes |
|---|---|---|
| `mlx` | `mlx_lm.lora -c jobs/<id>/mlx.yaml` (LoRA, prompt masked, checkpoints every ~10%) | `adapters.safetensors`, then `fused/` |
| `trl` | `python -m forge.train.trl_sft job.json`: TRL `SFTTrainer` + PEFT LoRA on all linear layers; 4-bit QLoRA when planned; bf16 where supported, else fp16 | PEFT adapter, `checkpoints/` |
| `command` | Your `train.command` with `{job}` (the path to `job.json`) | Whatever your trainer writes to `adapter_dir` |
| `auto` | `mlx` on Apple silicon, `trl` on NVIDIA | - |

**`job.json` contents:**
- `weights`
- `dataset_dir`
- `adapter_dir`
- `plan`
- `trainer`
- the Model and version

**Interruptions:** a job left in `running` with no worker is re-queued when the next worker starts. On the next attempt:
- MLX resumes from `adapters.safetensors`;
- TRL resumes from its latest checkpoint.

## 4. Gate and record

- **Serve both sides:** after training (and fusing, for MLX), the worker serves the new version and the incumbent side by side:
  - vLLM: one process with both adapters;
  - MLX or command: two processes.
- **Evaluate:** it runs the Model's evaluators on held-out and audit rows (see [evaluation](evaluation.md)).
- **Record:** it writes the report and sets the version to `passed` or `rejected`.
- **Next step:** `forge promote` puts a passing version live.

## Statuses

| Status | Meaning |
|---|---|
| `training` | Queued or running |
| `evaluating` | Gate running |
| `passed` / `rejected` | Gate decision |
| `failed` | Trainer crashed, wrote no adapter, or the gate couldn't run |
| `promoted` | Live |
| `retired` | Was live; replaced |

**Recipes:** `sft` is implemented. `dpo`, `grpo` and `sft-then-grpo` pass validation but are refused at run time until 0.3.
