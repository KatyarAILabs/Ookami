"""Generate docs/reference/forge-yaml.md from the Pydantic schema, so the reference can't drift from the code.

    uv run python scripts/gen_config_reference.py        # rewrite the file
    uv run python scripts/gen_config_reference.py --check  # exit 1 if it is out of date (CI)
"""
from __future__ import annotations

import sys
import typing
from pathlib import Path

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from forge.config import ModelSpec, PlatformSpec

OUT = Path(__file__).resolve().parents[1] / "docs" / "reference" / "forge-yaml.md"


def type_name(tp) -> str:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is typing.Literal:
        return " \\| ".join(f"`{a}`" for a in args)
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)):
        return " \\| ".join(type_name(a) for a in args if a is not type(None)) + " (optional)" \
            if type(None) in args else " \\| ".join(type_name(a) for a in args)
    if origin in (list, typing.List):
        return f"list of {type_name(args[0])}" if args else "list"
    if origin in (dict, typing.Dict):
        return "map"
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return f"[{tp.__name__}](#{tp.__name__.lower()})"
    return getattr(tp, "__name__", str(tp))


def _nested_doc(tp) -> str:
    for t in [tp, *typing.get_args(tp)]:
        if isinstance(t, type) and issubclass(t, BaseModel) and t.__doc__:
            return t.__doc__.strip().splitlines()[0]
    return ""


def default_of(f) -> str:
    if f.is_required():
        return "**required**"
    d = f.default
    if d is PydanticUndefined or d is None:
        return "-"
    if isinstance(d, BaseModel):
        return "see below"
    return f"`{d}`"


def section(model: type[BaseModel], seen: set, out: list[str]) -> None:
    if model.__name__ in seen:
        return
    seen.add(model.__name__)
    out += [f"### {model.__name__}", ""]
    if model.__doc__ and model.__doc__.strip() and not model.__doc__.startswith("!!!"):
        out += [model.__doc__.strip().splitlines()[0], ""]
    out += ["| Field | Type | Default | Description |", "|---|---|---|---|"]
    nested = []
    for name, f in model.model_fields.items():
        desc = f.description or _nested_doc(f.annotation)
        out.append(f"| `{name}` | {type_name(f.annotation)} | {default_of(f)} | {desc.replace('|', '/')} |")
        for tp in [f.annotation, *typing.get_args(f.annotation)]:
            for t in [tp, *typing.get_args(tp)]:
                if isinstance(t, type) and issubclass(t, BaseModel):
                    nested.append(t)
    out.append("")
    for t in nested:
        section(t, seen, out)


def render() -> str:
    out = ["# forge.yaml reference", "",
           "_Generated from `src/forge/config.py` by `scripts/gen_config_reference.py`. Do not edit by hand._", "",
           "A `forge.yaml` holds YAML documents, each with `apiVersion: forge.dev/v1alpha1`, a `kind`, "
           "`metadata.name` (lowercase letters, digits and `-`) and a `spec`. Unknown keys are errors.", "",
           "## kind: Platform", ""]
    seen: set = set()
    section(PlatformSpec, seen, out)
    out += ["## kind: Model", ""]
    section(ModelSpec, seen, out)
    return "\n".join(out).rstrip() + "\n"


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT} is out of date; run scripts/gen_config_reference.py", file=sys.stderr)
            sys.exit(1)
    else:
        OUT.write_text(text)
        print(f"wrote {OUT}")
