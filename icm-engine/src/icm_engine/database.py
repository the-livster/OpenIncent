"""SQLite persistence for plans, payees, settings, column mappings,
calculation history, ledger entries, and API keys.

Default database location:
  Windows: %APPDATA%/OpenIncent/openincent.db
  Other:   ~/.openincent/openincent.db

Multi-tenancy: all entities are scoped to an org_id. Default is "default".
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS plans (
    id TEXT NOT NULL,
    org_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    yaml_content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (id, org_id)
);

CREATE TABLE IF NOT EXISTS payees (
    id TEXT NOT NULL,
    org_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    quota TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (id, org_id)
);

CREATE TABLE IF NOT EXISTS calculations (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL DEFAULT 'default',
    plan_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    input_summary TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT NOT NULL,
    org_id TEXT NOT NULL DEFAULT 'default',
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (key, org_id)
);

CREATE TABLE IF NOT EXISTS column_mappings (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    mapping_data TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL DEFAULT 'default',
    calculation_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    payee_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    inputs TEXT NOT NULL DEFAULT '{}',
    outputs TEXT NOT NULL DEFAULT '{}',
    human_readable TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL DEFAULT 'default',
    key_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_payees_plan ON payees(plan_id, org_id);
CREATE INDEX IF NOT EXISTS idx_calculations_plan ON calculations(plan_id, org_id);
CREATE INDEX IF NOT EXISTS idx_calculations_created ON calculations(created_at);
CREATE INDEX IF NOT EXISTS idx_ledger_payee ON ledger_entries(org_id, payee_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_ledger_calculation ON ledger_entries(calculation_id);
CREATE INDEX IF NOT EXISTS idx_ledger_timestamp ON ledger_entries(org_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
"""


def default_db_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
        return Path(base) / "OpenIncent" / "openincent.db"
    return Path.home() / ".openincent" / "openincent.db"


