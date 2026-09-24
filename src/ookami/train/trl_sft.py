"""LoRA / QLoRA SFT on NVIDIA GPUs with Hugging Face TRL + PEFT. Run by the job worker:

    python -m ookami.train.trl_sft <job.json>

Writes a PEFT adapter (adapter_model.safetensors + adapter_config.json) to adapter_dir and checkpoints to
adapter_dir/checkpoints, resuming from the latest checkpoint when one exists.

Needs ookami[train-cuda]. Not yet exercised on a GPU in CI; the mlx path is the one tested end to end.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def main(job_path: str) -> None:
    import torch
    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    job = json.loads(Path(job_path).read_text())
    p = job["plan"]
    adapter_dir = Path(job["adapter_dir"])
    ckpt_dir = adapter_dir / "checkpoints"
    data_dir = Path(job["dataset_dir"])
    ds = load_dataset("json", data_files={"train": str(data_dir / "train.jsonl"),
                                          "validation": str(data_dir / "valid.jsonl")})

    # bf16 where the GPU has it (Ampere and newer); fp16 on older cards such as the T4
    bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    dtype = torch.bfloat16 if bf16 else torch.float16
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                               bnb_4bit_compute_dtype=dtype) if p["quantize"] else None
    tok = AutoTokenizer.from_pretrained(job["weights"])
    model = AutoModelForCausalLM.from_pretrained(job["weights"], torch_dtype=dtype,
                                                 quantization_config=quant, device_map="auto")
    lora = LoraConfig(r=p["rank"], lora_alpha=p["alpha"], lora_dropout=p["dropout"],
                      target_modules="all-linear", task_type="CAUSAL_LM")
    args = SFTConfig(
        # plan iters count micro-batches; TRL's max_steps counts optimizer steps (micro-batches / accumulation)
        output_dir=str(ckpt_dir), max_steps=max(1, math.ceil(p["iters"] / p["grad_accumulation"])),
        per_device_train_batch_size=p["batch_size"],
        gradient_accumulation_steps=p["grad_accumulation"], learning_rate=p["learning_rate"],
        gradient_checkpointing=p["grad_checkpoint"], bf16=bf16, fp16=not bf16, logging_steps=10,
        save_steps=max(10, p["iters"] // p["grad_accumulation"] // 10), save_total_limit=2, eval_strategy="steps",
        eval_steps=max(10, p["iters"] // p["grad_accumulation"] // 4), max_length=p["max_seq_len"], seed=p["seed"], report_to=[],
    )
    trainer = SFTTrainer(model=model, args=args, train_dataset=ds["train"], eval_dataset=ds["validation"],
                         processing_class=tok, peft_config=lora)
    has_ckpt = ckpt_dir.exists() and any(ckpt_dir.glob("checkpoint-*"))
    trainer.train(resume_from_checkpoint=True if has_ckpt else None)
    trainer.save_model(str(adapter_dir))


if __name__ == "__main__":
    main(sys.argv[1])
