# KdbMonitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Streamlit app that lets the Algo trading team register KDB servers (host+port), build chains of KDB queries through a guided builder, and get notified (in-app/sound, email, Teams/Slack) when a chain's final result meets a trigger condition — while the app is open.

**Architecture:** All logic lives in a UI-independent `kdbmonitor/core/` package (storage, connections, schema, chain execution, conditions, re-arm, notifiers) tested against a fake KDB client. A thin Streamlit UI (`app.py` + `kdbmonitor/ui/`) provides Admin/Builder/Monitor views. Persistence is SQLite. Alerts are evaluated on the Monitor page's auto-refresh loop.

**Tech Stack:** Python 3.11+, Streamlit, pykx, pandas, SQLite (stdlib `sqlite3`), `requests` (webhooks), `smtplib` (email), pytest.

---

## File Structure

```
KdbMonitor/
├── app.py                          # Streamlit entry, role switch, page routing
├── requirements.txt
├── pyproject.toml                  # pytest config + package metadata
├── kdbmonitor/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── models.py               # dataclasses + JSON (de)serialization
│   │   ├── storage.py              # SQLite CRUD: connections, alerts, runs
│   │   ├── qfmt.py                 # q value/list literal formatting
│   │   ├── chain.py                # build step qSQL, ref substitution, run_chain
│   │   ├── conditions.py           # evaluate trigger condition on a DataFrame
│   │   ├── rearm.py                # pure re-arm decision
│   │   ├── client.py               # KdbClient protocol, PyKxClient, FakeClient, ConnectionManager
│   │   ├── schema.py               # introspect tables/columns
│   │   ├── notifiers.py            # Notifier interface + in-app/email/webhook + dispatch
│   │   └── evaluate.py             # evaluate_alert: ties chain+condition+rearm+message
│   └── ui/
│       ├── __init__.py
│       ├── admin.py                # register connections + SMTP settings
│       ├── builder.py              # alert CRUD + chain builder
│       └── monitor.py             # live loop, status table, in-app notifications
└── tests/
    ├── __init__.py
    ├── test_models.py
    ├── test_storage.py
    ├── test_qfmt.py
    ├── test_chain.py
    ├── test_conditions.py
    ├── test_rearm.py
    ├── test_client.py
    ├── test_schema.py
    ├── test_notifiers.py
    └── test_evaluate.py
```

---

## Task 0: Project scaffolding

**Files:**
- Create: `requirements.txt`, `pyproject.toml`, `kdbmonitor/__init__.py`, `kdbmonitor/core/__init__.py`, `kdbmonitor/ui/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Create `requirements.txt`**

```
streamlit>=1.36
pykx>=2.5
pandas>=2.0
requests>=2.31
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "kdbmonitor"
version = "0.1.0"
requires-python = ">=3.11"

