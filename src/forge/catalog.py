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


CATALOG: dict[str, CatalogModel] = {
    "qwen3.5-0.8b": CatalogModel("Apache-2.0", "Qwen/Qwen3.5-0.8B"),
    "qwen3.5-2b": CatalogModel("Apache-2.0", "Qwen/Qwen3.5-2B"),
    "qwen3.5-4b": CatalogModel("Apache-2.0", "Qwen/Qwen3.5-4B"),
    "qwen3.5-9b": CatalogModel("Apache-2.0", "Qwen/Qwen3.5-9B"),
    "qwen3-4b-instruct-2507": CatalogModel("Apache-2.0", "Qwen/Qwen3-4B-Instruct-2507",
                                           "mlx-community/Qwen3-4B-Instruct-2507-4bit"),
    "gemma-4-e2b": CatalogModel("Apache-2.0"),
    "gemma-4-e4b": CatalogModel("Apache-2.0"),
    "gemma-4-12b": CatalogModel("Apache-2.0"),
    "gpt-oss-20b": CatalogModel("Apache-2.0", "openai/gpt-oss-20b"),
    "phi-4-mini": CatalogModel("MIT", "microsoft/Phi-4-mini-instruct"),
    "smollm3-3b": CatalogModel("Apache-2.0", "HuggingFaceTB/SmolLM3-3B"),
}

ALLOWED_LICENCES = {"Apache-2.0", "MIT", "BSD-3-Clause", "BSD-2-Clause"}
