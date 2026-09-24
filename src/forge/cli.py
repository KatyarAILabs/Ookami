"""forge CLI. Exit codes: 0 ok / gate passed, 1 error, 3 gate did not pass."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import json_schema, load
from .evaluators.base import Target


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = load(args.file)
    for issue in cfg.issues:
        print(issue, file=sys.stderr)
    if not cfg.ok:
        return 1
    plat = f"Platform/{cfg.platform.metadata.name} ({cfg.platform.spec.backend})" if cfg.platform else "no Platform"
    print(f"ok: {plat}; {len(cfg.models)} Model(s): {', '.join(cfg.models) or '-'}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval_runner import run_eval, save

    cfg = load(args.file)
    errors = [i for i in cfg.issues if i.level == "error"]
    for issue in errors:
        print(issue, file=sys.stderr)
    if errors:
        return 1
    model = args.model or (next(iter(cfg.models)) if len(cfg.models) == 1 else None)
    if not model:
        print(f"error: --model is required; have {sorted(cfg.models)}", file=sys.stderr)
        return 1
    report = run_eval(cfg, model, Target.parse(args.candidate, "candidate"), Target.parse(args.incumbent, "incumbent"))
    path = save(report, cfg)
    print(report.markdown())
    print(f"\nreport: {path}")
    return 0 if report.decision == "pass" else 3


def cmd_schema(_: argparse.Namespace) -> int:
    print(json.dumps(json_schema(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="forge", description="Packaged, self-hosted AI infrastructure.")
    p.add_argument("--version", action="version", version=f"forge {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check forge.yaml")
    v.add_argument("-f", "--file", default="forge.yaml")
    v.set_defaults(fn=cmd_validate)

    e = sub.add_parser("eval", help="run the gate: candidate vs incumbent on the model's evaluators")
    e.add_argument("-f", "--file", default="forge.yaml")
    e.add_argument("--model", help="Model name (optional when the file has one)")
    e.add_argument("--candidate", required=True, help="openai:<base_url>#<model> or results:<path>")
    e.add_argument("--incumbent", required=True, help="openai:<base_url>#<model> or results:<path>")
    e.set_defaults(fn=cmd_eval)

    s = sub.add_parser("schema", help="print the JSON Schema for editor autocomplete")
    s.set_defaults(fn=cmd_schema)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (KeyError, ValueError, NotImplementedError, FileNotFoundError, ImportError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