class Database:
    def __init__(self, path: str | Path, org_id: str = "default") -> None:
        self.path = Path(path)
        self.org_id = org_id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO _schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Plans
    # ------------------------------------------------------------------

    def save_plan(
        self, name: str, yaml_content: str, *, plan_id: str | None = None, description: str = "",
    ) -> str:
        pid = plan_id or uuid.uuid4().hex[:12]
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO plans (id, org_id, name, description, yaml_content, updated_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(id, org_id) DO UPDATE SET
                       name=excluded.name, description=excluded.description,
                       yaml_content=excluded.yaml_content, updated_at=datetime('now')""",
                (pid, self.org_id, name, description, yaml_content),
            )
        return pid

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM plans WHERE id=? AND org_id=?", (plan_id, self.org_id),
            ).fetchone()
        return dict(row) if row else None

    def list_plans(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM plans WHERE org_id=? ORDER BY updated_at DESC, rowid DESC",
                (self.org_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_plan(self, plan_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM plans WHERE id=? AND org_id=?", (plan_id, self.org_id),
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Payees
    # ------------------------------------------------------------------

    def save_payee(
        self, payee_id: str, name: str, quota: str, plan_id: str,
        effective_from: str, effective_to: str | None = None,
    ) -> str:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO payees (id, org_id, name, quota, plan_id, effective_from, effective_to)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id, org_id) DO UPDATE SET
                       name=excluded.name, quota=excluded.quota, plan_id=excluded.plan_id,
                       effective_from=excluded.effective_from, effective_to=excluded.effective_to""",
                (payee_id, self.org_id, name, quota, plan_id, effective_from, effective_to),
            )
        return payee_id

    def save_payees_batch(self, payees: list[dict[str, Any]]) -> int:
        with self._conn() as conn:
            for p in payees:
                p["org_id"] = self.org_id
            conn.executemany(
                """INSERT INTO payees (id, org_id, name, quota, plan_id, effective_from, effective_to)
                   VALUES (:id, :org_id, :name, :quota, :plan_id, :effective_from, :effective_to)
                   ON CONFLICT(id, org_id) DO UPDATE SET
                       name=excluded.name, quota=excluded.quota, plan_id=excluded.plan_id,
                       effective_from=excluded.effective_from, effective_to=excluded.effective_to""",
                payees,
            )
        return len(payees)

    def list_payees(self, plan_id: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if plan_id:
                rows = conn.execute(
                    "SELECT * FROM payees WHERE org_id=? AND plan_id=? ORDER BY name",
                    (self.org_id, plan_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM payees WHERE org_id=? ORDER BY name", (self.org_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def delete_payee(self, payee_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM payees WHERE id=? AND org_id=?", (payee_id, self.org_id),
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=? AND org_id=?",
                (key, self.org_id),
            ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO settings (key, org_id, value, updated_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(key, org_id) DO UPDATE SET
                       value=excluded.value, updated_at=datetime('now')""",
                (key, self.org_id, value),
            )

    def delete_setting(self, key: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM settings WHERE key=? AND org_id=?", (key, self.org_id),
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Column mappings
    # ------------------------------------------------------------------

    def save_mapping(self, name: str, mapping_data: dict[str, Any]) -> str:
        mid = uuid.uuid4().hex[:12]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO column_mappings (id, org_id, name, mapping_data) VALUES (?, ?, ?, ?)",
                (mid, self.org_id, name, json.dumps(mapping_data)),
            )
        return mid

    def get_mapping(self, mapping_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM column_mappings WHERE id=? AND org_id=?",
                (mapping_id, self.org_id),
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["mapping_data"] = json.loads(d["mapping_data"])
        return d

    def list_mappings(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM column_mappings WHERE org_id=? ORDER BY created_at DESC",
                (self.org_id,),
            ).fetchall()
        return [_unpack_mapping(r) for r in rows]

    def delete_mapping(self, mapping_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM column_mappings WHERE id=? AND org_id=?",
                (mapping_id, self.org_id),
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Calculation history
    # ------------------------------------------------------------------

    def record_calculation(
        self, plan_id: str, *, status: str = "completed", input_summary: dict[str, Any] | None = None,
    ) -> str:
        cid = uuid.uuid4().hex
        summary = json.dumps(input_summary or {})
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO calculations (id, org_id, plan_id, status, input_summary) VALUES (?, ?, ?, ?, ?)",
                (cid, self.org_id, plan_id, status, summary),
            )
        return cid

    def list_calculations(self, plan_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if plan_id:
                rows = conn.execute(
                    """SELECT * FROM calculations WHERE org_id=? AND plan_id=?
                       ORDER BY created_at DESC LIMIT ?""",
                    (self.org_id, plan_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM calculations WHERE org_id=? ORDER BY created_at DESC LIMIT ?",
                    (self.org_id, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Ledger entries
    # ------------------------------------------------------------------

    def save_ledger_entries(self, calculation_id: str, entries: list[dict[str, Any]]) -> int:
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO ledger_entries
                   (org_id, calculation_id, timestamp, transaction_id, payee_id,
                    rule_id, event_type, inputs, outputs, human_readable)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        self.org_id, calculation_id,
                        e.get("timestamp", ""), e.get("transaction_id", ""),
                        e.get("payee_id", ""), e.get("rule_id", ""),
                        e.get("event_type", ""), json.dumps(e.get("inputs", {})),
                        json.dumps(e.get("outputs", {})), e.get("human_readable", ""),
                    )
                    for e in entries
                ],
            )
        return len(entries)

    def query_ledger(
        self,
        *,
        payee_id: str | None = None,
        calculation_id: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["org_id=?"]
        params: list[Any] = [self.org_id]
        if payee_id:
            clauses.append("payee_id=?")
            params.append(payee_id)
        if calculation_id:
            clauses.append("calculation_id=?")
            params.append(calculation_id)
        if from_date:
            clauses.append("timestamp >= ?")
            params.append(from_date)
        if to_date:
            clauses.append("timestamp <= ?")
            params.append(to_date)

        where = " AND ".join(clauses)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM ledger_entries WHERE {where} ORDER BY timestamp DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [_unpack_ledger(r) for r in rows]

    # ------------------------------------------------------------------
    # API keys
    # ------------------------------------------------------------------

    def create_api_key(self, name: str = "") -> tuple[str, str]:
        """Create a new API key. Returns (key_id, plaintext_key)."""
        plaintext = "oi_" + secrets.token_urlsafe(32)
        key_hash = _hash_key(plaintext)
        kid = uuid.uuid4().hex[:12]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO api_keys (id, org_id, key_hash, name) VALUES (?, ?, ?, ?)",
                (kid, self.org_id, key_hash, name),
            )
        return kid, plaintext

    def validate_api_key(self, plaintext: str) -> str | None:
        """Return org_id if key is valid, None otherwise."""
        key_hash = _hash_key(plaintext)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT org_id FROM api_keys WHERE key_hash=?", (key_hash,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE api_keys SET last_used_at=datetime('now') WHERE key_hash=?",
                    (key_hash,),
                )
                return str(row["org_id"])
        return None

    def list_api_keys(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, org_id, name, created_at, last_used_at
                   FROM api_keys WHERE org_id=? ORDER BY created_at DESC""",
                (self.org_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_api_key(self, key_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM api_keys WHERE id=? AND org_id=?", (key_id, self.org_id),
            )
        return cur.rowcount > 0


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _unpack_mapping(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["mapping_data"] = json.loads(d["mapping_data"])
    return d


def _unpack_ledger(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["inputs"] = json.loads(d["inputs"])
    d["outputs"] = json.loads(d["outputs"])
    return d
