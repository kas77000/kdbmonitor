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
from datetime import datetime, timedelta, timezone

import pandas as pd

from kdbmonitor.core.models import Connection

_SYMS = ["AAPL", "MSFT", "GOOG", "AMZN", "TSLA"]

# table -> ordered columns
SCHEMA: dict[str, list[str]] = {
    "QATT": ["sym", "bid", "ask", "volume", "time",
             "qbid", "qask", "lastPrice", "pctChange"],
    "target": ["sym", "orderId", "qty", "side", "price", "algo"],
    "work_order": ["sym", "workOrderId", "filledQty", "leavesQty", "state"],
    "target_state": ["sym", "orderId", "state", "pct_complete"],
    # Reference data: what an instrument *is*, not what it did today.
    "instrument": ["sym", "name", "sector", "currency", "lotSize", "exchange"],
}

# Names that can go "limit locked" in the demo; kept in sync with _target so
# the cross-server chain (limit stock -> our order) actually intersects.
_LIMIT_SYMS = ["AAPL", "MSFT", "GOOG"]


def _phase() -> int:
    t = datetime.now(timezone.utc)
    return t.hour * 3600 + t.minute * 60 + t.second


def _limit_state() -> tuple[str | None, str | None]:
    """Which sym is locked right now, and which way — rotates on wall-clock.

    Cycles A(up) -> A(down) -> M(up) -> ... -> nothing-locked, so an alert
    watching for limit stocks naturally flips triggered/armed every ~20s.
    """
    step = _phase() // 20
    slot = step % (len(_LIMIT_SYMS) + 1)       # last slot = nothing locked (armed)
    if slot == len(_LIMIT_SYMS):
        return None, None
    return _LIMIT_SYMS[slot], ("up" if step % 2 == 0 else "down")


def _qatt() -> pd.DataFrame:
    phase = _phase()
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    locked_sym, direction = _limit_state()
    rows = []
    for i, s in enumerate(_SYMS):
        base = 100 + i * 5
        bid = round(base + 8 * math.sin(phase / 30.0 + i), 2)
        ask = round(bid + 0.05, 2)
        qbid, qask, last = bid, ask, bid
        pct = round(8 * math.sin(phase / 30.0 + i) / base * 100, 2)
        if s == locked_sym and direction == "up":       # locked limit-up: no offers
            qbid, qask, last, pct = round(base * 1.10, 2), 0.0, round(base * 1.10, 2), 10.0
        elif s == locked_sym and direction == "down":   # locked limit-down: no bids
            qbid, qask, last, pct = 0.0, round(base * 0.90, 2), round(base * 0.90, 2), -10.0
        rows.append({
            "sym": s, "bid": bid, "ask": ask,
            "volume": 1000 * (i + 1) + (phase % 500), "time": stamp,
            "qbid": qbid, "qask": qask, "lastPrice": last, "pctChange": pct,
        })
    return pd.DataFrame(rows)


