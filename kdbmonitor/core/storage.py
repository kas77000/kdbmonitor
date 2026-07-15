# kdbmonitor/core/storage.py
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from kdbmonitor.core.models import Connection, Alert, alert_to_json, alert_from_json


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
                message TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self.conn.commit()

    # --- connections ---
    def add_connection(self, c: Connection) -> int:
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
    def add_alert(self, a: Alert) -> int:
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
                   notified: bool, row_count: Optional[int], message: str) -> None:
        self.conn.execute(
            "INSERT INTO alert_runs(alert_id, ts, status, triggered, notified, row_count, message)"
            " VALUES (?,?,?,?,?,?,?)",
            (alert_id, ts, status, 1 if triggered else 0, 1 if notified else 0, row_count, message),
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

    def last_notified_at(self, alert_id: int) -> Optional[str]:
        r = self.conn.execute(
            "SELECT ts FROM alert_runs WHERE alert_id=? AND notified=1 ORDER BY id DESC LIMIT 1",
            (alert_id,),
        ).fetchone()
        return r["ts"] if r else None

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