[tool.setuptools.packages.find]
include = ["kdbmonitor*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 3: Create empty package files**

Create `kdbmonitor/__init__.py`, `kdbmonitor/core/__init__.py`, `kdbmonitor/ui/__init__.py`, `tests/__init__.py` each as empty files.

- [ ] **Step 4: Create and activate a virtualenv, install pytest + pandas**

Run (PowerShell):
```
python -m venv .venv; .venv\Scripts\Activate.ps1; pip install pytest pandas requests
```
Expected: installs succeed. (pykx/streamlit installed later when running the app; core tests only need pandas.)

- [ ] **Step 5: Verify pytest runs (no tests yet)**

Run: `python -m pytest`
Expected: "no tests ran".

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pyproject.toml kdbmonitor tests
git commit -m "chore: scaffold kdbmonitor package"
```

---

## Task 1: Data models (`models.py`)

**Files:**
- Create: `kdbmonitor/core/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from kdbmonitor.core.models import (
    Filter, Step, TriggerCondition, RearmPolicy, Channels, Alert, Connection,
    alert_to_json, alert_from_json,
)


def test_alert_json_roundtrip():
    alert = Alert(
        id=None,
        name="AAPL bid check",
        enabled=True,
        poll_interval_secs=30,
        steps=[
            Step(server="orders", table="target", mode="form",
                 filters=[Filter(column="sym", op="in", value=["AAPL", "MSFT"], value_type="symbol")],
                 raw_qsql=None, output_name="step1"),
            Step(server="kdp", table="QATT", mode="raw",
                 filters=[], raw_qsql="select from QATT where sym in {{step1.sym}}",
                 output_name="step2"),
        ],
        trigger=TriggerCondition(type="any_row", column="bid", op=">", value=100.0, n=None, agg=None),
        channels=Channels(in_app=True, sound=True, email_to=["me@x.com"], webhook_urls=[]),
        rearm=RearmPolicy(mode="transition", cooldown_secs=0),
    )
    restored = alert_from_json(alert_to_json(alert))
    assert restored == alert
    assert restored.steps[0].filters[0].value == ["AAPL", "MSFT"]
    assert restored.trigger.value == 100.0


def test_connection_defaults():
    c = Connection(id=None, name="orders", host="localhost", port=5010)
    assert c.schema == {}
    assert c.last_introspected_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: kdbmonitor.core.models`.

- [ ] **Step 3: Write minimal implementation**

```python
# kdbmonitor/core/models.py
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Filter:
    column: str
    op: str                      # = <> < <= > >= in
    value: Any                   # scalar, or list for op == "in"
    value_type: str              # symbol | number | string


@dataclass
class Step:
    server: str                  # connection name
    table: str
    mode: str                    # form | raw
    filters: list[Filter] = field(default_factory=list)
    raw_qsql: Optional[str] = None
    output_name: str = "step1"


@dataclass
class TriggerCondition:
    type: str                    # no_rows | has_rows | row_count_gte | any_row | all_rows | aggregate
    column: Optional[str] = None
    op: Optional[str] = None
    value: Any = None
    n: Optional[int] = None      # for row_count_gte
    agg: Optional[str] = None    # max | min | avg | sum (for aggregate)


@dataclass
class RearmPolicy:
    mode: str = "transition"     # transition | cooldown | every_tick
    cooldown_secs: int = 0


@dataclass
class Channels:
    in_app: bool = True
    sound: bool = True
    email_to: list[str] = field(default_factory=list)
    webhook_urls: list[str] = field(default_factory=list)


@dataclass
class Alert:
    id: Optional[int]
    name: str
    enabled: bool
    poll_interval_secs: int
    steps: list[Step]
    trigger: TriggerCondition
    channels: Channels
    rearm: RearmPolicy


@dataclass
class Connection:
    id: Optional[int]
    name: str
    host: str
    port: int
    schema: dict[str, list[str]] = field(default_factory=dict)  # table -> columns
    last_introspected_at: Optional[str] = None


def alert_to_json(alert: Alert) -> str:
    return json.dumps(asdict(alert))


def alert_from_json(raw: str) -> Alert:
    d = json.loads(raw)
    return Alert(
        id=d["id"],
        name=d["name"],
        enabled=d["enabled"],
        poll_interval_secs=d["poll_interval_secs"],
        steps=[
            Step(
                server=s["server"], table=s["table"], mode=s["mode"],
                filters=[Filter(**f) for f in s["filters"]],
                raw_qsql=s["raw_qsql"], output_name=s["output_name"],
            )
            for s in d["steps"]
        ],
        trigger=TriggerCondition(**d["trigger"]),
        channels=Channels(**d["channels"]),
        rearm=RearmPolicy(**d["rearm"]),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/models.py tests/test_models.py
git commit -m "feat: add core data models with JSON roundtrip"
```

---

## Task 2: Storage — connections CRUD (`storage.py`)

**Files:**
- Create: `kdbmonitor/core/storage.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage.py
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.models import Connection


def test_connection_crud():
    store = Storage(":memory:")
    store.init_db()

    cid = store.add_connection(Connection(id=None, name="orders", host="h", port=5010))
    assert isinstance(cid, int)

    conns = store.list_connections()
    assert len(conns) == 1 and conns[0].name == "orders" and conns[0].id == cid

    got = store.get_connection(cid)
    got.schema = {"target": ["sym", "orderId"]}
    got.last_introspected_at = "2026-07-15T10:00:00"
    store.update_connection(got)
    assert store.get_connection(cid).schema == {"target": ["sym", "orderId"]}

    store.delete_connection(cid)
    assert store.list_connections() == []


def test_connection_name_unique():
    store = Storage(":memory:")
    store.init_db()
    store.add_connection(Connection(id=None, name="dup", host="h", port=1))
    import pytest
    with pytest.raises(Exception):
        store.add_connection(Connection(id=None, name="dup", host="h", port=2))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_storage.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_storage.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/storage.py tests/test_storage.py
git commit -m "feat: add SQLite storage with connection CRUD"
```

---

## Task 3: Storage — alerts CRUD + runs

**Files:**
- Modify: `kdbmonitor/core/storage.py`
- Test: `tests/test_storage.py` (add tests)

- [ ] **Step 1: Write the failing test (append to `tests/test_storage.py`)**

```python
def _sample_alert():
    from kdbmonitor.core.models import (
        Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels,
    )
    return Alert(
        id=None, name="a1", enabled=True, poll_interval_secs=30,
        steps=[Step(server="orders", table="target", mode="form",
                    filters=[Filter("sym", "in", ["AAPL"], "symbol")],
                    raw_qsql=None, output_name="step1")],
        trigger=TriggerCondition(type="has_rows"),
        channels=Channels(), rearm=RearmPolicy(),
    )


def test_alert_crud_and_toggle():
    store = Storage(":memory:")
    store.init_db()

    aid = store.add_alert(_sample_alert())
    got = store.get_alert(aid)
    assert got.name == "a1" and got.id == aid and got.enabled is True

    got.name = "a1-renamed"
    store.update_alert(got)
    assert store.get_alert(aid).name == "a1-renamed"

    store.set_alert_enabled(aid, False)
    assert store.get_alert(aid).enabled is False
    assert store.list_alerts()[0].enabled is False

    store.delete_alert(aid)
    assert store.list_alerts() == []


def test_runs_record_and_latest():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    store.record_run(aid, ts="2026-07-15T10:00:00", status="armed", triggered=False,
                     notified=False, row_count=0, message="")
    store.record_run(aid, ts="2026-07-15T10:00:30", status="triggered", triggered=True,
                     notified=True, row_count=3, message="hit")
    latest = store.latest_run(aid)
    assert latest["status"] == "triggered" and latest["triggered"] == 1
    assert len(store.list_runs(aid)) == 2
    assert store.latest_run(9999) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_storage.py -v`
Expected: FAIL — `Storage` has no attribute `add_alert`.

- [ ] **Step 3: Write minimal implementation (append methods to `Storage` in `storage.py`)**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_storage.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/storage.py tests/test_storage.py
git commit -m "feat: add alert CRUD, enable toggle, and run history"
```

---

## Task 4: q literal formatting (`qfmt.py`)

**Files:**
- Create: `kdbmonitor/core/qfmt.py`
- Test: `tests/test_qfmt.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qfmt.py
from kdbmonitor.core.qfmt import format_q_value, format_q_list


def test_format_scalar():
    assert format_q_value("AAPL", "symbol") == "`AAPL"
    assert format_q_value(100, "number") == "100"
    assert format_q_value(100.5, "number") == "100.5"
    assert format_q_value('he"llo', "string") == '"he\\"llo"'


def test_format_list_symbol():
    assert format_q_list(["AAPL", "MSFT"], "symbol") == "`AAPL`MSFT"
    assert format_q_list(["AAPL"], "symbol") == "enlist `AAPL"


def test_format_list_number():
    assert format_q_list([1, 2, 3], "number") == "1 2 3"
    assert format_q_list([5], "number") == "enlist 5"


def test_format_list_string():
    assert format_q_list(["a", "b"], "string") == '("a";"b")'
    assert format_q_list(["a"], "string") == 'enlist "a"'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_qfmt.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```python
# kdbmonitor/core/qfmt.py
from __future__ import annotations

from typing import Any


def format_q_value(value: Any, value_type: str) -> str:
    if value_type == "symbol":
        return "`" + str(value)
    if value_type == "number":
        return str(value)
    if value_type == "string":
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    raise ValueError(f"unknown value_type: {value_type}")


def format_q_list(values: list, value_type: str) -> str:
    if value_type == "symbol":
        joined = "".join("`" + str(v) for v in values)
        return joined if len(values) > 1 else "enlist " + joined
    if value_type == "number":
        joined = " ".join(str(v) for v in values)
        return joined if len(values) > 1 else "enlist " + joined
    if value_type == "string":
        parts = [format_q_value(v, "string") for v in values]
        return "(" + ";".join(parts) + ")" if len(values) > 1 else "enlist " + parts[0]
    raise ValueError(f"unknown value_type: {value_type}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_qfmt.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/qfmt.py tests/test_qfmt.py
git commit -m "feat: add q literal formatting helpers"
```

---

## Task 5: Chain — build step qSQL from a form step

**Files:**
- Create: `kdbmonitor/core/chain.py`
- Test: `tests/test_chain.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chain.py
from kdbmonitor.core.models import Step, Filter
from kdbmonitor.core.chain import build_step_qsql


def test_build_no_filters():
    step = Step(server="orders", table="target", mode="form", filters=[], output_name="step1")
    assert build_step_qsql(step) == "select from target"


def test_build_with_filters():
    step = Step(
        server="orders", table="target", mode="form",
        filters=[
            Filter("sym", "in", ["AAPL", "MSFT"], "symbol"),
            Filter("qty", ">", 100, "number"),
        ],
        output_name="step1",
    )
    assert build_step_qsql(step) == "select from target where sym in `AAPL`MSFT, qty>100"


def test_build_raw_mode_returns_raw():
    step = Step(server="orders", table="target", mode="raw",
                filters=[], raw_qsql="select from x where a=1", output_name="step1")
    assert build_step_qsql(step) == "select from x where a=1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chain.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```python
# kdbmonitor/core/chain.py
from __future__ import annotations

from kdbmonitor.core.models import Step
from kdbmonitor.core.qfmt import format_q_value, format_q_list


def _filter_clause(f) -> str:
    if f.op == "in":
        return f"{f.column} in {format_q_list(f.value, f.value_type)}"
    return f"{f.column}{f.op}{format_q_value(f.value, f.value_type)}"


def build_step_qsql(step: Step) -> str:
    if step.mode == "raw":
        return step.raw_qsql or ""
    base = f"select from {step.table}"
    if not step.filters:
        return base
    clauses = ", ".join(_filter_clause(f) for f in step.filters)
    return f"{base} where {clauses}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chain.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/chain.py tests/test_chain.py
git commit -m "feat: build qSQL from a guided-form step"
```

---

## Task 6: Chain — `{{step.col}}` reference substitution

**Files:**
- Modify: `kdbmonitor/core/chain.py`
- Test: `tests/test_chain.py` (add tests)

- [ ] **Step 1: Write the failing test (append to `tests/test_chain.py`)**

```python
import pandas as pd
from kdbmonitor.core.chain import substitute_refs


def test_substitute_symbol_series():
    outputs = {"step1": pd.DataFrame({"sym": ["AAPL", "MSFT", "AAPL"]})}
    q = "select from QATT where sym in {{step1.sym}}"
    assert substitute_refs(q, outputs) == "select from QATT where sym in `AAPL`MSFT"


def test_substitute_number_series_single():
    outputs = {"s1": pd.DataFrame({"id": [7]})}
    assert substitute_refs("select from t where id in {{s1.id}}", outputs) == \
        "select from t where id in enlist 7"


def test_substitute_missing_ref_raises():
    import pytest
    with pytest.raises(KeyError):
        substitute_refs("x {{nope.col}}", {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chain.py -v`
Expected: FAIL — `substitute_refs` not defined.

- [ ] **Step 3: Write minimal implementation (append to `chain.py`)**

```python
import re
import pandas as pd

_REF = re.compile(r"\{\{(\w+)\.(\w+)\}\}")


def _infer_value_type(series: pd.Series) -> str:
    return "number" if pd.api.types.is_numeric_dtype(series) else "symbol"


def substitute_refs(qsql: str, outputs: dict) -> str:
    def repl(m: re.Match) -> str:
        name, col = m.group(1), m.group(2)
        if name not in outputs:
            raise KeyError(f"unknown step reference: {name}")
        df = outputs[name]
        if col not in df.columns:
            raise KeyError(f"step '{name}' has no column '{col}'")
        series = df[col]
        distinct = list(dict.fromkeys(series.tolist()))  # preserve order, dedupe
        return format_q_list(distinct, _infer_value_type(series))

    return _REF.sub(repl, qsql)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chain.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/chain.py tests/test_chain.py
git commit -m "feat: substitute {{step.col}} references into qSQL"
```

---

## Task 7: KDB client protocol, fake, real, connection manager (`client.py`)

**Files:**
- Create: `kdbmonitor/core/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_client.py
import pandas as pd
from kdbmonitor.core.client import FakeClient, ConnectionManager
from kdbmonitor.core.models import Connection


def test_fake_client_returns_canned():
    df = pd.DataFrame({"sym": ["AAPL"]})
    client = FakeClient({"select from target": df})
    assert client.query("select from target").equals(df)


def test_connection_manager_caches_client(monkeypatch):
    created = []

    class DummyClient:
        def __init__(self, host, port):
            created.append((host, port))
        def query(self, q):
            return pd.DataFrame()

    mgr = ConnectionManager(client_factory=DummyClient)
    conn = Connection(id=1, name="orders", host="h", port=5010)
    c1 = mgr.get(conn)
    c2 = mgr.get(conn)
    assert c1 is c2
    assert created == [("h", 5010)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_client.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_client.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/client.py tests/test_client.py
git commit -m "feat: add KDB client protocol, fake, pykx client, and connection cache"
```

---

## Task 8: Schema introspection (`schema.py`)

**Files:**
- Create: `kdbmonitor/core/schema.py`
- Test: `tests/test_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schema.py
import pandas as pd
from kdbmonitor.core.schema import introspect


class ScriptedClient:
    def __init__(self, mapping):
        self.mapping = mapping
    def query(self, q):
        return self.mapping[q]


def test_introspect_builds_table_column_map():
    client = ScriptedClient({
        "tables[]": pd.DataFrame({"t": ["target", "QATT"]}),
        "cols `target": pd.DataFrame({"c": ["sym", "orderId"]}),
        "cols `QATT": pd.DataFrame({"c": ["sym", "bid", "ask"]}),
    })
    schema = introspect(client)
    assert schema == {"target": ["sym", "orderId"], "QATT": ["sym", "bid", "ask"]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_schema.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```python
# kdbmonitor/core/schema.py
from __future__ import annotations


def introspect(client) -> dict[str, list[str]]:
    tables_df = client.query("tables[]")
    tables = tables_df.iloc[:, 0].tolist()
    schema: dict[str, list[str]] = {}
    for t in tables:
        cols_df = client.query(f"cols `{t}")
        schema[t] = cols_df.iloc[:, 0].tolist()
    return schema
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_schema.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/schema.py tests/test_schema.py
git commit -m "feat: introspect tables and columns into a schema map"
```

---

## Task 9: Chain — run the full chain across servers

**Files:**
- Modify: `kdbmonitor/core/chain.py`
- Test: `tests/test_chain.py` (add test)

- [ ] **Step 1: Write the failing test (append to `tests/test_chain.py`)**

```python
from kdbmonitor.core.models import Alert, TriggerCondition, RearmPolicy, Channels
from kdbmonitor.core.client import FakeClient
from kdbmonitor.core.chain import run_chain


def test_run_chain_cross_server():
    orders = FakeClient({"select from target where sym in `AAPL`MSFT":
                         pd.DataFrame({"sym": ["AAPL", "MSFT"]})})
    kdp = FakeClient({"select from QATT where sym in `AAPL`MSFT":
                      pd.DataFrame({"sym": ["AAPL", "MSFT"], "bid": [101.0, 99.0]})})
    clients = {"orders": orders, "kdp": kdp}

    alert = Alert(
        id=1, name="x", enabled=True, poll_interval_secs=30,
        steps=[
            Step(server="orders", table="target", mode="form",
                 filters=[Filter("sym", "in", ["AAPL", "MSFT"], "symbol")], output_name="step1"),
            Step(server="kdp", table="QATT", mode="raw", filters=[],
                 raw_qsql="select from QATT where sym in {{step1.sym}}", output_name="step2"),
        ],
        trigger=TriggerCondition(type="has_rows"),
        channels=Channels(), rearm=RearmPolicy(),
    )

    final = run_chain(alert, client_for=lambda name: clients[name])
    assert list(final["bid"]) == [101.0, 99.0]
    assert kdp.calls == ["select from QATT where sym in `AAPL`MSFT"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chain.py -v`
Expected: FAIL — `run_chain` not defined.

- [ ] **Step 3: Write minimal implementation (append to `chain.py`)**

```python
from typing import Callable
from kdbmonitor.core.models import Alert


def run_chain(alert: Alert, client_for: Callable[[str], object]) -> pd.DataFrame:
    outputs: dict[str, pd.DataFrame] = {}
    final: pd.DataFrame = pd.DataFrame()
    for step in alert.steps:
        qsql = substitute_refs(build_step_qsql(step), outputs)
        final = client_for(step.server).query(qsql)
        outputs[step.output_name] = final
    return final
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chain.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/chain.py tests/test_chain.py
git commit -m "feat: execute a full cross-server query chain"
```

---

## Task 10: Trigger condition evaluation (`conditions.py`)

**Files:**
- Create: `kdbmonitor/core/conditions.py`
- Test: `tests/test_conditions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_conditions.py
import pandas as pd
from kdbmonitor.core.models import TriggerCondition
from kdbmonitor.core.conditions import evaluate

EMPTY = pd.DataFrame({"bid": []})
ROWS = pd.DataFrame({"bid": [101.0, 99.0, 98.0]})


def test_no_rows_and_has_rows():
    assert evaluate(TriggerCondition(type="no_rows"), EMPTY) is True
    assert evaluate(TriggerCondition(type="no_rows"), ROWS) is False
    assert evaluate(TriggerCondition(type="has_rows"), ROWS) is True


def test_row_count_gte():
    assert evaluate(TriggerCondition(type="row_count_gte", n=3), ROWS) is True
    assert evaluate(TriggerCondition(type="row_count_gte", n=4), ROWS) is False


def test_any_row():
    assert evaluate(TriggerCondition(type="any_row", column="bid", op=">", value=100), ROWS) is True
    assert evaluate(TriggerCondition(type="any_row", column="bid", op=">", value=200), ROWS) is False


def test_all_rows():
    assert evaluate(TriggerCondition(type="all_rows", column="bid", op=">", value=90), ROWS) is True
    assert evaluate(TriggerCondition(type="all_rows", column="bid", op=">", value=100), ROWS) is False
    assert evaluate(TriggerCondition(type="all_rows", column="bid", op=">", value=90), EMPTY) is False


def test_aggregate():
    assert evaluate(TriggerCondition(type="aggregate", agg="max", column="bid", op=">", value=100), ROWS) is True
    assert evaluate(TriggerCondition(type="aggregate", agg="avg", column="bid", op="<", value=50), ROWS) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_conditions.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```python
# kdbmonitor/core/conditions.py
from __future__ import annotations

import operator

import pandas as pd

from kdbmonitor.core.models import TriggerCondition

_OPS = {
    "=": operator.eq, "<>": operator.ne, "<": operator.lt,
    "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}
_AGGS = {"max": "max", "min": "min", "avg": "mean", "sum": "sum"}


def evaluate(cond: TriggerCondition, df: pd.DataFrame) -> bool:
    n = len(df)
    if cond.type == "no_rows":
        return n == 0
    if cond.type == "has_rows":
        return n > 0
    if cond.type == "row_count_gte":
        return n >= cond.n
    if cond.type == "any_row":
        if n == 0:
            return False
        return bool(_OPS[cond.op](df[cond.column], cond.value).any())
    if cond.type == "all_rows":
        if n == 0:
            return False
        return bool(_OPS[cond.op](df[cond.column], cond.value).all())
    if cond.type == "aggregate":
        if n == 0:
            return False
        agg_val = getattr(df[cond.column], _AGGS[cond.agg])()
        return bool(_OPS[cond.op](agg_val, cond.value))
    raise ValueError(f"unknown condition type: {cond.type}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_conditions.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/conditions.py tests/test_conditions.py
git commit -m "feat: evaluate trigger conditions on the final result"
```

---

## Task 11: Re-arm decision (`rearm.py`)

**Files:**
- Create: `kdbmonitor/core/rearm.py`
- Test: `tests/test_rearm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rearm.py
from datetime import datetime
from kdbmonitor.core.models import RearmPolicy
from kdbmonitor.core.rearm import should_notify


def test_not_triggered_never_notifies():
    assert should_notify(prev_triggered=False, prev_notified_at=None,
                         curr_triggered=False, policy=RearmPolicy("transition"),
                         now=datetime(2026, 7, 15, 10, 0, 0)) is False


def test_transition_only_on_rising_edge():
    p = RearmPolicy("transition")
    now = datetime(2026, 7, 15, 10, 0, 0)
    assert should_notify(False, None, True, p, now) is True   # rising edge
    assert should_notify(True, now, True, p, now) is False    # still triggered


def test_every_tick():
    p = RearmPolicy("every_tick")
    now = datetime(2026, 7, 15, 10, 0, 0)
    assert should_notify(True, now, True, p, now) is True


def test_cooldown():
    p = RearmPolicy("cooldown", cooldown_secs=900)
    t0 = datetime(2026, 7, 15, 10, 0, 0)
    t_soon = datetime(2026, 7, 15, 10, 10, 0)   # 600s later
    t_late = datetime(2026, 7, 15, 10, 20, 0)   # 1200s later
    assert should_notify(False, None, True, p, t0) is True    # first trigger
    assert should_notify(True, t0, True, p, t_soon) is False  # within cooldown
    assert should_notify(True, t0, True, p, t_late) is True   # cooldown elapsed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rearm.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```python
# kdbmonitor/core/rearm.py
from __future__ import annotations

from datetime import datetime
from typing import Optional

from kdbmonitor.core.models import RearmPolicy


def should_notify(prev_triggered: bool, prev_notified_at: Optional[datetime],
                  curr_triggered: bool, policy: RearmPolicy, now: datetime) -> bool:
    if not curr_triggered:
        return False
    if policy.mode == "every_tick":
        return True
    if policy.mode == "transition":
        return not prev_triggered
    if policy.mode == "cooldown":
        if not prev_triggered or prev_notified_at is None:
            return True
        return (now - prev_notified_at).total_seconds() >= policy.cooldown_secs
    raise ValueError(f"unknown rearm mode: {policy.mode}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_rearm.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/rearm.py tests/test_rearm.py
git commit -m "feat: pure re-arm decision (transition/cooldown/every-tick)"
```

---

## Task 12: Notifiers + dispatch (`notifiers.py`)

**Files:**
- Create: `kdbmonitor/core/notifiers.py`
- Test: `tests/test_notifiers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notifiers.py
from kdbmonitor.core.models import Channels
from kdbmonitor.core.notifiers import dispatch, InAppSink


def test_dispatch_selects_enabled_channels():
    sink = InAppSink()
    sent_webhooks = []
    sent_emails = []

    channels = Channels(in_app=True, sound=True, email_to=["me@x.com"],
                        webhook_urls=["http://hook"])

    dispatch(
        channels, message="AAPL bid>100",
        in_app_sink=sink,
        email_fn=lambda to, msg: sent_emails.append((to, msg)),
        webhook_fn=lambda url, msg: sent_webhooks.append((url, msg)),
    )

    assert sink.messages == ["AAPL bid>100"]
    assert sent_emails == [(["me@x.com"], "AAPL bid>100")]
    assert sent_webhooks == [("http://hook", "AAPL bid>100")]


def test_dispatch_skips_disabled_channels():
    sink = InAppSink()
    sent = []
    channels = Channels(in_app=False, sound=False, email_to=[], webhook_urls=[])
    dispatch(channels, "m", in_app_sink=sink,
             email_fn=lambda to, msg: sent.append("email"),
             webhook_fn=lambda url, msg: sent.append("hook"))
    assert sink.messages == [] and sent == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_notifiers.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```python
# kdbmonitor/core/notifiers.py
from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from typing import Callable, Optional

import requests

from kdbmonitor.core.models import Channels


class InAppSink:
    """Collects messages to render in the Streamlit UI."""
    def __init__(self):
        self.messages: list[str] = []

    def push(self, message: str) -> None:
        self.messages.append(message)


def send_email(smtp_host: str, smtp_port: int, sender: str,
               to: list[str], subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    with smtplib.SMTP(smtp_host, smtp_port) as s:
        s.sendmail(sender, to, msg.as_string())


def post_webhook(url: str, message: str) -> None:
    requests.post(url, json={"text": message}, timeout=10)


def dispatch(channels: Channels, message: str, in_app_sink: InAppSink,
             email_fn: Optional[Callable[[list[str], str], None]] = None,
             webhook_fn: Optional[Callable[[str, str], None]] = None) -> None:
    if channels.in_app:
        in_app_sink.push(message)
    if channels.email_to and email_fn is not None:
        email_fn(channels.email_to, message)
    if webhook_fn is not None:
        for url in channels.webhook_urls:
            webhook_fn(url, message)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_notifiers.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/notifiers.py tests/test_notifiers.py
git commit -m "feat: notifier sinks and channel dispatch"
```

---

## Task 13: Evaluate one alert end-to-end (`evaluate.py`)

**Files:**
- Create: `kdbmonitor/core/evaluate.py`
- Test: `tests/test_evaluate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evaluate.py
from datetime import datetime
import pandas as pd
from kdbmonitor.core.models import Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels
from kdbmonitor.core.client import FakeClient
from kdbmonitor.core.evaluate import evaluate_alert


def _alert(cond):
    return Alert(
        id=1, name="x", enabled=True, poll_interval_secs=30,
        steps=[Step(server="kdp", table="QATT", mode="form",
                    filters=[Filter("sym", "in", ["AAPL"], "symbol")], output_name="step1")],
        trigger=cond, channels=Channels(), rearm=RearmPolicy("transition"),
    )


def _client_for(df):
    c = FakeClient({"select from QATT where sym in enlist `AAPL": df})
    return lambda name: c


def test_evaluate_triggers_and_notifies_on_rising_edge():
    df = pd.DataFrame({"sym": ["AAPL"], "bid": [101.0]})
    alert = _alert(TriggerCondition(type="any_row", column="bid", op=">", value=100))
    res = evaluate_alert(alert, _client_for(df), prev_run=None,
                         now=datetime(2026, 7, 15, 10, 0, 0))
    assert res.status == "triggered"
    assert res.triggered is True and res.notify is True
    assert res.row_count == 1
    assert "x" in res.message


def test_evaluate_armed_when_condition_false():
    df = pd.DataFrame({"sym": ["AAPL"], "bid": [50.0]})
    alert = _alert(TriggerCondition(type="any_row", column="bid", op=">", value=100))
    res = evaluate_alert(alert, _client_for(df), prev_run=None,
                         now=datetime(2026, 7, 15, 10, 0, 0))
    assert res.status == "armed" and res.triggered is False and res.notify is False


def test_evaluate_error_on_query_failure():
    def boom(name):
        raise RuntimeError("connection refused")
    alert = _alert(TriggerCondition(type="has_rows"))
    res = evaluate_alert(alert, boom, prev_run=None, now=datetime(2026, 7, 15, 10, 0, 0))
    assert res.status == "error" and res.notify is False
    assert "connection refused" in res.message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_evaluate.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```python
# kdbmonitor/core/evaluate.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from kdbmonitor.core.models import Alert
from kdbmonitor.core.chain import run_chain
from kdbmonitor.core.conditions import evaluate as eval_condition
from kdbmonitor.core.rearm import should_notify


@dataclass
class EvalResult:
    status: str          # armed | triggered | error
    triggered: bool
    notify: bool
    row_count: Optional[int]
    message: str


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(ts) if ts else None


def evaluate_alert(alert: Alert, client_for: Callable[[str], object],
                   prev_run: Optional[dict], now: datetime) -> EvalResult:
    try:
        df = run_chain(alert, client_for)
    except Exception as exc:  # noqa: BLE001 - surface any query/connection error
        return EvalResult(status="error", triggered=False, notify=False,
                          row_count=None, message=f"{alert.name}: error - {exc}")

    triggered = eval_condition(alert.trigger, df)
    prev_triggered = bool(prev_run["triggered"]) if prev_run else False
    prev_notified_at = None
    if prev_run and prev_run.get("notified") and prev_run.get("ts"):
        prev_notified_at = _parse_ts(prev_run["ts"])

    notify = should_notify(prev_triggered, prev_notified_at, triggered, alert.rearm, now)
    status = "triggered" if triggered else "armed"
    message = (f"{alert.name}: TRIGGERED ({len(df)} rows)" if triggered
               else f"{alert.name}: armed ({len(df)} rows)")
    return EvalResult(status=status, triggered=triggered, notify=notify,
                      row_count=len(df), message=message)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_evaluate.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest`
Expected: PASS (all tests green).

- [ ] **Step 6: Commit**

```bash
git add kdbmonitor/core/evaluate.py tests/test_evaluate.py
git commit -m "feat: evaluate an alert end-to-end (chain + condition + rearm)"
```

---

## Task 14: Streamlit UI — app shell, Admin, Builder, Monitor

> UI is kept thin: it wires `core/` together. Verification is a smoke import test plus a manual run, since Streamlit views are not unit-tested.

**Files:**
- Create: `app.py`, `kdbmonitor/ui/admin.py`, `kdbmonitor/ui/builder.py`, `kdbmonitor/ui/monitor.py`
- Test: `tests/test_ui_smoke.py`

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/test_ui_smoke.py
import importlib


def test_ui_modules_import():
    for mod in ("kdbmonitor.ui.admin", "kdbmonitor.ui.builder", "kdbmonitor.ui.monitor"):
        assert importlib.import_module(mod) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ui_smoke.py -v`
Expected: FAIL — modules do not exist yet.

- [ ] **Step 3: Install Streamlit + pykx into the venv**

Run: `pip install streamlit pykx`
Expected: installs succeed. (If pykx fails to install on this machine, the app still imports because `PyKxClient` imports pykx lazily; only live queries need it.)

- [ ] **Step 4: Create `kdbmonitor/ui/admin.py`**

```python
# kdbmonitor/ui/admin.py
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.models import Connection
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.schema import introspect


def render(store, mgr: ConnectionManager) -> None:
    st.header("Admin — KDB Connections")

    with st.form("add_conn", clear_on_submit=True):
        name = st.text_input("Name (e.g. orders, kdp)")
        host = st.text_input("Host", value="localhost")
        port = st.number_input("Port", min_value=1, max_value=65535, value=5010, step=1)
        if st.form_submit_button("Add connection") and name:
            store.add_connection(Connection(id=None, name=name, host=host, port=int(port)))
            st.success(f"Added {name}")
            st.rerun()

    st.subheader("Registered servers")
    for c in store.list_connections():
        cols = st.columns([3, 3, 2, 2, 2])
        cols[0].write(f"**{c.name}**")
        cols[1].write(f"{c.host}:{c.port}")
        cols[2].write(f"{len(c.schema)} tables" if c.schema else "not introspected")
        if cols[3].button("Introspect", key=f"intro_{c.id}"):
            try:
                c.schema = introspect(mgr.get(c))
                c.last_introspected_at = datetime.now(timezone.utc).isoformat()
                store.update_connection(c)
                st.success(f"{c.name}: found {len(c.schema)} tables")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Introspect failed: {exc}")
        if cols[4].button("Delete", key=f"del_{c.id}"):
            store.delete_connection(c.id)
            st.rerun()

    st.subheader("Email (SMTP) settings")
    st.caption("Used by alerts that select the email channel.")
    st.session_state.setdefault("smtp", {"host": "", "port": 25, "sender": ""})
    smtp = st.session_state["smtp"]
    smtp["host"] = st.text_input("SMTP host", value=smtp["host"])
    smtp["port"] = int(st.number_input("SMTP port", min_value=1, value=int(smtp["port"])))
    smtp["sender"] = st.text_input("From address", value=smtp["sender"])
```

- [ ] **Step 5: Create `kdbmonitor/ui/builder.py`**

```python
# kdbmonitor/ui/builder.py
from __future__ import annotations

import streamlit as st

from kdbmonitor.core.models import (
    Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels,
)

_OPS = ["=", "<>", "<", "<=", ">", ">=", "in"]
_VALUE_TYPES = ["symbol", "number", "string"]
_COND_TYPES = ["no_rows", "has_rows", "row_count_gte", "any_row", "all_rows", "aggregate"]
_AGGS = ["max", "min", "avg", "sum"]


def _server_names(store) -> list[str]:
    return [c.name for c in store.list_connections()]


def _schema_for(store, server: str) -> dict[str, list[str]]:
    c = store.get_connection_by_name(server)
    return c.schema if c else {}


def _step_editor(store, idx: int, servers: list[str]) -> Step:
    st.markdown(f"**Step {idx + 1}**")
    server = st.selectbox("Server", servers, key=f"srv_{idx}")
    schema = _schema_for(store, server)
    tables = list(schema.keys()) or ["<introspect server first>"]
    mode = st.radio("Mode", ["form", "raw"], horizontal=True, key=f"mode_{idx}")

    filters: list[Filter] = []
    raw_qsql = None
    table = st.selectbox("Table", tables, key=f"tbl_{idx}")

    if mode == "form":
        n_filters = st.number_input("Number of filters", 0, 5, 0, key=f"nf_{idx}")
        cols = schema.get(table, [])
        for fi in range(int(n_filters)):
            c1, c2, c3, c4 = st.columns([3, 2, 3, 2])
            col = c1.selectbox("Column", cols or ["<col>"], key=f"fcol_{idx}_{fi}")
            op = c2.selectbox("Op", _OPS, key=f"fop_{idx}_{fi}")
            raw_val = c3.text_input("Value(s), comma-separated for 'in'", key=f"fval_{idx}_{fi}")
            vtype = c4.selectbox("Type", _VALUE_TYPES, key=f"ftype_{idx}_{fi}")
            value = [v.strip() for v in raw_val.split(",")] if op == "in" else raw_val
            if vtype == "number":
                value = [float(v) for v in value] if op == "in" else float(raw_val or 0)
            filters.append(Filter(column=col, op=op, value=value, value_type=vtype))
    else:
        raw_qsql = st.text_area(
            "Raw qSQL (use {{stepN.col}} to reference earlier steps)",
            key=f"raw_{idx}", height=80,
        )

    return Step(server=server, table=table, mode=mode, filters=filters,
                raw_qsql=raw_qsql, output_name=f"step{idx + 1}")


def _trigger_editor() -> TriggerCondition:
    st.markdown("**Trigger condition (on final step result)**")
    ctype = st.selectbox("Condition", _COND_TYPES, key="cond_type")
    column = op = value = agg = None
    n = None
    if ctype == "row_count_gte":
        n = int(st.number_input("N (rows >=)", 1, 100000, 1, key="cond_n"))
    if ctype in ("any_row", "all_rows", "aggregate"):
        column = st.text_input("Column", key="cond_col")
        if ctype == "aggregate":
            agg = st.selectbox("Aggregate", _AGGS, key="cond_agg")
        op = st.selectbox("Operator", ["=", "<>", "<", "<=", ">", ">="], key="cond_op")
        value = float(st.number_input("Value", value=0.0, key="cond_val"))
    return TriggerCondition(type=ctype, column=column, op=op, value=value, n=n, agg=agg)


def _channels_editor() -> Channels:
    st.markdown("**Notify via (per-alert choice)**")
    in_app = st.checkbox("In-app banner", value=True)
    sound = st.checkbox("Sound", value=True)
    email_raw = st.text_input("Email recipients (comma-separated)")
    hooks_raw = st.text_input("Teams/Slack webhook URLs (comma-separated)")
    email_to = [e.strip() for e in email_raw.split(",") if e.strip()]
    webhook_urls = [h.strip() for h in hooks_raw.split(",") if h.strip()]
    return Channels(in_app=in_app, sound=sound, email_to=email_to, webhook_urls=webhook_urls)


def _rearm_editor() -> RearmPolicy:
    mode = st.selectbox("Re-arm", ["transition", "cooldown", "every_tick"], key="rearm_mode")
    cooldown = 0
    if mode == "cooldown":
        cooldown = int(st.number_input("Cooldown (seconds)", 1, 86400, 900, key="rearm_cd"))
    return RearmPolicy(mode=mode, cooldown_secs=cooldown)


def render(store) -> None:
    st.header("Builder — Alerts")
    servers = _server_names(store)
    if not servers:
        st.info("Add a connection in Admin first.")
        return

    st.subheader("Existing alerts")
    for a in store.list_alerts():
        cols = st.columns([4, 2, 2, 2])
        cols[0].write(f"**{a.name}** — {len(a.steps)} step(s), trigger: {a.trigger.type}")
        new_enabled = cols[1].toggle("Enabled", value=a.enabled, key=f"en_{a.id}")
        if new_enabled != a.enabled:
            store.set_alert_enabled(a.id, new_enabled)
            st.rerun()
        if cols[2].button("Delete", key=f"delA_{a.id}"):
            store.delete_alert(a.id)
            st.rerun()

    st.divider()
    st.subheader("Create new alert")
    name = st.text_input("Alert name")
    n_steps = int(st.number_input("Number of steps", 1, 5, 1))
    steps = [_step_editor(store, i, servers) for i in range(n_steps)]
    trigger = _trigger_editor()
    channels = _channels_editor()
    rearm = _rearm_editor()
    interval = int(st.number_input("Poll interval (seconds)", 5, 3600, 30))

    if st.button("Save alert") and name:
        store.add_alert(Alert(id=None, name=name, enabled=True,
                              poll_interval_secs=interval, steps=steps,
                              trigger=trigger, channels=channels, rearm=rearm))
        st.success(f"Saved alert '{name}'")
        st.rerun()
```

- [ ] **Step 6: Create `kdbmonitor/ui/monitor.py`**

```python
# kdbmonitor/ui/monitor.py
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.evaluate import evaluate_alert
from kdbmonitor.core.notifiers import InAppSink, dispatch, send_email, post_webhook


def _client_for(store, mgr: ConnectionManager):
    def resolve(server_name: str):
        conn = store.get_connection_by_name(server_name)
        if conn is None:
            raise RuntimeError(f"unknown server '{server_name}'")
        return mgr.get(conn)
    return resolve


def render(store, mgr: ConnectionManager) -> None:
    st.header("Monitor — Live")
    refresh = st.number_input("Refresh every (seconds)", 5, 600, 30)
    running = st.toggle("Actively monitoring", value=False)
    sink: InAppSink = st.session_state.setdefault("in_app_sink", InAppSink())

    now = datetime.now(timezone.utc)
    resolve = _client_for(store, mgr)
    rows = []
    for a in store.list_alerts():
        if not a.enabled:
            rows.append({"alert": a.name, "status": "disabled", "rows": None, "when": ""})
            continue
        prev = store.latest_run(a.id)
        res = evaluate_alert(a, resolve, prev_run=prev, now=now)
        store.record_run(a.id, ts=now.isoformat(), status=res.status,
                         triggered=res.triggered, notified=res.notify,
                         row_count=res.row_count, message=res.message)
        if res.notify:
            smtp = st.session_state.get("smtp", {})
            email_fn = None
            if smtp.get("host"):
                email_fn = lambda to, msg: send_email(
                    smtp["host"], int(smtp["port"]), smtp["sender"], to,
                    subject="KdbMonitor alert", body=msg)
            dispatch(a.channels, res.message, in_app_sink=sink,
                     email_fn=email_fn, webhook_fn=post_webhook)
        rows.append({"alert": a.name, "status": res.status,
                     "rows": res.row_count, "when": now.strftime("%H:%M:%S")})

    if sink.messages:
        for m in sink.messages[-10:]:
            st.error(f"🔔 {m}")
        st.markdown(
            "<audio autoplay><source src='https://actions.google.com/sounds/v1/alarms/beep_short.ogg'></audio>",
            unsafe_allow_html=True,
        )

    st.dataframe(rows, use_container_width=True)
    st.caption(f"Last check: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    if running:
        # Streamlit auto-refresh: rerun after `refresh` seconds while the page is open.
        st.markdown(
            f"<meta http-equiv='refresh' content='{int(refresh)}'>",
            unsafe_allow_html=True,
        )
```

- [ ] **Step 7: Create `app.py`**

```python
# app.py
import streamlit as st

from kdbmonitor.core.storage import Storage
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.ui import admin, builder, monitor

st.set_page_config(page_title="KdbMonitor", layout="wide")


@st.cache_resource
def get_store():
    store = Storage("kdbmonitor.db")
    store.init_db()
    return store


@st.cache_resource
def get_manager():
    return ConnectionManager()


store = get_store()
mgr = get_manager()

st.sidebar.title("KdbMonitor")
page = st.sidebar.radio("View", ["Monitor", "Builder", "Admin"])

if page == "Admin":
    admin.render(store, mgr)
elif page == "Builder":
    builder.render(store)
else:
    monitor.render(store, mgr)
```

- [ ] **Step 8: Run the smoke test to verify it passes**

Run: `python -m pytest tests/test_ui_smoke.py -v`
Expected: PASS (1 passed). (Imports succeed because pykx is imported lazily inside `PyKxClient`.)

- [ ] **Step 9: Run the full test suite**

Run: `python -m pytest`
Expected: PASS (all green).

- [ ] **Step 10: Manual verification — launch the app**

Run: `streamlit run app.py`
Expected: browser opens. Verify manually:
1. **Admin:** add a connection (name `kdp`, your host/port); click **Introspect** — tables count appears (needs a reachable KDB).
2. **Builder:** create a 2-step alert (Step 1 on one server, Step 2 raw qSQL referencing `{{step1.<col>}}` on another), pick a trigger condition and channels, Save. Toggle it enabled/disabled; delete it.
3. **Monitor:** toggle **Actively monitoring**; confirm the status table updates on refresh and an in-app banner + sound appears when a condition is met.

- [ ] **Step 11: Commit**

```bash
git add app.py kdbmonitor/ui tests/test_ui_smoke.py
git commit -m "feat: Streamlit Admin/Builder/Monitor UI over core"
```

---

## Self-Review Notes

- **Spec coverage:** connections host+port (T2, T14) ✓; schema introspection (T8, T14) ✓; hybrid form+raw builder (T5, T6, T14) ✓; cross-server chains (T9, T14) ✓; flexible trigger conditions incl. no-rows/has-rows/count/any/all/aggregate (T10) ✓; re-arm to avoid spam (T11) ✓; per-alert channel selection in-app/email/webhook (T12, T14) ✓; alert enable toggle + CRUD (T3, T14) ✓; checks-while-open auto-refresh loop (T14) ✓; SQLite persistence (T2, T3) ✓; core testable via fake client (T7 onward) ✓.
- **Deferred (per spec §11):** background daemon, KDB auth, multi-user auth — intentionally out of scope.
- **Type consistency:** `Storage`, `Connection`, `Alert`, `Step`, `Filter`, `TriggerCondition`, `RearmPolicy`, `Channels`, `EvalResult`, `FakeClient.query`, `ConnectionManager.get`, `run_chain(alert, client_for)`, `evaluate_alert(alert, client_for, prev_run, now)`, `should_notify(...)`, `dispatch(...)` names are used consistently across tasks.
- **Known simplification to flag during execution:** the guided-form filter `value_type` is chosen by the user in the UI; a future enhancement could infer it from the introspected column type.
