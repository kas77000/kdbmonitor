# kdbmonitor/core/storage.py
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta
from typing import Optional

from kdbmonitor.core.models import Connection, Alert, alert_to_json, alert_from_json

RESULT_RETENTION_DAYS = 20   # default; overridable via the 'result_retention_days' setting
RESULT_MAX_ROWS = 500        # default row cap per snapshot; overridable via 'result_max_rows'


def _jsonable(v):
    """Coerce a cell value to something json.dumps can store."""
    if v is None:
        return None
    if isinstance(v, bool) or isinstance(v, (int, str)):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) else v
    if hasattr(v, "item"):        # numpy scalar
        try:
            return _jsonable(v.item())
        except Exception:  # noqa: BLE001
            return str(v)
    if hasattr(v, "isoformat"):    # datetime / date / pandas Timestamp
        return v.isoformat()
    return str(v)


class Storage:
    def __init__(self, path: str = "kdbmonitor.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def init_db(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                schema_json TEXT NOT NULL DEFAULT '{}',
                last_introspected_at TEXT
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                alert_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                status TEXT NOT NULL,
                triggered INTEGER NOT NULL DEFAULT 0,
                notified INTEGER NOT NULL DEFAULT 0,
                row_count INTEGER,
                message TEXT,
                result_hash TEXT
            );
            CREATE TABLE IF NOT EXISTS alert_results (
                alert_id INTEGER NOT NULL,
                day TEXT NOT NULL,              -- YYYY-MM-DD (UTC) of capture
                ts TEXT NOT NULL,               -- full capture timestamp
                row_count INTEGER NOT NULL,
                truncated INTEGER NOT NULL DEFAULT 0,
                columns_json TEXT NOT NULL,
                rows_json TEXT NOT NULL,
                PRIMARY KEY (alert_id, day)     -- one (latest) snapshot per alert per day
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Additive migrations for DBs created before a column existed."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(alert_runs)")}
        if "result_hash" not in cols:
            self.conn.execute("ALTER TABLE alert_runs ADD COLUMN result_hash TEXT")

    # --- connections ---
    def add_connection(self, c: Connection) -> int:
        r = self.conn.execute(
            "SELECT 1 FROM connections WHERE name=? LIMIT 1", (c.name,)
        ).fetchone()
        if r is not None:
            raise ValueError(f"A connection named '{c.name}' already exists.")
        cur = self.conn.execute(
            "INSERT INTO connections(name, host, port, schema_json, last_introspected_at) VALUES (?,?,?,?,?)",
            (c.name, c.host, c.port, json.dumps(c.schema), c.last_introspected_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def _row_to_connection(self, r: sqlite3.Row) -> Connection:
        return Connection(
            id=r["id"], name=r["name"], host=r["host"], port=r["port"],
            schema=json.loads(r["schema_json"]),
            last_introspected_at=r["last_introspected_at"],
        )

    def list_connections(self) -> list[Connection]:
        rows = self.conn.execute("SELECT * FROM connections ORDER BY name").fetchall()
        return [self._row_to_connection(r) for r in rows]

    def get_connection(self, cid: int) -> Optional[Connection]:
        r = self.conn.execute("SELECT * FROM connections WHERE id=?", (cid,)).fetchone()
        return self._row_to_connection(r) if r else None

    def get_connection_by_name(self, name: str) -> Optional[Connection]:
        r = self.conn.execute("SELECT * FROM connections WHERE name=?", (name,)).fetchone()
        return self._row_to_connection(r) if r else None

    def update_connection(self, c: Connection) -> None:
        self.conn.execute(
            "UPDATE connections SET name=?, host=?, port=?, schema_json=?, last_introspected_at=? WHERE id=?",
            (c.name, c.host, c.port, json.dumps(c.schema), c.last_introspected_at, c.id),
        )
        self.conn.commit()

    def delete_connection(self, cid: int) -> None:
        self.conn.execute("DELETE FROM connections WHERE id=?", (cid,))
        self.conn.commit()

    # --- alerts ---
    def alert_name_exists(self, name: str, exclude_id: Optional[int] = None) -> bool:
        if exclude_id is None:
            r = self.conn.execute(
                "SELECT 1 FROM alerts WHERE name=? LIMIT 1", (name,)
            ).fetchone()
        else:
            r = self.conn.execute(
                "SELECT 1 FROM alerts WHERE name=? AND id<>? LIMIT 1", (name, exclude_id)
            ).fetchone()
        return r is not None

    def add_alert(self, a: Alert) -> int:
        if self.alert_name_exists(a.name):
            raise ValueError(f"An alert named '{a.name}' already exists.")
        cur = self.conn.execute(
            "INSERT INTO alerts(name, enabled, alert_json) VALUES (?,?,?)",
            (a.name, 1 if a.enabled else 0, alert_to_json(a)),
        )
        self.conn.commit()
        return cur.lastrowid

    def _row_to_alert(self, r: sqlite3.Row) -> Alert:
        a = alert_from_json(r["alert_json"])
        a.id = r["id"]
        a.enabled = bool(r["enabled"])
        return a

    def list_alerts(self) -> list[Alert]:
        rows = self.conn.execute("SELECT * FROM alerts ORDER BY name").fetchall()
        return [self._row_to_alert(r) for r in rows]

    def get_alert(self, aid: int) -> Optional[Alert]:
        r = self.conn.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()
        return self._row_to_alert(r) if r else None

    def update_alert(self, a: Alert) -> None:
        if self.alert_name_exists(a.name, exclude_id=a.id):
            raise ValueError(f"An alert named '{a.name}' already exists.")
        self.conn.execute(
            "UPDATE alerts SET name=?, enabled=?, alert_json=? WHERE id=?",
            (a.name, 1 if a.enabled else 0, alert_to_json(a), a.id),
        )
        self.conn.commit()

    def set_alert_enabled(self, aid: int, enabled: bool) -> None:
        self.conn.execute("UPDATE alerts SET enabled=? WHERE id=?", (1 if enabled else 0, aid))
        self.conn.commit()

    def delete_alert(self, aid: int) -> None:
        self.conn.execute("DELETE FROM alerts WHERE id=?", (aid,))
        self.conn.commit()

    # --- runs ---
    def record_run(self, alert_id: int, ts: str, status: str, triggered: bool,
                   notified: bool, row_count: Optional[int], message: str,
                   result_hash: str = "") -> None:
        self.conn.execute(
            "INSERT INTO alert_runs(alert_id, ts, status, triggered, notified, row_count,"
            " message, result_hash) VALUES (?,?,?,?,?,?,?,?)",
            (alert_id, ts, status, 1 if triggered else 0, 1 if notified else 0,
             row_count, message, result_hash),
        )
        self.conn.commit()

    def latest_run(self, alert_id: int) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT * FROM alert_runs WHERE alert_id=? ORDER BY id DESC LIMIT 1", (alert_id,)
        ).fetchone()
        return dict(r) if r else None

    def list_runs(self, alert_id: int, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM alert_runs WHERE alert_id=? ORDER BY id DESC LIMIT ?", (alert_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_runs_since(self, since_ts: str, triggered_only: bool = False) -> list[dict]:
        """All runs (any alert) with ts >= since_ts, oldest first.

        Used to build day/period reports across every alert in one pass.
        """
        sql = "SELECT * FROM alert_runs WHERE ts>=?"
        if triggered_only:
            sql += " AND triggered=1"
        sql += " ORDER BY ts ASC, id ASC"
        return [dict(r) for r in self.conn.execute(sql, (since_ts,)).fetchall()]

    # --- captured result snapshots (latest per alert per day, N-day retention) ---
    @staticmethod
    def _result_cutoff(retention_days: int, anchor_day: str) -> str:
        return (date.fromisoformat(anchor_day) - timedelta(days=retention_days - 1)).isoformat()

    def get_result_retention_days(self) -> int:
        try:
            return max(1, int(self.get_setting("result_retention_days")))
        except (TypeError, ValueError):
            return RESULT_RETENTION_DAYS

    def set_result_retention_days(self, days: int) -> None:
        days = max(1, int(days))
        self.set_setting("result_retention_days", str(days))
        row = self.conn.execute("SELECT max(day) AS d FROM alert_results").fetchone()
        if row and row["d"]:                           # prune existing to the new window now
            self.conn.execute("DELETE FROM alert_results WHERE day < ?",
                              (self._result_cutoff(days, row["d"]),))
            self.conn.commit()

    def get_result_max_rows(self) -> int:
        try:
            return max(1, int(self.get_setting("result_max_rows")))
        except (TypeError, ValueError):
            return RESULT_MAX_ROWS

    def set_result_max_rows(self, rows: int) -> None:
        self.set_setting("result_max_rows", str(max(1, int(rows))))

    def save_result(self, alert_id: int, ts: str, df, max_rows: Optional[int] = None,
                    retention_days: Optional[int] = None) -> None:
        """Upsert the latest result snapshot for an alert on ``ts``'s day, then
        prune snapshots older than the retention window.

        Only the first ``max_rows`` rows are serialized, so a huge result never
        gets fully materialized into JSON; ``truncated`` records that it was
        capped and ``row_count`` keeps the true size for the report.
        """
        if max_rows is None:
            max_rows = self.get_result_max_rows()
        if retention_days is None:
            retention_days = self.get_result_retention_days()
        day = ts[:10]                                  # YYYY-MM-DD from the ISO timestamp
        full = len(df)
        capped = df.head(max_rows)                     # cap BEFORE serialize (bounds memory/DB)
        columns = [str(c) for c in capped.columns]
        rows = [
            {str(k): _jsonable(v) for k, v in rec.items()}
            for rec in capped.to_dict(orient="records")
        ]
        self.conn.execute(
            "INSERT INTO alert_results(alert_id, day, ts, row_count, truncated,"
            " columns_json, rows_json) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(alert_id, day) DO UPDATE SET"
            " ts=excluded.ts, row_count=excluded.row_count, truncated=excluded.truncated,"
            " columns_json=excluded.columns_json, rows_json=excluded.rows_json",
            (alert_id, day, ts, full, 1 if full > max_rows else 0,
             json.dumps(columns), json.dumps(rows)),
        )
        self.conn.execute("DELETE FROM alert_results WHERE day < ?",
                          (self._result_cutoff(retention_days, day),))
        self.conn.commit()

    def get_result(self, alert_id: int, day: str) -> Optional[dict]:
        """The stored snapshot for an alert on a given ``day`` (YYYY-MM-DD), or None."""
        r = self.conn.execute(
            "SELECT * FROM alert_results WHERE alert_id=? AND day=?", (alert_id, day)
        ).fetchone()
        if not r:
            return None
        return {
            "ts": r["ts"], "row_count": r["row_count"], "truncated": bool(r["truncated"]),
            "columns": json.loads(r["columns_json"]), "rows": json.loads(r["rows_json"]),
        }

    def result_days(self, alert_id: Optional[int] = None) -> list[str]:
        """Distinct days that have snapshots, newest first (optionally per alert)."""
        if alert_id is None:
            rows = self.conn.execute(
                "SELECT DISTINCT day FROM alert_results ORDER BY day DESC").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT DISTINCT day FROM alert_results WHERE alert_id=? ORDER BY day DESC",
                (alert_id,)).fetchall()
        return [r["day"] for r in rows]

    def last_notified_at(self, alert_id: int) -> Optional[str]:
        r = self.conn.execute(
            "SELECT ts FROM alert_runs WHERE alert_id=? AND notified=1 ORDER BY id DESC LIMIT 1",
            (alert_id,),
        ).fetchone()
        return r["ts"] if r else None

    def last_notified_hash(self, alert_id: int) -> Optional[str]:
        """Result fingerprint of the most recent notification (for 'on_change' re-arm)."""
        r = self.conn.execute(
            "SELECT result_hash FROM alert_runs WHERE alert_id=? AND notified=1"
            " ORDER BY id DESC LIMIT 1", (alert_id,),
        ).fetchone()
        return r["result_hash"] if r else None

    # --- settings ---
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()
