r"""In-memory mock KDB server for demo / testing without a real connection.

`MockKdbClient` satisfies the same `.query(qsql) -> DataFrame` contract as
`PyKxClient`. It answers the three query shapes the app issues:
  - `tables[]`            -> the available table names
  - `cols \`t`            -> the columns of table t
  - `select from t ...`   -> synthetic rows for table t (best-effort `sym in` filter)

Market data (QATT) varies smoothly with wall-clock time so alert conditions
naturally flip between armed and triggered, which makes the demo feel live.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

import pandas as pd

from kdbmonitor.core.models import Connection

_SYMS = ["AAPL", "MSFT", "GOOG", "AMZN", "TSLA"]

# table -> ordered columns
SCHEMA: dict[str, list[str]] = {
    "QATT": ["sym", "bid", "ask", "volume", "time"],
    "target": ["sym", "orderId", "qty", "side", "price"],
    "work_order": ["sym", "workOrderId", "filledQty", "leavesQty", "state"],
    "target_state": ["sym", "orderId", "state", "pct_complete"],
}


def _phase() -> int:
    t = datetime.now(timezone.utc)
    return t.hour * 3600 + t.minute * 60 + t.second


def _qatt() -> pd.DataFrame:
    phase = _phase()
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    rows = []
    for i, s in enumerate(_SYMS):
        base = 100 + i * 5
        bid = round(base + 8 * math.sin(phase / 30.0 + i), 2)
        rows.append({
            "sym": s, "bid": bid, "ask": round(bid + 0.05, 2),
            "volume": 1000 * (i + 1) + (phase % 500), "time": stamp,
        })
    return pd.DataFrame(rows)


def _target() -> pd.DataFrame:
    return pd.DataFrame([
        {"sym": "AAPL", "orderId": 1001, "qty": 5000, "side": "buy", "price": 101.2},
        {"sym": "MSFT", "orderId": 1002, "qty": 3000, "side": "sell", "price": 402.5},
        {"sym": "GOOG", "orderId": 1003, "qty": 1200, "side": "buy", "price": 138.9},
    ])


def _work_order() -> pd.DataFrame:
    return pd.DataFrame([
        {"sym": "AAPL", "workOrderId": 9001, "filledQty": 4200, "leavesQty": 800, "state": "working"},
        {"sym": "MSFT", "workOrderId": 9002, "filledQty": 3000, "leavesQty": 0, "state": "done"},
        {"sym": "GOOG", "workOrderId": 9003, "filledQty": 0, "leavesQty": 1200, "state": "new"},
    ])


def _target_state() -> pd.DataFrame:
    return pd.DataFrame([
        {"sym": "AAPL", "orderId": 1001, "state": "working", "pct_complete": 84.0},
        {"sym": "MSFT", "orderId": 1002, "state": "done", "pct_complete": 100.0},
        {"sym": "GOOG", "orderId": 1003, "state": "new", "pct_complete": 0.0},
    ])


_BUILDERS = {
    "QATT": _qatt, "target": _target,
    "work_order": _work_order, "target_state": _target_state,
}


class MockKdbClient:
    """Serves synthetic KDB tables. No network, no pykx."""

    def query(self, qsql: str) -> pd.DataFrame:
        q = qsql.strip()
        if "tables[]" in q:
            return pd.DataFrame({"t": list(SCHEMA.keys())})
        if q.startswith("cols"):
            m = re.search(r"`(\w+)", q)
            table = m.group(1) if m else ""
            return pd.DataFrame({"c": SCHEMA.get(table, [])})

        m = re.search(r"from\s+(\w+)", q)
        if not m or m.group(1) not in _BUILDERS:
            return pd.DataFrame()
        df = _BUILDERS[m.group(1)]()

        # Best-effort `sym in `A`B` filter so chains behave believably.
        if "sym in" in q and "sym" in df.columns:
            syms = re.findall(r"`(\w+)", q.split("sym in", 1)[1])
            if syms:
                df = df[df["sym"].isin(syms)].reset_index(drop=True)
        return df


def demo_connection_specs() -> list[Connection]:
    """Two pre-introspected demo connections (host 'demo' routes to the mock)."""
    ts = datetime.now(timezone.utc).isoformat()
    return [
        Connection(id=None, name="kdp_demo", host="demo", port=1,
                   schema={"QATT": SCHEMA["QATT"]}, last_introspected_at=ts),
        Connection(id=None, name="orders_demo", host="demo", port=2,
                   schema={k: SCHEMA[k] for k in ("target", "work_order", "target_state")},
                   last_introspected_at=ts),
    ]
