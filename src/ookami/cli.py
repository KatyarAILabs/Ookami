"""ookami CLI. Exit codes: 0 ok / gate passed, 1 error, 3 gate did not pass."""
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
        if s.kind == "console":
            print(f"{'console':24} {s.url}  (sign in with `ookami keys master`)")
        if s.kind == "tracing":
            print(f"{'tracing':24} Trajectory collector <- gateway callbacks ({s.url})")
        if s.kind == "engine":
            via = f"{gw.url}  model={s.name}" if gw else f"{s.url}  model={s.model_id}"
            print(f"{s.name:24} {via}")
    if gw and cfg.platform.spec.gateway.auth == "keys":
        print("\ngateway auth is on: create a key with `ookami keys create NAME`, or use `ookami keys master`")
    print("\nookami status | ookami logs <name> | ookami down")
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
        print("nothing running (ookami up to start)")
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
        print(f"ookami jobs -f {args.file}   # to follow it")
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
        print(f"ookami promote {args.model} {v.version} -f {args.file}   # to put it live")
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
        print("no trained versions yet (ookami train <model>)")
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
        print("not running; ookami up serves it from now on")
    return 0


def _keystore(args):
    from .gateway.keys import KeyStore
    from .local.runtime import storage_dir

    cfg = _load_ok(args.file)
    return (KeyStore(storage_dir(cfg)), cfg) if cfg else (None, None)


def cmd_keys(args: argparse.Namespace) -> int:
    ks, _ = _keystore(args)
    if ks is None:
        return 1
    try:
        if args.action == "create":
            if not args.name:
                print("error: ookami keys create NAME", file=sys.stderr)
                return 1
            key = ks.create(args.name, args.team, args.budget, args.rpm)
            print(key)
            print(f"# key {args.name!r} for team {args.team!r}"
                  f"{f', budget ${args.budget:g}/month' if args.budget is not None else ''}"
                  f"{f', {args.rpm} req/min' if args.rpm else ''}. Shown once; Ookami stores only its hash.",
                  file=sys.stderr)
        elif args.action == "list":
            keys = ks.list()
            if not keys:
                print("no keys (ookami keys create NAME)")
            for k in keys:
                state = "revoked" if k.revoked_at else "active"
                spent = ks.spend_this_month(k.key_hash)
                budget = f"${spent:.2f} of ${k.budget_usd:g}" if k.budget_usd is not None else f"${spent:.2f}"
                print(f"{k.name:20} {k.team:14} {k.prefix}…  {state:8} {budget:>20} this month"
                      f"{f'  {k.rpm}/min' if k.rpm else ''}")
        elif args.action == "revoke":
            n = ks.revoke(args.name or "")
            print(f"revoked {n} key(s)")
            return 0 if n else 1
        elif args.action == "master":
            print(ks.master_key())
    finally:
        ks.close()
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    import time

    from .gateway.keys import usage_report

    ks, _ = _keystore(args)
    if ks is None:
        return 1
    since = time.time() - _duration(args.since)
    rows, per_model = usage_report(ks, since, by=args.by)
    ks.close()
    if not rows:
        print("no gateway usage in this period")
        return 0
    print(f"{args.by:28} {'requests':>9} {'errors':>7} {'tokens':>11} {'API $':>10} {'self-hosted $':>14} {'total $':>10}")
    for u in rows:
        print(f"{u.who:28} {u.requests:9} {u.errors:7} {u.tokens:11} {u.api_cost:10.4f} {u.self_hosted_cost:14.4f} "
              f"{u.total:10.4f}")
    hosted = {m: v for m, v in per_model.items() if v["hardware_cost"]}
    if hosted:
        print("\nself-hosted models (hardware cost split by token share):")
        for m, v in hosted.items():
            per_m = f"${v['cost_per_million_tokens']:.2f} per 1M tokens" if v["cost_per_million_tokens"] else "-"
            print(f"  {m:24} {v['tokens']:>11} tokens  ${v['hardware_cost']:.4f}  {per_m}")
    return 0


def _duration(text: str) -> float:
    unit = {"h": 3600, "d": 86400, "m": 60}[text[-1]]
    return float(text[:-1]) * unit


