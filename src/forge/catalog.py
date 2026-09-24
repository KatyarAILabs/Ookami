"""Base models Forge knows. Open weights with a licence that allows commercial use.

Any other model works too: set `weights:` (Hugging Face id or local path) and `licence:` on the Model.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogModel:
    licence: str
    hf: str | None = None       # weights for vLLM and other CUDA engines
    mlx: str | None = None      # pre-converted weights for Apple silicon; falls back to hf
    params_b: float | None = None   # total parameters, billions; drives the memory planner


CATALOG: dict[str, CatalogModel] = {
    "qwen3.5-0.8b": CatalogModel("Apache-2.0", "Qwen/Qwen3.5-0.8B", params_b=0.8),
    "qwen3.5-2b": CatalogModel("Apache-2.0", "Qwen/Qwen3.5-2B", params_b=2),
    "qwen3.5-4b": CatalogModel("Apache-2.0", "Qwen/Qwen3.5-4B", params_b=4),
    "qwen3.5-9b": CatalogModel("Apache-2.0", "Qwen/Qwen3.5-9B", params_b=9),
    "qwen3-4b-instruct-2507": CatalogModel("Apache-2.0", "Qwen/Qwen3-4B-Instruct-2507",
                                           "mlx-community/Qwen3-4B-Instruct-2507-4bit", params_b=4),
    "gemma-4-e2b": CatalogModel("Apache-2.0", params_b=5),
    "gemma-4-e4b": CatalogModel("Apache-2.0", params_b=8),
    "gemma-4-12b": CatalogModel("Apache-2.0", params_b=12),
    "gpt-oss-20b": CatalogModel("Apache-2.0", "openai/gpt-oss-20b", params_b=21),
    "phi-4-mini": CatalogModel("MIT", "microsoft/Phi-4-mini-instruct", params_b=3.8),
    "smollm3-3b": CatalogModel("Apache-2.0", "HuggingFaceTB/SmolLM3-3B", params_b=3),
}

ALLOWED_LICENCES = {"Apache-2.0", "MIT", "BSD-3-Clause", "BSD-2-Clause"}
