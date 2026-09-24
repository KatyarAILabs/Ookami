"""The memory planner: pick LoRA settings that fit the machine, so users don't tune hyperparameters to start.

The memory figures are estimates. `forge train` prints the plan, and every field can be set with
train.overrides.
"""
from __future__ import annotations

import math
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any

from ..catalog import CATALOG
from ..config import ModelSpec


@dataclass
class Plan:
    engine: str
    quantize: bool
    rank: int = 16
    alpha: int = 32
    dropout: float = 0.05
    layers: int = 16                 # mlx: top layers to adapt (-1 = all); trl adapts all linear layers
    learning_rate: float = 2e-4
    batch_size: int = 1
    grad_accumulation: int = 8
    epochs: float = 1.0
    iters: int = 0                   # derived from epochs unless overridden
    max_seq_len: int = 4096
    grad_checkpoint: bool = True
    seed: int = 0
    memory_gb: float | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def accelerator_memory_gb(engine: str) -> float | None:
    """Usable accelerator memory: unified memory on Apple silicon (75%), GPU 0 memory on NVIDIA."""
    try:
        if engine == "mlx" and platform.system() == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True)
            return int(out.stdout) / 2**30 * 0.75
        if engine == "trl" and shutil.which("nvidia-smi"):
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits", "-i", "0"],
                                 capture_output=True, text=True, check=True)
            return float(out.stdout.strip()) / 1024
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    return None


def plan(spec: ModelSpec, engine: str, n_train: int, memory_gb: float | None = None, weights: str = "") -> Plan:
    entry = CATALOG.get(spec.base)
    params_b = entry.params_b if entry else None
    mem = memory_gb if memory_gb is not None else accelerator_memory_gb(engine)
    p = Plan(engine=engine, quantize=False, memory_gb=mem)
    prequantized = any(t in weights.lower() for t in ("4bit", "8bit", "awq", "gptq", "mxfp4"))

    if params_b is None:
        p.notes.append("parameter count unknown; using conservative defaults")
        p.quantize = engine == "trl"
    elif mem is not None:
        bf16_need = params_b * 2 * 1.3 + 4          # weights + LoRA/optimizer/activations headroom, GB (estimate)
        q4_need = params_b * 0.6 * 1.3 + 4
        if prequantized:
            p.notes.append(f"weights are pre-quantized ({weights}); training LoRA on top (QLoRA)")
        elif bf16_need <= mem:
            p.notes.append(f"LoRA in 16-bit fits: ~{bf16_need:.0f} GB of {mem:.0f} GB")
        elif q4_need <= mem:
            p.quantize = True
            p.notes.append(f"16-bit needs ~{bf16_need:.0f} GB of {mem:.0f} GB; using 4-bit QLoRA (~{q4_need:.0f} GB)")
        else:
            p.quantize = True
            p.notes.append(f"~{q4_need:.0f} GB needed even in 4-bit, {mem:.0f} GB available; expect to run out of memory")
        if mem >= 48:
            p.grad_checkpoint = False
            p.batch_size, p.grad_accumulation = 2, 4
    else:
        p.notes.append("accelerator memory unknown; using conservative defaults")

    o = spec.train.overrides if spec.train else {}
    p.rank = int(o.get("lora.rank", p.rank))
    p.alpha = int(o.get("lora.alpha", 2 * p.rank))
    p.dropout = float(o.get("lora.dropout", p.dropout))
    p.layers = int(o.get("lora.layers", p.layers))
    p.learning_rate = float(o.get("learning_rate", p.learning_rate))
    p.batch_size = int(o.get("batch_size", p.batch_size))
    p.grad_accumulation = int(o.get("grad_accumulation", p.grad_accumulation))
    p.epochs = float(o.get("epochs", p.epochs))
    p.max_seq_len = int(o.get("max_seq_len", p.max_seq_len))
    p.seed = int(o.get("seed", p.seed))
    p.quantize = bool(o.get("quantize", p.quantize))
    p.iters = int(o.get("iters", max(10, math.ceil(p.epochs * n_train / p.batch_size))))
    return p
