# kdbmonitor/core/client.py
from __future__ import annotations

from typing import Callable, Protocol

import pandas as pd

from kdbmonitor.core.models import Connection


class KdbClient(Protocol):
    def query(self, qsql: str) -> pd.DataFrame: ...


# kdb+ has nowhere in an integer vector to put "unknown", so a null *is* a
# value: the lowest one the width can hold. pandas has no idea, and pykx hands
# them straight over — so an order that missed a left join arrives holding
# -2,147,483,648, and summing a column of those gives a rejection count in the
# billions instead of a gap. These three numbers are the nulls, never data.
Q_INT_NULLS = {"int16": -32768,
               "int32": -2147483648,
               "int64": -9223372036854775808}


def _scrub(values: pd.Series) -> "pd.Series | None":
    """One column's sentinels as NaN, or None when it holds none to replace.

    None rather than the column back, so a caller can tell "nothing to do" from
    "here it is" without comparing identities pandas makes no promise about.
    """
    sentinel = Q_INT_NULLS.get(str(values.dtype).lower())
    if sentinel is None:
        return None
    hit = values == sentinel
    return values.astype("float64").mask(hit) if hit.any() else None


def nulls_to_nan(data):
    """kdb+ integer nulls as NaN, so nothing sums or plots them as numbers.

    Only columns that actually hold a sentinel are touched, and only those
    become floats — a frame of honest integers comes back untouched, dtypes and
    all. Wrong-looking is recoverable; wrong-and-plausible is not, which is why
    this happens on arrival rather than being left to each query to remember.

    Every result passes through here, and not every result is a table. A q
    *vector* — what ``tables[]`` and ``cols`` return, so what introspection is
    made of — arrives as a 1-D Series with no columns to iterate, and a count
    arrives as a bare scalar. They are scrubbed as they are, or handed back
    untouched; asking a Series for its columns is what broke introspection
    against a real server while every test drove a client that skips this.
    """
    if data is None:
        return data
    if isinstance(data, pd.Series):
        scrubbed = _scrub(data)
        return data if scrubbed is None else scrubbed
    if not isinstance(data, pd.DataFrame) or data.empty:
        return data

    out = data
    for col in data.columns:
        scrubbed = _scrub(data[col])
        if scrubbed is None:
            continue
        if out is data:
            out = data.copy()
        out[col] = scrubbed
    return out


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
            return nulls_to_nan(self._conn(qsql).pd())
        except Exception:
            # reconnect once, then retry
            self._conn = self._kx.SyncQConnection(host=self.host, port=self.port)
            return nulls_to_nan(self._conn(qsql).pd())


class ConnectionManager:
    """Caches one client per (host, port).

    Connections whose host is the sentinel ``"demo"`` are served by an
    in-memory ``MockKdbClient`` instead of a real pykx connection, so the app
    can be exercised end-to-end without any KDB server.
    """
    def __init__(self, client_factory: Callable[[str, int], object] = PyKxClient,
                 mock_factory: Callable[[], object] | None = None):
        self._factory = client_factory
        self._mock_factory = mock_factory
        self._cache: dict[tuple[str, int], object] = {}

    def get(self, conn: Connection):
        key = (conn.host, conn.port)
        if key not in self._cache:
            if conn.host == "demo":
                self._cache[key] = self._make_mock(conn)
            else:
                self._cache[key] = self._factory(conn.host, conn.port)
        return self._cache[key]

    def _make_mock(self, conn: Connection):
        """Pick the mock matching the connection's kind. A custom
        ``mock_factory`` (tests) wins and receives the connection."""
        if self._mock_factory is not None:
            return self._mock_factory(conn)
        from kdbmonitor.core.mock import MockHdbClient, MockKdbClient
        return MockHdbClient() if conn.kind == "historical" else MockKdbClient()
