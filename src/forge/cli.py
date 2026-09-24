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


def _load_ok(path: str):
    cfg = load(path)
    for issue in cfg.issues:
        if issue.level == "error":
            print(issue, file=sys.stderr)
    return cfg if cfg.ok else None


def cmd_up(args: argparse.Namespace) -> int:
    from .local.runtime import up

    cfg = _load_ok(args.file)
    if cfg is None:
        return 1
    services = up(cfg, timeout=args.timeout)
    gw = next((s for s in services if s.kind == "gateway"), None)
    print()
    for s in services:
        if s.kind == "engine":
            via = f"{gw.url}  model={s.name}" if gw else f"{s.url}  model={s.model_id}"
            print(f"{s.name:24} {via}")
    print("\nforge status | forge logs <name> | forge down")
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    from .local.runtime import down

    cfg = _load_ok(args.file)
    if cfg is None:
        return 1
    if down(cfg) == 0:
        print("nothing running")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from .local.runtime import status

    cfg = _load_ok(args.file)
    if cfg is None:
        return 1
    rows = status(cfg)
    if not rows:
        print("nothing running (forge up to start)")
        return 0
    for s, state in rows:
        print(f"{s.name:24} {s.kind:8} {state:9} pid {s.pid:<7} {s.url}")
    return 0 if all(state == "ready" for _, state in rows) else 3


def cmd_logs(args: argparse.Namespace) -> int:
    from .local.runtime import read_state, tail

    cfg = _load_ok(args.file)
    if cfg is None:
        return 1
    match = [s for s in read_state(cfg) if s.name == args.name]
    if not match:
        print(f"error: no service {args.name!r}", file=sys.stderr)
        return 1
    print(tail(match[0].log, args.lines))
    return 0


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

    u = sub.add_parser("up", help="start serving and the gateway on this machine (local backend)")
    u.add_argument("-f", "--file", default="forge.yaml")
    u.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for each service")
    u.set_defaults(fn=cmd_up)

    d = sub.add_parser("down", help="stop everything forge up started")
    d.add_argument("-f", "--file", default="forge.yaml")
    d.set_defaults(fn=cmd_down)

    st = sub.add_parser("status", help="show running services (exit 3 if any is not ready)")
    st.add_argument("-f", "--file", default="forge.yaml")
    st.set_defaults(fn=cmd_status)

    lg = sub.add_parser("logs", help="print the last lines of a service's log")
    lg.add_argument("name")
    lg.add_argument("-f", "--file", default="forge.yaml")
    lg.add_argument("-n", "--lines", type=int, default=80)
    lg.set_defaults(fn=cmd_logs)

    s = sub.add_parser("schema", help="print the JSON Schema for editor autocomplete")
    s.set_defaults(fn=cmd_schema)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (KeyError, ValueError, NotImplementedError, FileNotFoundError, ImportError, RuntimeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
