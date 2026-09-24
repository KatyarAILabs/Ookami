"""Gateway keys, budgets and usage, stored next to the registry (SQLite). No extra database to run.

Keys are shown once and stored only as SHA-256 hashes. The master key lives in <storage>/secrets/master_key
with 0600 permissions and is generated on first use.
"""
from __future__ import annotations

import calendar
import hashlib
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
  key_hash TEXT PRIMARY KEY,
  prefix TEXT NOT NULL,           -- first characters, to recognise a key without storing it
  name TEXT NOT NULL,
  team TEXT NOT NULL,
  budget_usd REAL,                -- per calendar month; NULL = no budget
  rpm INTEGER,                    -- requests per minute; NULL = no limit
  created_at REAL NOT NULL,
  revoked_at REAL
);
CREATE TABLE IF NOT EXISTS usage (
  ts REAL NOT NULL,
  key_hash TEXT,
  team TEXT,
  model TEXT,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  cost_usd REAL,                  -- provider cost computed by the gateway (0 for self-hosted models)
  latency_ms REAL,
  ok INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_key_ts ON usage (key_hash, ts);
CREATE TABLE IF NOT EXISTS engine_runs (
  model TEXT NOT NULL,
  started_at REAL NOT NULL,
  stopped_at REAL,
  cost_per_hour REAL
);
"""

KEY_PREFIX = "ook-"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def month_start(now: float | None = None) -> float:
    """Start of the current calendar month, UTC."""
    t = time.gmtime(now or time.time())
    return float(calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0)))


@dataclass
class Key:
    key_hash: str
    prefix: str
    name: str
    team: str
    budget_usd: float | None
    rpm: int | None
    created_at: float
    revoked_at: float | None


class KeyStore:
    def __init__(self, storage: Path):
        self.storage = storage
        storage.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(storage / "registry.db", timeout=30, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ master key
    def master_key(self) -> str:
        path = self.storage / "secrets" / "master_key"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(path.parent, 0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(KEY_PREFIX + "master-" + secrets.token_urlsafe(32))
        return path.read_text().strip()

    # ------------------------------------------------------------ keys
    def create(self, name: str, team: str = "default", budget_usd: float | None = None,
               rpm: int | None = None) -> str:
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO keys VALUES (?,?,?,?,?,?,?,NULL)",
                        (hash_key(key), key[:10], name, team, budget_usd, rpm, time.time()))
        return key

    def get(self, key: str) -> Key | None:
        row = self.db.execute("SELECT * FROM keys WHERE key_hash = ?", (hash_key(key),)).fetchone()
        return Key(**dict(row)) if row else None

    def list(self) -> list[Key]:
        return [Key(**dict(r)) for r in self.db.execute("SELECT * FROM keys ORDER BY created_at")]

    def revoke(self, name_or_prefix: str) -> int:
        cur = self.db.execute("UPDATE keys SET revoked_at = ? WHERE revoked_at IS NULL AND (name = ? OR prefix = ?)",
                              (time.time(), name_or_prefix, name_or_prefix))
        return cur.rowcount

    # ------------------------------------------------------------ usage
    def record(self, key_hash: str | None, team: str | None, model: str | None, prompt_tokens: int,
               completion_tokens: int, cost_usd: float, latency_ms: float, ok: bool) -> None:
        self.db.execute("INSERT INTO usage VALUES (?,?,?,?,?,?,?,?,?)",
                        (time.time(), key_hash, team, model, prompt_tokens, completion_tokens, cost_usd,
                         latency_ms, int(ok)))

    def spend_this_month(self, key_hash: str) -> float:
        return self.db.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE key_hash = ? AND ts >= ?",
                               (key_hash, month_start())).fetchone()[0]

    # ------------------------------------------------------------ engine uptime, for self-hosted cost
    def engine_started(self, model: str, cost_per_hour: float | None) -> None:
        self.engine_stopped(model)
        self.db.execute("INSERT INTO engine_runs VALUES (?,?,NULL,?)", (model, time.time(), cost_per_hour))

    def engine_stopped(self, model: str) -> None:
        self.db.execute("UPDATE engine_runs SET stopped_at = ? WHERE model = ? AND stopped_at IS NULL",
                        (time.time(), model))

    def engine_cost(self, model: str, since: float, until: float) -> float:
        total = 0.0
        for r in self.db.execute("SELECT * FROM engine_runs WHERE model = ?", (model,)):
            start, stop = max(r["started_at"], since), min(r["stopped_at"] or until, until)
            if stop > start and r["cost_per_hour"]:
                total += (stop - start) / 3600 * r["cost_per_hour"]
        return total


@dataclass
class UsageRow:
    who: str                 # key name (team) or team
    requests: int
    errors: int
    tokens: int
    api_cost: float
    self_hosted_cost: float

    @property
    def total(self) -> float:
        return self.api_cost + self.self_hosted_cost


def usage_report(store: KeyStore, since: float, until: float | None = None, by: str = "key") -> tuple[list[UsageRow], dict]:
    """Spend per key (or team). Self-hosted cost is each engine's hardware cost for the period, split by token share."""
    until = until or time.time()
    names = {k.key_hash: (k.name, k.team) for k in store.list()}
    names[hash_key(store.master_key())] = ("master", "admin")
    rows = [dict(r) for r in store.db.execute("SELECT * FROM usage WHERE ts >= ? AND ts < ?", (since, until))]
    tokens_by_model: dict[str, int] = {}
    for r in rows:
        tokens_by_model[r["model"]] = tokens_by_model.get(r["model"], 0) + (r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0)
    model_cost = {m: store.engine_cost(m, since, until) for m in tokens_by_model}
    out: dict[str, UsageRow] = {}
    for r in rows:
        name, team = names.get(r["key_hash"], ("unknown key" if r["key_hash"] else "no key", "-"))
        who = team if by == "team" else f"{name} ({team})"
        u = out.setdefault(who, UsageRow(who, 0, 0, 0, 0.0, 0.0))
        t = (r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0)
        u.requests += 1
        u.errors += 0 if r["ok"] else 1
        u.tokens += t
        u.api_cost += r["cost_usd"] or 0.0
        if tokens_by_model.get(r["model"]):
            u.self_hosted_cost += model_cost[r["model"]] * t / tokens_by_model[r["model"]]
    per_model = {m: {"tokens": n, "hardware_cost": model_cost[m],
                     "cost_per_million_tokens": (model_cost[m] / n * 1e6) if n and model_cost[m] else None}
                 for m, n in tokens_by_model.items()}
    return sorted(out.values(), key=lambda u: -u.total), per_model
