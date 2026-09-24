"""Evaluators that ship with Forge. None of them claims correctness on its own:
structural checks shape only; labels and python are the customer's judgement; webhook calls the customer's service.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import Example, Score, as_score, load_ref, post_json


def output_text(output: Any) -> str:
    if isinstance(output, dict):
        return output.get("content") or ""
    return "" if output is None else str(output)


def tool_calls(output: Any) -> list[dict]:
    return (output.get("tool_calls") or []) if isinstance(output, dict) else []


class StructuralEvaluator:
    """Shape checks only: JSON / schema, tool exists, tool arguments valid. Reports label it 'structural only'."""
    kind = "structural"
    version = "1"

    def __init__(self, name: str = "structural", **_: Any):
        self.name = name

    def score(self, example: Example, output: Any) -> Score:
        problems: list[str] = []
        tools = {t["function"]["name"]: t["function"] for t in example.meta.get("tools") or [] if "function" in t}
        for call in tool_calls(output):
            fn = call.get("function") or {}
            name = fn.get("name")
            if tools and name not in tools:
                problems.append(f"unknown tool {name!r}")
                continue
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                problems.append(f"{name}: arguments are not JSON")
                continue
            if name in tools:
                problems += [f"{name}: {p}" for p in check_schema(args, tools[name].get("parameters") or {})]
        fmt = example.meta.get("response_format")
        schema = example.meta.get("json_schema")
        if (fmt == "json" or schema) and not tool_calls(output):
            try:
                doc = json.loads(output_text(output))
                if schema:
                    problems += check_schema(doc, schema)
            except json.JSONDecodeError:
                problems.append("output is not JSON")
        ok = not problems
        return Score(float(ok), passed=ok, reason="; ".join(problems) or None)


def check_schema(doc: Any, schema: dict, path: str = "$") -> list[str]:
    """The small JSON Schema subset tool definitions use: type, required, properties, enum, items."""
    out: list[str] = []
    types = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}
    t = schema.get("type")
    if isinstance(t, str) and t in types:
        if not isinstance(doc, types[t]) or (t in ("integer", "number") and isinstance(doc, bool)):
            return [f"{path} should be {t}"]
    if "enum" in schema and doc not in schema["enum"]:
        out.append(f"{path} not one of {schema['enum']}")
    if isinstance(doc, dict):
        for k in schema.get("required", []):
            if k not in doc:
                out.append(f"{path}.{k} is required")
        for k, sub in (schema.get("properties") or {}).items():
            if k in doc:
                out += check_schema(doc[k], sub, f"{path}.{k}")
    if isinstance(doc, list) and isinstance(schema.get("items"), dict):
        for i, x in enumerate(doc):
            out += check_schema(x, schema["items"], f"{path}[{i}]")
    return out


class LabelsEvaluator:
    """Compares the output with a label the customer supplied (a column in their data)."""
    kind = "labels"
    version = "1"

    def __init__(self, name: str, column: str = "label", match: str = "exact", **_: Any):
        if match not in ("exact", "contains"):
            raise ValueError("labels.match must be exact or contains")
        self.name, self.column, self.match = name, column, match

    def score(self, example: Example, output: Any) -> Score:
        label = example.label if self.column == "label" else example.meta.get(self.column)
        if label is None:
            return Score(0.0, passed=False, reason=f"no {self.column!r} on this example")
        got, want = norm(output_text(output)), norm(str(label))
        ok = got == want if self.match == "exact" else want in got
        return Score(float(ok), passed=ok, reason=None if ok else f"expected {label!r}")


def norm(s: str) -> str:
    return " ".join(s.lower().split())


class PythonEvaluator:
    """A customer function: fn(example, output) -> Score | bool | number | dict."""
    kind = "python"

    def __init__(self, name: str, ref: str, base_dir: Path, **_: Any):
        self.fn = load_ref(ref, base_dir)
        self.name = name
        self.version = str(getattr(self.fn, "forge_version", "1"))

    def score(self, example: Example, output: Any) -> Score:
        return as_score(self.fn(example, output))


class WebhookEvaluator:
    """POSTs {example, output} to a customer service inside the perimeter; expects {value, passed?, reason?}."""
    kind = "webhook"

    def __init__(self, name: str, url: str, version: str = "1", timeout: float = 60.0, **_: Any):
        self.name, self.url, self.version, self.timeout = name, url, str(version), timeout

    def score(self, example: Example, output: Any) -> Score:
        payload = {"example": {"id": example.id, "input": example.input, "label": example.label,
                               "meta": example.meta}, "output": output}
        return as_score(post_json(self.url, payload, timeout=self.timeout))
