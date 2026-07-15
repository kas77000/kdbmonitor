# kdbmonitor/core/client.py
from __future__ import annotations

from typing import Callable, Protocol

import pandas as pd

from kdbmonitor.core.models import Connection


class KdbClient(Protocol):
    def query(self, qsql: str) -> pd.DataFrame: ...


class FakeClient:
    """Test double: returns canned DataFrames keyed by exact query string."""
    def __init__(self, responses: dict[str, pd.DataFrame]):
        self.responses = responses
        self.calls: list[str] = []

    def query(self, qsql: str) -> pd.DataFrame:
        self.calls.append(qsql)
        if qsql not in self.responses:
            raise KeyError(f"FakeClient has no canned response for: {qsql}")
        return self.responses[qsql]


class PyKxClient:
    """Real client wrapping a pykx QConnection. Imports pykx lazily."""
    def __init__(self, host: str, port: int):
        import pykx as kx
        self._kx = kx
        self.host = host
        self.port = port
        self._conn = kx.SyncQConnection(host=host, port=port)

    def query(self, qsql: str) -> pd.DataFrame:
        try:
            return self._conn(qsql).pd()
        except Exception:
            # reconnect once, then retry
            self._conn = self._kx.SyncQConnection(host=self.host, port=self.port)
            return self._conn(qsql).pd()


class ConnectionManager:
    """Caches one client per (host, port)."""
    def __init__(self, client_factory: Callable[[str, int], object] = PyKxClient):
        self._factory = client_factory
        self._cache: dict[tuple[str, int], object] = {}

    def get(self, conn: Connection):
        key = (conn.host, conn.port)
        if key not in self._cache:
            self._cache[key] = self._factory(conn.host, conn.port)
        return self._cache[key]
