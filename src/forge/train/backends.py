"""Trainer backends. Each turns a job into one process that writes a LoRA adapter to the adapter directory.

  mlx      Apple silicon, mlx-lm (LoRA / QLoRA)
  trl      NVIDIA GPUs, Hugging Face TRL + PEFT (LoRA / QLoRA)   [forge-ml[train-cuda]]
  command  your own trainer: a template with {job}, the path to job.json
"""
from __future__ import annotations

import importlib.util
import platform
import shlex
import shutil
import sys
from pathlib import Path

import yaml

from .planner import Plan


class TrainerError(RuntimeError):
    pass


def detect_trainer() -> str:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mlx"
    if shutil.which("nvidia-smi"):
        return "trl"
    raise TrainerError("no supported accelerator for training (NVIDIA GPU or Apple silicon); "
                       "set train.engine: command to use your own trainer")


def resume_file(adapter_dir: Path) -> Path | None:
    f = adapter_dir / "adapters.safetensors"
    return f if f.exists() else None


def argv_for(engine: str, job_dir: Path, command: str | None = None) -> list[str]:
    """Build the trainer command. job.json in job_dir holds weights, dataset, adapter dir and plan."""
    import json
    job = json.loads((job_dir / "job.json").read_text())
    p = Plan(**job["plan"])
    if engine == "mlx":
        exe = shutil.which("mlx_lm.lora")
        if not exe:
            raise TrainerError("mlx_lm.lora not found on PATH: pip install mlx-lm")
        adapter_dir = Path(job["adapter_dir"])
        conf = {
            "model": job["weights"], "train": True, "data": job["dataset_dir"], "fine_tune_type": "lora",
            "mask_prompt": True, "batch_size": p.batch_size, "grad_accumulation_steps": p.grad_accumulation,
            "iters": p.iters, "learning_rate": p.learning_rate, "num_layers": p.layers,
            "lora_parameters": {"rank": p.rank, "scale": p.alpha / p.rank, "dropout": p.dropout},
            "max_seq_length": p.max_seq_len, "grad_checkpoint": p.grad_checkpoint,
            "steps_per_report": 10, "steps_per_eval": max(10, p.iters // 4), "val_batches": 8,
            "save_every": max(10, p.iters // 10), "adapter_path": str(adapter_dir), "seed": p.seed,
        }
        resume = resume_file(adapter_dir)
        if resume:
            conf["resume_adapter_file"] = str(resume)
        (job_dir / "mlx.yaml").write_text(yaml.safe_dump(conf, sort_keys=False))
        return [exe, "-c", str(job_dir / "mlx.yaml")]
    if engine == "trl":
        if importlib.util.find_spec("trl") is None:
            raise TrainerError("the trl trainer needs: pip install 'forge-ml[train-cuda]'")
        return [sys.executable, "-m", "forge.train.trl_sft", str(job_dir / "job.json")]
    if engine == "command":
        if not command:
            raise TrainerError("train.engine command needs train.command")
        return shlex.split(command.format(job=job_dir / "job.json"))
    raise TrainerError(f"unknown trainer {engine!r}")