def cmd_init(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .init import detect, write

    m = detect()
    path = Path(args.file)
    try:
        write(path, m, api_model=args.with_openai, force=args.force)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {path} for {m.kind} (engine: {m.engine})")
    steps = [] if m.engine_ready else [f"install the engine: {m.hint}"]
    steps += ["pip install 'ookami[gateway]'   # if you haven't", f"ookami up -f {path}",
              f"export OOKAMI_API_KEY=$(ookami keys master -f {path})",
              "curl localhost:4000/v1/chat/completions -H \"Authorization: Bearer $OOKAMI_API_KEY\" "
              "-H 'Content-Type: application/json' -d '{\"model\": \"local\", \"messages\": "
              "[{\"role\": \"user\", \"content\": \"hi\"}]}'"]
    if args.with_openai:
        steps.insert(0, "export OPENAI_API_KEY=...   # for the gpt model")
    print("next:")
    for i, st in enumerate(steps, 1):
        print(f"  {i}. {st}")
    return 0


def cmd_console(args: argparse.Namespace) -> int:
    from .console.server import run

    cfg = _load_ok(args.file)
    if cfg is None:
        return 1
    c = cfg.platform.spec.console
    run(cfg.path, args.host or c.host, args.port or c.port)
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from .train.jobs import run_worker
    return run_worker(args.file)


def cmd_schema(_: argparse.Namespace) -> int:
    print(json.dumps(json_schema(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ookami", description="Packaged, self-hosted AI infrastructure.")
    p.add_argument("--version", action="version", version=f"ookami {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="write a working ookami.yaml for this machine")
    i.add_argument("-f", "--file", default="ookami.yaml")
    i.add_argument("--with-openai", action="store_true", help="also route an OpenAI model through the gateway")
    i.add_argument("--force", action="store_true", help="overwrite an existing file")
    i.set_defaults(fn=cmd_init)

    v = sub.add_parser("validate", help="check ookami.yaml")
    v.add_argument("-f", "--file", default="ookami.yaml")
    v.set_defaults(fn=cmd_validate)

    e = sub.add_parser("eval", help="run the gate: candidate vs incumbent on the model's evaluators")
    e.add_argument("-f", "--file", default="ookami.yaml")
    e.add_argument("--model", help="Model name (optional when the file has one)")
    e.add_argument("--candidate", required=True, help="openai:<base_url>#<model> or results:<path>")
    e.add_argument("--incumbent", required=True, help="openai:<base_url>#<model> or results:<path>")
    e.set_defaults(fn=cmd_eval)

    u = sub.add_parser("up", help="start serving and the gateway on this machine (local backend)")
    u.add_argument("-f", "--file", default="ookami.yaml")
    u.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for each service")
    u.set_defaults(fn=cmd_up)

    d = sub.add_parser("down", help="stop everything ookami up started")
    d.add_argument("-f", "--file", default="ookami.yaml")
    d.set_defaults(fn=cmd_down)

    st = sub.add_parser("status", help="show running services (exit 3 if any is not ready)")
    st.add_argument("-f", "--file", default="ookami.yaml")
    st.set_defaults(fn=cmd_status)

    lg = sub.add_parser("logs", help="print the last lines of a service's log")
    lg.add_argument("name")
    lg.add_argument("-f", "--file", default="ookami.yaml")
    lg.add_argument("-n", "--lines", type=int, default=80)
    lg.set_defaults(fn=cmd_logs)

    t = sub.add_parser("train", help="snapshot data, fine-tune, then gate the new version against the live one")
    t.add_argument("model")
    t.add_argument("-f", "--file", default="ookami.yaml")
    t.add_argument("--detach", action="store_true", help="queue the job and return")
    t.set_defaults(fn=cmd_train)

    j = sub.add_parser("jobs", help="list training jobs, or show one job's log")
    j.add_argument("-f", "--file", default="ookami.yaml")
    j.add_argument("--logs", metavar="JOB_ID")
    j.add_argument("-n", "--lines", type=int, default=80)
    j.set_defaults(fn=cmd_jobs)

    m = sub.add_parser("models", help="list trained versions and their gate results")
    m.add_argument("model", nargs="?")
    m.add_argument("-f", "--file", default="ookami.yaml")
    m.set_defaults(fn=cmd_models)

    pr = sub.add_parser("promote", help="put a version live (only if it passed its gate, unless --force)")
    pr.add_argument("model")
    pr.add_argument("version", nargs="?", type=int, help="default: the newest version that passed")
    pr.add_argument("-f", "--file", default="ookami.yaml")
    pr.add_argument("--force", action="store_true")
    pr.add_argument("--reason")
    pr.set_defaults(fn=cmd_promote)

    k = sub.add_parser("keys", help="gateway keys: create, list, revoke, or show the master key")
    k.add_argument("action", choices=["create", "list", "revoke", "master"])
    k.add_argument("name", nargs="?")
    k.add_argument("-f", "--file", default="ookami.yaml")
    k.add_argument("--team", default="default")
    k.add_argument("--budget", type=float, help="USD per calendar month (provider cost)")
    k.add_argument("--rpm", type=int, help="requests per minute")
    k.set_defaults(fn=cmd_keys)

    us = sub.add_parser("usage", help="gateway spend per key or team: API cost and self-hosted hardware cost")
    us.add_argument("-f", "--file", default="ookami.yaml")
    us.add_argument("--by", choices=["key", "team"], default="key")
    us.add_argument("--since", default="30d", help="e.g. 24h, 7d, 30d")
    us.set_defaults(fn=cmd_usage)

    co = sub.add_parser("console", help="run the web console in the foreground (ookami up starts it when enabled)")
    co.add_argument("-f", "--file", default="ookami.yaml")
    co.add_argument("--host")
    co.add_argument("--port", type=int)
    co.set_defaults(fn=cmd_console)

    w = sub.add_parser("_worker", help=argparse.SUPPRESS)
    w.add_argument("-f", "--file", default="ookami.yaml")
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
