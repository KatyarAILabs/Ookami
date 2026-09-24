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


def cmd_train(args: argparse.Namespace) -> int:
    import time

    from .interfaces import JobState
    from .train import submit_training
    from .train.jobs import LocalJobRunner

    cfg = _load_ok(args.file)
    if cfg is None:
        return 1
    sub = submit_training(cfg, args.model)
    p = sub.plan
    print(f"{sub.version.tag}: {sub.snapshot.train} train / {sub.snapshot.valid} valid rows "
          f"({sub.snapshot.held_out} held out, {sub.snapshot.audit} audit, never trained on)")
    print(f"plan: {p.engine} LoRA r={p.rank} alpha={p.alpha} lr={p.learning_rate:g} iters={p.iters} "
          f"batch={p.batch_size}x{p.grad_accumulation} seq={p.max_seq_len}"
          f"{' 4-bit' if p.quantize else ''}")
    for note in p.notes:
        print(f"  - {note}")
    print(f"job {sub.job_id} queued")
    if args.detach:
        print(f"forge jobs -f {args.file}   # to follow it")
        return 0
    runner = LocalJobRunner(cfg)
    last = ""
    while True:
        st = runner.status(sub.job_id)
        if st.message != last:
            print(f"[{time.strftime('%H:%M:%S')}] {st.state.value}: {st.message}")
            last = st.message
        if st.state in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED):
            break
        time.sleep(2)
    from .registry import open_registry
    from .local.runtime import storage_dir
    reg = open_registry(storage_dir(cfg))
    v = reg.get(args.model, sub.version.version)
    reg.close()
    if st.state is JobState.FAILED:
        print(runner.logs(sub.job_id, 30))
        return 1
    print(f"{v.tag}: {v.status}. Report: {v.report}")
    if v.status == "passed":
        print(f"forge promote {args.model} {v.version} -f {args.file}   # to put it live")
    return 0 if v.status == "passed" else 3


def cmd_jobs(args: argparse.Namespace) -> int:
    from .train.jobs import LocalJobRunner

    cfg = _load_ok(args.file)
    if cfg is None:
        return 1
    runner = LocalJobRunner(cfg)
    if args.logs:
        print(runner.logs(args.logs, args.lines))
        return 0
    rows = runner.list()
    if not rows:
        print("no jobs")
    for jid, raw in rows:
        print(f"{jid:44} {raw['state']:10} {raw.get('message', '')}")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    from .local.runtime import storage_dir
    from .registry import open_registry

    cfg = _load_ok(args.file)
    if cfg is None:
        return 1
    reg = open_registry(storage_dir(cfg))
    try:
        versions = reg.list(args.model)
    finally:
        reg.close()
    if not versions:
        print("no trained versions yet (forge train <model>)")
    for v in versions:
        print(f"{v.tag:28} {v.status:10} gate={v.decision or '-':8} data={v.dataset_hash or '-'}  {v.report or ''}")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    from .local.runtime import restart_engine, storage_dir
    from .registry import open_registry

    cfg = _load_ok(args.file)
    if cfg is None:
        return 1
    reg = open_registry(storage_dir(cfg))
    try:
        version = args.version or max((v.version for v in reg.list(args.model) if v.status == "passed"), default=None)
        if version is None:
            print(f"error: {args.model} has no version that passed its gate", file=sys.stderr)
            return 1
        try:
            v = reg.promote(args.model, int(version), force=args.force, reason=args.reason)
        except PermissionError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 3
    finally:
        reg.close()
    print(f"{v.tag} is live")
    if not restart_engine(cfg, args.model):
        print("not running; forge up serves it from now on")
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from .train.jobs import run_worker
    return run_worker(args.file)


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

    t = sub.add_parser("train", help="snapshot data, fine-tune, then gate the new version against the live one")
    t.add_argument("model")
    t.add_argument("-f", "--file", default="forge.yaml")
    t.add_argument("--detach", action="store_true", help="queue the job and return")
    t.set_defaults(fn=cmd_train)

    j = sub.add_parser("jobs", help="list training jobs, or show one job's log")
    j.add_argument("-f", "--file", default="forge.yaml")
    j.add_argument("--logs", metavar="JOB_ID")
    j.add_argument("-n", "--lines", type=int, default=80)
    j.set_defaults(fn=cmd_jobs)

    m = sub.add_parser("models", help="list trained versions and their gate results")
    m.add_argument("model", nargs="?")
    m.add_argument("-f", "--file", default="forge.yaml")
    m.set_defaults(fn=cmd_models)

    pr = sub.add_parser("promote", help="put a version live (only if it passed its gate, unless --force)")
    pr.add_argument("model")
    pr.add_argument("version", nargs="?", type=int, help="default: the newest version that passed")
    pr.add_argument("-f", "--file", default="forge.yaml")
    pr.add_argument("--force", action="store_true")
    pr.add_argument("--reason")
    pr.set_defaults(fn=cmd_promote)

    w = sub.add_parser("_worker", help=argparse.SUPPRESS)
    w.add_argument("-f", "--file", default="forge.yaml")
    w.set_defaults(fn=cmd_worker)

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
