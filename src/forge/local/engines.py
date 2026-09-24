"""Inference engines for the local backend. Each Model becomes one OpenAI-compatible server process.

  vllm     NVIDIA GPUs (Linux)
  mlx      Apple silicon (mlx-lm)
  command  anything else that speaks the OpenAI API: SGLang, llama.cpp, TGI, a custom server
"""
from __future__ import annotations

import platform
import shlex
import shutil
from dataclasses import dataclass

from ..catalog import CATALOG
from ..config import ModelDoc

INSTALL_HINT = {
    "vllm": "pip install vllm (Linux + NVIDIA GPU)",
    "mlx": "pip install mlx-lm (Apple silicon)",
}


class EngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class EngineLaunch:
    engine: str
    weights: str
    model_id: str          # what to send as "model" when calling the engine directly
    argv: list[str]


def detect_engine() -> str:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mlx"
    if shutil.which("nvidia-smi"):
        return "vllm"
    raise EngineError("no supported accelerator found (NVIDIA GPU or Apple silicon); "
                      "set serve.engine: command to use another OpenAI-compatible server")


def resolve_weights(doc: ModelDoc, engine: str) -> str:
    spec = doc.spec
    if spec.weights:
        return spec.weights
    entry = CATALOG.get(spec.base)
    if entry is None:
        raise EngineError(f"Model/{doc.metadata.name}: base {spec.base!r} is not in the catalog; set weights:")
    weights = (entry.mlx or entry.hf) if engine == "mlx" else entry.hf
    if not weights:
        raise EngineError(f"Model/{doc.metadata.name}: the catalog has no weights for {spec.base!r} yet; set weights:")
    return weights


def engine_for(doc: ModelDoc) -> str:
    s = doc.spec.serve.engine
    return detect_engine() if s == "auto" else s


def launch(doc: ModelDoc, port: int, adapter: str | None = None,
           loras: dict[str, str] | None = None) -> EngineLaunch:
    """The command that serves a Model's base, with a trained adapter when one is given.

    The id clients send stays the Model name (vllm, command) or the weights id (mlx), with or without an
    adapter, so the gateway config doesn't change when a new version goes live.
    `loras` (vllm only) serves several named adapters from one process; used by the gate.
    """
    s = doc.spec.serve
    name = doc.metadata.name
    engine = engine_for(doc)
    if engine == "command":
        weights = doc.spec.weights or resolve_weights(doc, "vllm")
        if adapter and "{adapter}" not in s.command:
            raise EngineError(f"Model/{name}: serve.command has no {{adapter}} placeholder, "
                              "so it can't serve a trained version")
        argv = shlex.split(s.command.format(weights=weights, port=port, host=s.host, name=name,
                                            adapter=adapter or "")) + s.args
        return EngineLaunch(engine, weights, s.modelId or name, argv)
    weights = resolve_weights(doc, engine)
    if engine == "vllm":
        argv = [_bin("vllm", "vllm"), "serve", weights, "--host", s.host, "--port", str(port)]
        mods = dict(loras or {})
        if adapter:
            mods[name] = adapter
        if mods:
            argv += ["--served-model-name", f"{name}-base", "--enable-lora", "--max-loras", str(len(mods)),
                     "--lora-modules", *[f"{k}={v}" for k, v in mods.items()]]
        else:
            argv += ["--served-model-name", name]
        return EngineLaunch(engine, weights, name, [*argv, *s.args])
    argv = [_bin("mlx_lm.server", "mlx"), "--model", weights, "--host", s.host, "--port", str(port)]
    if adapter:
        argv += ["--adapter-path", adapter]
    return EngineLaunch(engine, weights, weights, [*argv, *s.args])


def _bin(exe: str, engine: str) -> str:
    path = shutil.which(exe)
    if not path:
        raise EngineError(f"{exe} not found on PATH: {INSTALL_HINT[engine]}")
    return path
