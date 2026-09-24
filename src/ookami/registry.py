"""The model registry: every trained version, what went into it, how it did at the gate, and what is live.

SQLite in the storage directory for the local backend (Postgres on k8s later, same schema).
Every state change also writes an event, so the history is auditable.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
  model TEXT NOT NULL,
  version INTEGER NOT NULL,
  base TEXT NOT NULL,
  weights TEXT NOT NULL,
  adapter TEXT,
  dataset TEXT,
  dataset_hash TEXT,
  config_sha TEXT,
  job_id TEXT,
  status TEXT NOT NULL,          -- training | failed | evaluating | passed | rejected | promoted | retired
  decision TEXT,                 -- gate decision: pass | partial | fail
  report TEXT,                   -- path to the eval report
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY (model, version)
);
CREATE TABLE IF NOT EXISTS events (
  ts REAL NOT NULL,
  model TEXT NOT NULL,
  version INTEGER,
  action TEXT NOT NULL,
  detail TEXT
);
"""

STATUSES = {"training", "failed", "evaluating", "passed", "rejected", "promoted", "retired"}


@dataclass
class Version:
    model: str
    version: int
    base: str
    weights: str
    adapter: str | None
    dataset: str | None
    dataset_hash: str | None
    config_sha: str | None
    job_id: str | None
    status: str
    decision: str | None
    report: str | None
    created_at: float
    updated_at: float

    @property
    def tag(self) -> str:
        return f"{self.model}:v{self.version}"


class Registry:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def event(self, model: str, version: int | None, action: str, **detail: Any) -> None:
        self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                        (time.time(), model, version, action, json.dumps(detail, default=str)))

    def create(self, model: str, base: str, weights: str, **fields: Any) -> Version:
        now = time.time()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            n = self.db.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM versions WHERE model = ?",
                                (model,)).fetchone()[0]
            cols = {"model": model, "version": n, "base": base, "weights": weights, "status": "training",
                    "created_at": now, "updated_at": now, **fields}
            self.db.execute(f"INSERT INTO versions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            list(cols.values()))
        self.event(model, n, "created", **fields)
        return self.get(model, n)

    def update(self, model: str, version: int, **fields: Any) -> Version:
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"unknown status {fields['status']!r}")
        fields["updated_at"] = time.time()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE versions SET {sets} WHERE model = ? AND version = ?",
                        [*fields.values(), model, version])
        self.event(model, version, "updated", **{k: v for k, v in fields.items() if k != "updated_at"})
        return self.get(model, version)

    def get(self, model: str, version: int) -> Version:
        row = self.db.execute("SELECT * FROM versions WHERE model = ? AND version = ?", (model, version)).fetchone()
        if row is None:
            raise KeyError(f"no version {model}:v{version}")
        return Version(**dict(row))

    def list(self, model: str | None = None) -> list[Version]:
        q, args = ("SELECT * FROM versions WHERE model = ? ORDER BY version", (model,)) if model else \
            ("SELECT * FROM versions ORDER BY model, version", ())
        return [Version(**dict(r)) for r in self.db.execute(q, args)]

    def live(self, model: str) -> Version | None:
        row = self.db.execute("SELECT * FROM versions WHERE model = ? AND status = 'promoted'", (model,)).fetchone()
        return Version(**dict(row)) if row else None

    def promote(self, model: str, version: int, force: bool = False, reason: str | None = None) -> Version:
        """Make a version live. Refuses unless it passed its gate, or force is set (recorded with a reason)."""
        v = self.get(model, version)
        if v.status == "promoted":
            return v
        if v.status != "passed" and not force:
            raise PermissionError(f"{v.tag} did not pass its gate (status {v.status}); "
                                  "use --force --reason '...' to override, which is recorded")
        if force and not reason:
            raise ValueError("a forced promotion needs a reason")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("UPDATE versions SET status = 'retired', updated_at = ? "
                            "WHERE model = ? AND status = 'promoted'", (time.time(), model))
            self.db.execute("UPDATE versions SET status = 'promoted', updated_at = ? WHERE model = ? AND version = ?",
                            (time.time(), model, version))
        self.event(model, version, "promoted", forced=force, reason=reason, previous_status=v.status)
        return self.get(model, version)

    def events(self, model: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM events WHERE model = ? ORDER BY ts", (model,))]


def open_registry(storage: Path) -> Registry:
    return Registry(storage / "registry.db")