def _target() -> pd.DataFrame:
    return pd.DataFrame([
        {"sym": "AAPL", "orderId": 1001, "qty": 5000, "side": "buy", "price": 101.2, "algo": "VWAP"},
        {"sym": "MSFT", "orderId": 1002, "qty": 3000, "side": "sell", "price": 402.5, "algo": "TWAP"},
        {"sym": "GOOG", "orderId": 1003, "qty": 1200, "side": "buy", "price": 138.9, "algo": "IS"},
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


def _instrument() -> pd.DataFrame:
    """Static reference data — deliberately time-invariant, unlike the others."""
    return pd.DataFrame([
        {"sym": "AAPL", "name": "Apple Inc", "sector": "Technology",
         "currency": "USD", "lotSize": 1, "exchange": "NASDAQ"},
        {"sym": "MSFT", "name": "Microsoft Corp", "sector": "Technology",
         "currency": "USD", "lotSize": 1, "exchange": "NASDAQ"},
        {"sym": "GOOG", "name": "Alphabet Inc", "sector": "Technology",
         "currency": "USD", "lotSize": 1, "exchange": "NASDAQ"},
        {"sym": "AMZN", "name": "Amazon.com Inc", "sector": "Consumer",
         "currency": "USD", "lotSize": 1, "exchange": "NASDAQ"},
        {"sym": "TSLA", "name": "Tesla Inc", "sector": "Consumer",
         "currency": "USD", "lotSize": 1, "exchange": "NASDAQ"},
    ])


_BUILDERS = {
    "QATT": _qatt, "target": _target,
    "work_order": _work_order, "target_state": _target_state,
    "instrument": _instrument,
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

        # Limit up/down detection: any QATT query touching the order book.
        # Returns one row per currently-locked sym (empty when nothing is locked).
        if "qbid" in q and "QATT" in q:
            qatt = _qatt()
            up = (qatt["qask"] == 0) & (qatt["qbid"] > 0)
            down = (qatt["qbid"] == 0) & (qatt["qask"] > 0)
            hit = qatt[up | down]
            lim = pd.DataFrame({
                "sym": hit["sym"].tolist(),
                "dir": ["up" if u else "down" for u in up[up | down].tolist()],
                "lastPrice": hit["lastPrice"].tolist(),
                "pctChange": hit["pctChange"].tolist(),
            })
            return self._sym_filter(q, lim)

        m = re.search(r"from\s+(\w+)", q)
        if not m or m.group(1) not in _BUILDERS:
            return pd.DataFrame()
        return self._sym_filter(q, _BUILDERS[m.group(1)]())

    @staticmethod
    def _sym_filter(q: str, df: pd.DataFrame) -> pd.DataFrame:
        """Apply a best-effort ``sym in `A`B`` filter. An empty in-list (e.g.
        ``sym in `$()`` from an empty upstream step) matches nothing."""
        if "sym in" in q and "sym" in df.columns:
            syms = re.findall(r"`(\w+)", q.split("sym in", 1)[1])
            df = df[df["sym"].isin(syms)].reset_index(drop=True)
        return df


_HDB_DAYS = 30            # how much history the demo server pretends to hold


def _with_dates(df: pd.DataFrame, days: int = _HDB_DAYS) -> pd.DataFrame:
    """Repeat a real-time frame once per day for the last ``days`` days.

    Quantities are scaled by the day index so a chart over a range is not flat.
    """
    today = datetime.now(timezone.utc).date()
    frames = []
    for i in range(days):
        d = df.copy()
        d.insert(0, "date", today - timedelta(days=days - 1 - i))
        for col in ("qty", "filledQty", "leavesQty", "pct_complete"):
            if col in d.columns:
                d[col] = (d[col] * (0.5 + i / days)).round(2)
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


_DATE_WITHIN = re.compile(
    r"date\s+within\s*\(\s*(\d{4}\.\d{2}\.\d{2})\s*;\s*(\d{4}\.\d{2}\.\d{2})\s*\)")


class MockHdbClient(MockKdbClient):
    """Historical twin of ``MockKdbClient``: the same tables plus a ``date``
    column, with best-effort support for a ``date within (d1;d2)`` constraint."""

    def query(self, qsql: str) -> pd.DataFrame:
        q = qsql.strip()
        if "tables[]" in q:
            return pd.DataFrame({"t": list(SCHEMA.keys())})
        if q.startswith("cols"):
            m = re.search(r"`(\w+)", q)
            cols = SCHEMA.get(m.group(1) if m else "", [])
            return pd.DataFrame({"c": (["date"] + cols) if cols else []})

        m = re.search(r"from\s+(\w+)", q)
        if not m or m.group(1) not in _BUILDERS:
            return pd.DataFrame()
        df = _with_dates(_BUILDERS[m.group(1)]())

        w = _DATE_WITHIN.search(q)
        if w:
            lo = datetime.strptime(w.group(1), "%Y.%m.%d").date()
            hi = datetime.strptime(w.group(2), "%Y.%m.%d").date()
            df = df[(df["date"] >= lo) & (df["date"] <= hi)].reset_index(drop=True)
        return self._sym_filter(q, df)


def demo_connection_specs() -> list[Connection]:
    """Pre-introspected demo connections (host 'demo' routes to a mock).

    ``orders_demo`` / ``orders_hdb_demo`` form one environment, so the
    realtime/historical switch is demoable with no real KDB.
    """
    ts = datetime.now(timezone.utc).isoformat()
    order_tables = ("target", "work_order", "target_state")
    return [
        Connection(id=None, name="kdp_demo", host="demo", port=1,
                   schema={"QATT": SCHEMA["QATT"]}, last_introspected_at=ts,
                   kind="realtime", env="quotes"),
        Connection(id=None, name="refdata_demo", host="demo", port=4,
                   schema={"instrument": SCHEMA["instrument"]},
                   last_introspected_at=ts,
                   kind="marketdata", env="marketdata"),
        Connection(id=None, name="orders_demo", host="demo", port=2,
                   schema={k: SCHEMA[k] for k in order_tables},
                   last_introspected_at=ts, kind="realtime", env="orders"),
        Connection(id=None, name="orders_hdb_demo", host="demo", port=3,
                   schema={k: ["date"] + SCHEMA[k] for k in order_tables},
                   last_introspected_at=ts, kind="historical", env="orders"),
    ]
