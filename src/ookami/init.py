"""ookami init: write a working ookami.yaml for this machine, so the first call is minutes away."""
from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Machine:
    kind: str            # "apple" | "nvidia" | "cpu"
    engine: str          # engine the generated config uses
    engine_ready: bool
    hint: str            # what to install if the engine is missing


def detect() -> Machine:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return Machine("apple", "mlx", bool(shutil.which("mlx_lm.server")), "pip install mlx-lm")
    if shutil.which("nvidia-smi"):
        return Machine("nvidia", "vllm", bool(shutil.which("vllm")), "pip install vllm")
    return Machine("cpu", "llama.cpp", bool(shutil.which("llama-server")),
                   "install llama.cpp (brew install llama.cpp, or a release from github.com/ggml-org/llama.cpp)")


def render(m: Machine, name: str = "local", api_model: bool = False) -> str:
    platform_doc = f"""# Written by `ookami init` for this machine ({m.kind}). Full reference: docs/reference/ookami-yaml.md
apiVersion: ookami.dev/v1alpha1
kind: Platform
metadata: {{ name: {name} }}
spec:
  storage: {{ uri: ./.ookami }}
  gateway: {{ port: 4000 }}              # auth is on: `ookami keys create NAME` or `ookami keys master`
"""
    if m.kind == "cpu":
        model = """---
apiVersion: ookami.dev/v1alpha1
kind: Model
metadata: { name: local }
spec:
  base: qwen2.5-0.5b-instruct
  licence: Apache-2.0
  weights: Qwen/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M
  serve:
    engine: command
    command: "llama-server -hf {weights} --host {host} --port {port} -c 4096 --alias {name}"
    modelId: local
"""
    else:
        model = """---
apiVersion: ookami.dev/v1alpha1
kind: Model
metadata: { name: local }
spec:
  base: qwen3-4b-instruct-2507         # engine picked automatically: MLX on Apple silicon, vLLM on NVIDIA
"""
    api = """---
apiVersion: ookami.dev/v1alpha1
kind: Model
metadata: { name: gpt }
spec:
  provider: { name: openai, model: gpt-5-mini, apiKey: "${secret:OPENAI_API_KEY}" }   # routed through the same gateway
""" if api_model else ""
    return platform_doc + model + api


def write(path: Path, m: Machine, api_model: bool = False, force: bool = False) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists; use --force to overwrite")
    path.write_text(render(m, api_model=api_model))
