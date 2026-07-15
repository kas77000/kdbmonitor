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
