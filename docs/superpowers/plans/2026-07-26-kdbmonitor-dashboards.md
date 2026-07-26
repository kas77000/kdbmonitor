# KdbMonitor Dashboards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add saved, auto-refreshing dashboards built from KDB datasets — rows of KPI/table/chart widgets, interactive on screen, exportable as a PDF of exactly what is on screen.

**Architecture:** Datasets (query + transforms) resolve against a logical *environment* that pairs a real-time and a historical connection; the historical date range is injected at run time, never stored in filters. Each widget resolves once into a backend-agnostic `PlotModel`, which two dumb renderers draw: Plotly on screen, matplotlib/seaborn onto A4 for the PDF. Nothing touches the alert engine.

**Tech Stack:** Python 3.11, Streamlit, pandas, pykx, SQLite, matplotlib, seaborn, plotly, pytest.

**Spec:** `docs/superpowers/specs/2026-07-26-kdbmonitor-dashboards-design.md`

---

## File Structure

**Created — core (Streamlit-free, unit-tested):**

| File | Responsibility |
| --- | --- |
| `kdbmonitor/core/dashboard_models.py` | `Dashboard`, `Dataset`, `Transform`, `Row`, `Widget` dataclasses + JSON |
| `kdbmonitor/core/timectx.py` | time-context spec → dates; q date clause; `{{date_*}}` substitution; date-constraint check |
| `kdbmonitor/core/transform.py` | derive / filter / groupby / sort / limit / rename |
| `kdbmonitor/core/dataset.py` | run one dataset: env+mode → connection, build q, query, transform |
| `kdbmonitor/core/theme.py` | shared palette, seaborn theme, plotly template |
| `kdbmonitor/core/plotmodel.py` | `(Widget, DatasetResult) → PlotModel` — all numeric/colour/format decisions |
| `kdbmonitor/core/render_mpl.py` | `PlotModel` → matplotlib axes |
| `kdbmonitor/core/render_plotly.py` | `PlotModel` → plotly figure |
| `kdbmonitor/core/dashpdf.py` | rows → A4 pages → PDF bytes |

**Created — UI (thin):**

| File | Responsibility |
| --- | --- |
| `kdbmonitor/ui/dashboards.py` | gallery, pill tab strip, header bar, refresh fragment |
| `kdbmonitor/ui/dashboard_editor.py` | Data and Layout editors |

**Modified:**

| File | Change |
| --- | --- |
| `kdbmonitor/core/models.py` | `Connection.kind`, `Connection.env` |
| `kdbmonitor/core/storage.py` | connection columns + migration; `dashboards` table + CRUD; `list_environments` |
| `kdbmonitor/core/chain.py` | `_filter_clause` → public `filter_clause` (reused by datasets) |
| `kdbmonitor/core/client.py` | `ConnectionManager` passes the `Connection` to the mock factory |
| `kdbmonitor/core/mock.py` | historical demo server with a `date` column |
| `kdbmonitor/core/portability.py` | dashboards in export/import bundles |
| `kdbmonitor/ui/admin.py` | env/kind fields, environment pairing view |
| `app.py` | `Dashboards` nav page |
| `requirements.txt` | matplotlib, seaborn, plotly |

**Phases:** Tasks 1–3 environments · 4–8 data layer · 9–14 rendering · 15–18 UI · 19–20 integration.

---

## Task 1: Connection gains `kind` and `env`

**Files:**
- Modify: `kdbmonitor/core/models.py:66-74`
- Modify: `kdbmonitor/core/storage.py:41-47` (table), `:88-92` (`_migrate`), `:95-113` (add/row mapping), plus `update_connection`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_storage.py`:

```python
import sqlite3

from kdbmonitor.core.models import Connection
from kdbmonitor.core.storage import Storage


def test_connection_roundtrips_kind_and_env(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="order-hdb", host="h", port=5011,
                                kind="historical", env="orders"))
    got = s.list_connections()[0]
    assert got.kind == "historical"
    assert got.env == "orders"


def test_connection_defaults_to_realtime(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="rdb", host="h", port=5010))
    got = s.list_connections()[0]
    assert got.kind == "realtime"
    assert got.env == ""


def test_migration_adds_columns_to_an_old_db(tmp_path):
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE connections (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT UNIQUE NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL, "
        "schema_json TEXT NOT NULL DEFAULT '{}', last_introspected_at TEXT)"
    )
    raw.execute("INSERT INTO connections(name, host, port) VALUES ('legacy','h',5010)")
    raw.commit()
    raw.close()

    s = Storage(path)
    s.init_db()
    got = s.list_connections()[0]
    assert got.name == "legacy"
    assert got.kind == "realtime"
    assert got.env == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_storage.py -k "kind or env or migration" -v`
Expected: FAIL — `TypeError: Connection.__init__() got an unexpected keyword argument 'kind'`

- [ ] **Step 3: Add the fields to the model**

In `kdbmonitor/core/models.py`, replace the `Connection` dataclass:

```python
@dataclass
class Connection:
    id: Optional[int]
    name: str
    host: str
    port: int
    schema: dict[str, list[str]] = field(default_factory=dict)  # table -> columns
    last_introspected_at: Optional[str] = None
    kind: str = "realtime"       # realtime | historical
    env: str = ""                # logical environment; "" falls back to name
```

And extend `connection_from_dict` with the two new keys:

```python
def connection_from_dict(d: dict) -> Connection:
    return Connection(
        id=d.get("id"),
        name=d["name"],
        host=d["host"],
        port=d["port"],
        schema=d.get("schema", {}),
        last_introspected_at=d.get("last_introspected_at"),
        kind=d.get("kind", "realtime"),
        env=d.get("env", ""),
    )
```

- [ ] **Step 4: Persist them**

In `kdbmonitor/core/storage.py`, add the columns to the `connections` DDL inside `init_db`:

```sql
            CREATE TABLE IF NOT EXISTS connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                schema_json TEXT NOT NULL DEFAULT '{}',
                last_introspected_at TEXT,
                kind TEXT NOT NULL DEFAULT 'realtime',
                env TEXT NOT NULL DEFAULT ''
            );
```

Extend `_migrate` for databases created before those columns existed:

```python
    def _migrate(self) -> None:
        """Additive migrations for DBs created before a column existed."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(alert_runs)")}
        if "result_hash" not in cols:
            self.conn.execute("ALTER TABLE alert_runs ADD COLUMN result_hash TEXT")

        ccols = {r["name"] for r in self.conn.execute("PRAGMA table_info(connections)")}
        if "kind" not in ccols:
            self.conn.execute(
                "ALTER TABLE connections ADD COLUMN kind TEXT NOT NULL DEFAULT 'realtime'")
        if "env" not in ccols:
            self.conn.execute(
                "ALTER TABLE connections ADD COLUMN env TEXT NOT NULL DEFAULT ''")
```

Write them in `add_connection`:

```python
        cur = self.conn.execute(
            "INSERT INTO connections(name, host, port, schema_json, last_introspected_at, kind, env)"
            " VALUES (?,?,?,?,?,?,?)",
            (c.name, c.host, c.port, json.dumps(c.schema), c.last_introspected_at,
             c.kind, c.env),
        )
```

And read them back in `_row_to_connection`:

```python
    def _row_to_connection(self, r: sqlite3.Row) -> Connection:
        return Connection(
            id=r["id"], name=r["name"], host=r["host"], port=r["port"],
            schema=json.loads(r["schema_json"]),
            last_introspected_at=r["last_introspected_at"],
            kind=r["kind"], env=r["env"],
        )
```

- [ ] **Step 5: Update `update_connection` to write the new columns**

Open `kdbmonitor/core/storage.py`, find `update_connection`, and add `kind=?, env=?` to its `SET` clause with `c.kind, c.env` in the parameter tuple, keeping the existing parameter order and trailing `WHERE id=?`.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS — the three new tests plus all 51 existing tests.

- [ ] **Step 7: Commit**

```bash
git add kdbmonitor/core/models.py kdbmonitor/core/storage.py tests/test_storage.py
git commit -m "feat: connections carry kind (realtime/historical) and env"
```

---

## Task 2: Environment pairing lookup

**Files:**
- Modify: `kdbmonitor/core/storage.py` (append after the connection CRUD)
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_storage.py`:

```python
def test_list_environments_pairs_by_env(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="order-rdb", host="h", port=5010,
                                kind="realtime", env="orders"))
    s.add_connection(Connection(id=None, name="order-hdb", host="h", port=5011,
                                kind="historical", env="orders"))
    envs = s.list_environments()
    assert set(envs) == {"orders"}
    assert envs["orders"]["realtime"].name == "order-rdb"
    assert envs["orders"]["historical"].name == "order-hdb"


def test_list_environments_reports_missing_side(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="rdb-only", host="h", port=5010,
                                kind="realtime", env="orders"))
    envs = s.list_environments()
    assert envs["orders"]["historical"] is None


def test_env_falls_back_to_connection_name(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="standalone", host="h", port=5010))
    assert "standalone" in s.list_environments()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_storage.py -k environments -v`
Expected: FAIL — `AttributeError: 'Storage' object has no attribute 'list_environments'`

- [ ] **Step 3: Implement it**

Add to `Storage` in `kdbmonitor/core/storage.py`, after the connection methods:

```python
    def list_environments(self) -> dict[str, dict[str, Optional[Connection]]]:
        """Group connections into {env: {"realtime": Connection|None,
        "historical": Connection|None}}.

        A connection with a blank ``env`` forms its own single-sided environment
        named after itself, so pre-existing connections keep working untouched.
        """
        envs: dict[str, dict[str, Optional[Connection]]] = {}
        for c in self.list_connections():
            key = c.env or c.name
            slot = envs.setdefault(key, {"realtime": None, "historical": None})
            slot[c.kind] = c
        return envs
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_storage.py -k environments -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/storage.py tests/test_storage.py
git commit -m "feat: group connections into realtime/historical environments"
```

---

## Task 3: Historical demo server

The mock lets the whole feature be exercised with no real KDB, matching the
existing `host == "demo"` convention. Historical demo tables carry a `date`
column and honour `date within (d1;d2)`.

**Files:**
- Modify: `kdbmonitor/core/client.py:59-69`
- Modify: `kdbmonitor/core/mock.py:151-160` (append `MockHdbClient`, extend demo specs)
- Test: `tests/test_mock.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mock.py`:

```python
from datetime import date, timedelta

from kdbmonitor.core.mock import MockHdbClient, demo_connection_specs


def test_hdb_tables_have_a_date_column():
    df = MockHdbClient().query("select from target")
    assert "date" in df.columns


def test_hdb_honours_a_date_within_filter():
    today = date.today()
    lo = (today - timedelta(days=2)).strftime("%Y.%m.%d")
    hi = today.strftime("%Y.%m.%d")
    df = MockHdbClient().query(f"select from target where date within ({lo};{hi})")
    assert not df.empty
    assert df["date"].min() >= today - timedelta(days=2)
    assert df["date"].max() <= today


def test_hdb_range_outside_the_generated_window_is_empty():
    df = MockHdbClient().query(
        "select from target where date within (1999.01.01;1999.01.31)")
    assert df.empty


def test_demo_specs_include_a_paired_historical_server():
    by_name = {c.name: c for c in demo_connection_specs()}
    assert by_name["orders_demo"].env == "orders"
    assert by_name["orders_demo"].kind == "realtime"
    assert by_name["orders_hdb_demo"].env == "orders"
    assert by_name["orders_hdb_demo"].kind == "historical"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_mock.py -k "hdb or historical" -v`
Expected: FAIL — `ImportError: cannot import name 'MockHdbClient'`

- [ ] **Step 3: Implement the historical mock**

Append to `kdbmonitor/core/mock.py`:

```python
_HDB_DAYS = 30            # how much history the demo server pretends to hold


def _with_dates(df: pd.DataFrame, days: int = _HDB_DAYS) -> pd.DataFrame:
    """Repeat a real-time frame once per day for the last ``days`` days.

    Quantities are scaled by the day index so charts over a range are not flat.
    """
    today = datetime.now(timezone.utc).date()
    frames = []
    for i in range(days):
        day = today - timedelta(days=days - 1 - i)
        d = df.copy()
        d.insert(0, "date", day)
        for col in ("qty", "filledQty", "leavesQty", "pct_complete"):
            if col in d.columns:
                d[col] = (d[col] * (0.5 + i / days)).round(2)
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


_DATE_WITHIN = re.compile(
    r"date\s+within\s*\(\s*(\d{4}\.\d{2}\.\d{2})\s*;\s*(\d{4}\.\d{2}\.\d{2})\s*\)")


class MockHdbClient(MockKdbClient):
    """Historical twin of ``MockKdbClient``: same tables plus a ``date`` column,
    with best-effort support for a ``date within (d1;d2)`` constraint."""

    def query(self, qsql: str) -> pd.DataFrame:
        q = qsql.strip()
        if "tables[]" in q:
            return pd.DataFrame({"t": list(SCHEMA.keys())})
        if q.startswith("cols"):
            m = re.search(r"`(\w+)", q)
            table = m.group(1) if m else ""
            cols = SCHEMA.get(table, [])
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
```

Add `timedelta` to the datetime import at the top of the file:

```python
from datetime import datetime, timedelta, timezone
```

- [ ] **Step 4: Extend the demo connection specs**

Replace `demo_connection_specs` in `kdbmonitor/core/mock.py`:

```python
def demo_connection_specs() -> list[Connection]:
    """Pre-introspected demo connections (host 'demo' routes to a mock).

    ``orders_demo`` / ``orders_hdb_demo`` form one environment so the
    realtime/historical switch is demoable with no real KDB.
    """
    ts = datetime.now(timezone.utc).isoformat()
    order_tables = ("target", "work_order", "target_state")
    return [
        Connection(id=None, name="kdp_demo", host="demo", port=1,
                   schema={"QATT": SCHEMA["QATT"]}, last_introspected_at=ts,
                   kind="realtime", env="marketdata"),
        Connection(id=None, name="orders_demo", host="demo", port=2,
                   schema={k: SCHEMA[k] for k in order_tables},
                   last_introspected_at=ts, kind="realtime", env="orders"),
        Connection(id=None, name="orders_hdb_demo", host="demo", port=3,
                   schema={k: ["date"] + SCHEMA[k] for k in order_tables},
                   last_introspected_at=ts, kind="historical", env="orders"),
    ]
```

- [ ] **Step 5: Route demo connections to the right mock**

`ConnectionManager` currently calls a zero-argument mock factory, so it cannot
tell the two demo servers apart. In `kdbmonitor/core/client.py`, replace `get`:

```python
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
        ``mock_factory`` (used by tests) wins and receives the connection."""
        if self._mock_factory is not None:
            return self._mock_factory(conn)
        from kdbmonitor.core.mock import MockHdbClient, MockKdbClient
        return MockHdbClient() if conn.kind == "historical" else MockKdbClient()
```

- [ ] **Step 6: Run the suite**

Run: `python -m pytest -q`
Expected: PASS. If an existing test passes a zero-argument `mock_factory`, update
that call site to accept one argument (`lambda conn: FakeClient(...)`).

- [ ] **Step 7: Commit**

```bash
git add kdbmonitor/core/client.py kdbmonitor/core/mock.py tests/test_mock.py
git commit -m "feat: historical demo server with a date column"
```

---

## Task 4: Dashboard models

**Files:**
- Create: `kdbmonitor/core/dashboard_models.py`
- Test: `tests/test_dashboard_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_models.py`:

```python
from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Row, Transform, Widget,
    dashboard_from_json, dashboard_to_json,
)
from kdbmonitor.core.models import Filter


def _sample() -> Dashboard:
    return Dashboard(
        id=7, name="Short sell", description="by market", refresh_secs=15,
        time_context={"mode": "historical",
                      "range": {"kind": "preset", "name": "last_30d"}},
        datasets=[Dataset(
            name="orders", env="orders", mode="guided", table="target",
            filters=[Filter(column="side", op="=", value="sellshort",
                            value_type="symbol")],
            transforms=[Transform(kind="groupby", params={
                "keys": ["market"],
                "aggs": [{"column": "id_target", "func": "nunique",
                          "as": "n_orders"}]})],
        )],
        rows=[Row(height_in=0.9, widgets=[
            Widget(type="kpi", dataset="orders", title="Orders",
                   spec={"column": "n_orders", "agg": "sum"}, width=1.0)])],
    )


def test_dashboard_survives_a_json_roundtrip():
    d = _sample()
    back = dashboard_from_json(dashboard_to_json(d))
    assert back == d


def test_nested_types_are_rebuilt_not_left_as_dicts():
    back = dashboard_from_json(dashboard_to_json(_sample()))
    assert isinstance(back.datasets[0], Dataset)
    assert isinstance(back.datasets[0].filters[0], Filter)
    assert isinstance(back.datasets[0].transforms[0], Transform)
    assert isinstance(back.rows[0], Row)
    assert isinstance(back.rows[0].widgets[0], Widget)


def test_defaults_are_filled_for_a_minimal_payload():
    d = dashboard_from_json('{"id": null, "name": "Empty"}')
    assert d.refresh_secs == 15
    assert d.time_context == {"mode": "realtime"}
    assert d.datasets == []
    assert d.rows == []


def test_dataset_defaults():
    ds = Dataset(name="d", env="orders")
    assert ds.time_mode == "inherit"
    assert ds.mode == "guided"
    assert ds.max_rows == 5000
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_dashboard_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.core.dashboard_models'`

- [ ] **Step 3: Implement the models**

Create `kdbmonitor/core/dashboard_models.py`:

```python
"""Dashboard definitions: datasets (the data) and rows of widgets (the layout).

Kept separate from ``core.models`` — alerts and dashboards are different
entities that happen to share ``Filter``. Serialised whole as JSON, the same way
alerts are, so adding a field never needs a schema migration.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

from kdbmonitor.core.models import Filter


def _default_time_context() -> dict:
    return {"mode": "realtime"}


@dataclass
class Transform:
    kind: str                    # derive | filter | groupby | sort | limit | rename
    params: dict = field(default_factory=dict)


@dataclass
class Dataset:
    name: str                    # referenced by widgets and by {{name.column}}
    env: str                     # logical environment, not a connection name
    time_mode: str = "inherit"   # inherit | realtime | custom
    time_context: Optional[dict] = None    # only when time_mode == "custom"
    mode: str = "guided"         # guided | raw
    table: str = ""              # guided only
    filters: list[Filter] = field(default_factory=list)      # guided only
    raw_qsql: Optional[str] = None                            # raw only
    transforms: list[Transform] = field(default_factory=list)
    max_rows: int = 5000


@dataclass
class Widget:
    type: str                    # kpi|table|bar|line|scatter|hist|box|heatmap|pie|text
    dataset: str
    title: str = ""
    spec: dict = field(default_factory=dict)
    width: float = 1.0           # relative weight within its row


@dataclass
class Row:
    widgets: list[Widget] = field(default_factory=list)
    height_in: float = 2.5       # printed height; screen height derives from it


@dataclass
class Dashboard:
    id: Optional[int]
    name: str
    description: str = ""
    refresh_secs: int = 15
    time_context: dict = field(default_factory=_default_time_context)
    datasets: list[Dataset] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)


def dashboard_to_dict(d: Dashboard) -> dict:
    return asdict(d)


def dashboard_from_dict(d: dict) -> Dashboard:
    return Dashboard(
        id=d.get("id"),
        name=d["name"],
        description=d.get("description", ""),
        refresh_secs=d.get("refresh_secs", 15),
        time_context=d.get("time_context") or _default_time_context(),
        datasets=[_dataset_from_dict(x) for x in d.get("datasets", [])],
        rows=[_row_from_dict(x) for x in d.get("rows", [])],
    )


def _dataset_from_dict(d: dict) -> Dataset:
    return Dataset(
        name=d["name"],
        env=d.get("env", ""),
        time_mode=d.get("time_mode", "inherit"),
        time_context=d.get("time_context"),
        mode=d.get("mode", "guided"),
        table=d.get("table", ""),
        filters=[Filter(**f) for f in d.get("filters", [])],
        raw_qsql=d.get("raw_qsql"),
        transforms=[Transform(kind=t["kind"], params=t.get("params", {}))
                    for t in d.get("transforms", [])],
        max_rows=d.get("max_rows", 5000),
    )


def _row_from_dict(d: dict) -> Row:
    return Row(
        widgets=[Widget(type=w["type"], dataset=w["dataset"],
                        title=w.get("title", ""), spec=w.get("spec", {}),
                        width=w.get("width", 1.0))
                 for w in d.get("widgets", [])],
        height_in=d.get("height_in", 2.5),
    )


def dashboard_to_json(d: Dashboard) -> str:
    return json.dumps(dashboard_to_dict(d))


def dashboard_from_json(raw: str) -> Dashboard:
    return dashboard_from_dict(json.loads(raw))
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_dashboard_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/dashboard_models.py tests/test_dashboard_models.py
git commit -m "feat: dashboard, dataset, row and widget models"
```

---

## Task 5: Dashboard storage

**Files:**
- Modify: `kdbmonitor/core/storage.py` (DDL in `init_db`, CRUD after the alert CRUD)
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_storage.py`:

```python
from kdbmonitor.core.dashboard_models import Dashboard, Dataset


def _dash(name="Short sell") -> Dashboard:
    return Dashboard(id=None, name=name,
                     datasets=[Dataset(name="orders", env="orders",
                                       table="target")])


def test_dashboard_crud(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()

    did = s.add_dashboard(_dash())
    got = s.get_dashboard(did)
    assert got.id == did
    assert got.name == "Short sell"
    assert got.datasets[0].table == "target"

    got.name = "Renamed"
    s.update_dashboard(got)
    assert s.get_dashboard(did).name == "Renamed"

    assert [d.name for d in s.list_dashboards()] == ["Renamed"]

    s.delete_dashboard(did)
    assert s.list_dashboards() == []
    assert s.get_dashboard(did) is None


def test_dashboards_are_listed_by_name(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_dashboard(_dash("Zulu"))
    s.add_dashboard(_dash("Alpha"))
    assert [d.name for d in s.list_dashboards()] == ["Alpha", "Zulu"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_storage.py -k dashboard -v`
Expected: FAIL — `AttributeError: 'Storage' object has no attribute 'add_dashboard'`

- [ ] **Step 3: Add the table**

In `kdbmonitor/core/storage.py`, add to the `init_db` script, after the
`alert_views` table:

```sql
            CREATE TABLE IF NOT EXISTS dashboards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                dashboard_json TEXT NOT NULL
            );
```

- [ ] **Step 4: Add the CRUD**

Add the import at the top of `kdbmonitor/core/storage.py`:

```python
from kdbmonitor.core.dashboard_models import (
    Dashboard, dashboard_from_json, dashboard_to_json,
)
```

And the methods to `Storage`, after the alert CRUD:

```python
    # --- dashboards ---
    def add_dashboard(self, d: Dashboard) -> int:
        cur = self.conn.execute(
            "INSERT INTO dashboards(name, dashboard_json) VALUES (?,?)",
            (d.name, dashboard_to_json(d)),
        )
        self.conn.commit()
        return cur.lastrowid

    def _row_to_dashboard(self, r: sqlite3.Row) -> Dashboard:
        d = dashboard_from_json(r["dashboard_json"])
        d.id = r["id"]                      # the row id is authoritative
        return d

    def get_dashboard(self, dashboard_id: int) -> Optional[Dashboard]:
        r = self.conn.execute(
            "SELECT * FROM dashboards WHERE id=?", (dashboard_id,)).fetchone()
        return self._row_to_dashboard(r) if r else None

    def list_dashboards(self) -> list[Dashboard]:
        rows = self.conn.execute(
            "SELECT * FROM dashboards ORDER BY name COLLATE NOCASE").fetchall()
        return [self._row_to_dashboard(r) for r in rows]

    def update_dashboard(self, d: Dashboard) -> None:
        self.conn.execute(
            "UPDATE dashboards SET name=?, dashboard_json=? WHERE id=?",
            (d.name, dashboard_to_json(d), d.id),
        )
        self.conn.commit()

    def delete_dashboard(self, dashboard_id: int) -> None:
        self.conn.execute("DELETE FROM dashboards WHERE id=?", (dashboard_id,))
        self.conn.commit()
```

- [ ] **Step 5: Run to verify they pass**

Run: `python -m pytest tests/test_storage.py -k dashboard -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add kdbmonitor/core/storage.py tests/test_storage.py
git commit -m "feat: persist dashboards"
```

---

## Task 6: Time context

Resolves a stored spec to concrete dates, builds the q date clause, substitutes
`{{date_*}}` placeholders in raw q, and reports whether a raw query constrains
`date` at all.

**Files:**
- Create: `kdbmonitor/core/timectx.py`
- Test: `tests/test_timectx.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_timectx.py`:

```python
from datetime import date

import pytest

from kdbmonitor.core.timectx import (
    ResolvedTime, date_clause, has_date_constraint, q_date, resolve,
    substitute_dates,
)

TODAY = date(2026, 7, 26)          # mid-year, so no month/year edge cases


def test_realtime_spec_resolves_without_dates():
    rt = resolve({"mode": "realtime"}, TODAY)
    assert rt.mode == "realtime"
    assert rt.start is None and rt.end is None


def test_absolute_range():
    rt = resolve({"mode": "historical",
                  "range": {"kind": "absolute",
                            "from": "2026-06-01", "to": "2026-06-30"}}, TODAY)
    assert (rt.start, rt.end) == (date(2026, 6, 1), date(2026, 6, 30))


def test_relative_range_is_inclusive_of_today():
    rt = resolve({"mode": "historical",
                  "range": {"kind": "relative", "n": 30, "unit": "days"}}, TODAY)
    assert rt.end == TODAY
    assert rt.start == date(2026, 6, 27)          # 30 days inclusive


def test_relative_weeks():
    rt = resolve({"mode": "historical",
                  "range": {"kind": "relative", "n": 2, "unit": "weeks"}}, TODAY)
    assert (rt.start, rt.end) == (date(2026, 7, 13), TODAY)


@pytest.mark.parametrize("name,start,end", [
    ("today",      date(2026, 7, 26), date(2026, 7, 26)),
    ("yesterday",  date(2026, 7, 25), date(2026, 7, 25)),
    ("last_7d",    date(2026, 7, 20), date(2026, 7, 26)),
    ("last_30d",   date(2026, 6, 27), date(2026, 7, 26)),
    ("mtd",        date(2026, 7, 1),  date(2026, 7, 26)),
    ("last_month", date(2026, 6, 1),  date(2026, 6, 30)),
    ("ytd",        date(2026, 1, 1),  date(2026, 7, 26)),
])
def test_presets(name, start, end):
    rt = resolve({"mode": "historical",
                  "range": {"kind": "preset", "name": name}}, TODAY)
    assert (rt.start, rt.end) == (start, end)


def test_last_month_handles_january():
    rt = resolve({"mode": "historical",
                  "range": {"kind": "preset", "name": "last_month"}},
                 date(2026, 1, 15))
    assert (rt.start, rt.end) == (date(2025, 12, 1), date(2025, 12, 31))


def test_unknown_preset_is_an_error():
    with pytest.raises(ValueError, match="unknown preset"):
        resolve({"mode": "historical",
                 "range": {"kind": "preset", "name": "nope"}}, TODAY)


def test_inverted_range_is_an_error():
    with pytest.raises(ValueError, match="starts after"):
        resolve({"mode": "historical",
                 "range": {"kind": "absolute",
                           "from": "2026-06-30", "to": "2026-06-01"}}, TODAY)


def test_q_date_literal():
    assert q_date(date(2026, 6, 1)) == "2026.06.01"


def test_date_clause():
    rt = ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 30))
    assert date_clause(rt) == "date within (2026.06.01;2026.06.30)"


def test_date_clause_is_empty_for_realtime():
    assert date_clause(ResolvedTime("realtime", None, None)) == ""


def test_substitute_dates_fills_placeholders():
    rt = ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 3))
    q = "select from t where date within ({{date_from}};{{date_to}})"
    assert substitute_dates(q, rt) == \
        "select from t where date within (2026.06.01;2026.06.03)"


def test_substitute_dates_expands_a_date_list():
    rt = ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 3))
    assert substitute_dates("select from t where date in {{date_list}}", rt) == \
        "select from t where date in 2026.06.01 2026.06.02 2026.06.03"


def test_substitute_dates_leaves_realtime_queries_alone():
    rt = ResolvedTime("realtime", None, None)
    q = "select from t where side=`sellshort"
    assert substitute_dates(q, rt) == q


def test_has_date_constraint():
    assert has_date_constraint("select from t where date within (a;b)")
    assert has_date_constraint("select from t where date={{date_from}}")
    assert not has_date_constraint("select from t where side=`sellshort")
    assert not has_date_constraint("select from t where update_time>0")


def test_label_describes_the_range():
    assert ResolvedTime("realtime", None, None).label == "Real-time"
    assert ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 30)).label \
        == "Historical · 2026-06-01 → 2026-06-30"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_timectx.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.core.timectx'`

- [ ] **Step 3: Implement it**

Create `kdbmonitor/core/timectx.py`:

```python
"""Time context: turning a stored range *spec* into concrete dates.

Dashboards store the spec, never resolved dates, so a saved "last 30 days"
dashboard means the last 30 days whenever it is opened rather than freezing on
the day it was built. Resolution happens once per refresh.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

PRESETS = ("today", "yesterday", "last_7d", "last_30d", "mtd", "last_month", "ytd")

PRESET_LABELS = {
    "today": "Today", "yesterday": "Yesterday", "last_7d": "Last 7 days",
    "last_30d": "Last 30 days", "mtd": "Month to date",
    "last_month": "Last month", "ytd": "Year to date",
}


@dataclass(frozen=True)
class ResolvedTime:
    mode: str                    # realtime | historical
    start: Optional[date]
    end: Optional[date]

    @property
    def label(self) -> str:
        if self.mode == "realtime":
            return "Real-time"
        return f"Historical · {self.start:%Y-%m-%d} → {self.end:%Y-%m-%d}"


def _preset(name: str, today: date) -> tuple[date, date]:
    if name == "today":
        return today, today
    if name == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if name == "last_7d":
        return today - timedelta(days=6), today
    if name == "last_30d":
        return today - timedelta(days=29), today
    if name == "mtd":
        return today.replace(day=1), today
    if name == "last_month":
        last_day_prev = today.replace(day=1) - timedelta(days=1)
        return last_day_prev.replace(day=1), last_day_prev
    if name == "ytd":
        return today.replace(month=1, day=1), today
    raise ValueError(f"unknown preset: {name}")


def _relative(n: int, unit: str, today: date) -> tuple[date, date]:
    if unit not in ("days", "weeks"):
        raise ValueError(f"unknown relative unit: {unit}")
    days = n * 7 if unit == "weeks" else n
    return today - timedelta(days=days - 1), today


def resolve(spec: dict, today: date) -> ResolvedTime:
    """Resolve a time-context spec against ``today``."""
    if (spec or {}).get("mode") != "historical":
        return ResolvedTime("realtime", None, None)

    rng = spec.get("range") or {}
    kind = rng.get("kind")
    if kind == "absolute":
        start = date.fromisoformat(rng["from"])
        end = date.fromisoformat(rng["to"])
    elif kind == "relative":
        start, end = _relative(int(rng.get("n", 1)), rng.get("unit", "days"), today)
    elif kind == "preset":
        start, end = _preset(rng.get("name", ""), today)
    else:
        raise ValueError(f"unknown range kind: {kind}")

    if start > end:
        raise ValueError(f"range starts after it ends: {start} > {end}")
    return ResolvedTime("historical", start, end)


def q_date(d: date) -> str:
    """A kdb+ date literal: 2026.06.01."""
    return f"{d:%Y.%m.%d}"


def date_clause(rt: ResolvedTime) -> str:
    """The where-clause constraining the partition column. Empty in real-time."""
    if rt.mode != "historical":
        return ""
    return f"date within ({q_date(rt.start)};{q_date(rt.end)})"


_DATE_REF = re.compile(r"\{\{(date_from|date_to|date_list)\}\}")
_DATE_WORD = re.compile(r"\bdate\b")


def substitute_dates(qsql: str, rt: ResolvedTime) -> str:
    """Fill {{date_from}} / {{date_to}} / {{date_list}} in a raw q query."""
    if rt.mode != "historical":
        return qsql

    def repl(m: re.Match) -> str:
        if m.group(1) == "date_from":
            return q_date(rt.start)
        if m.group(1) == "date_to":
            return q_date(rt.end)
        days = (rt.end - rt.start).days + 1
        return " ".join(q_date(rt.start + timedelta(days=i)) for i in range(days))

    return _DATE_REF.sub(repl, qsql)


def has_date_constraint(qsql: str) -> bool:
    """Whether a raw query mentions the ``date`` column at all.

    The guard against an unconstrained scan of a partitioned HDB — which does not
    error, it just reads years of data and hangs a refreshing page.
    """
    return _DATE_WORD.search(qsql or "") is not None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_timectx.py -v`
Expected: PASS (all cases, including the 7 parametrised presets)

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/timectx.py tests/test_timectx.py
git commit -m "feat: time context resolution and q date clauses"
```

---

## Task 7: Transforms

Post-query shaping in pandas. `derive` + `groupby` together reproduce
`short_sell_report.py`'s `market_of()` and `summarise_by_market()` with no Python.

**Files:**
- Create: `kdbmonitor/core/transform.py`
- Test: `tests/test_transform.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_transform.py`:

```python
import pandas as pd
import pytest

from kdbmonitor.core.dashboard_models import Transform
from kdbmonitor.core.transform import apply_transforms


def _orders() -> pd.DataFrame:
    return pd.DataFrame([
        {"id_target": 1, "sym": "5.HK",   "size": 100, "executed": 50, "nReject": 0},
        {"id_target": 2, "sym": "700.HK", "size": 200, "executed": 200, "nReject": 1},
        {"id_target": 3, "sym": "7203.JP", "size": 50, "executed": 0,  "nReject": 2},
    ])


def test_derive_arithmetic():
    out = apply_transforms(_orders(), [Transform(kind="derive", params={
        "column": "completion_pct", "kind": "arithmetic",
        "expr": "100 * executed / size"})])
    assert out["completion_pct"].tolist() == [50.0, 100.0, 0.0]


def test_derive_suffix_map():
    out = apply_transforms(_orders(), [Transform(kind="derive", params={
        "column": "market", "kind": "suffix_map", "source": "sym",
        "mapping": {".HK": "Hong Kong", ".JP": "Japan"}, "default": "Unknown"})])
    assert out["market"].tolist() == ["Hong Kong", "Hong Kong", "Japan"]


def test_derive_suffix_map_falls_back_to_default():
    df = pd.DataFrame({"sym": ["AAPL", "5.XX"]})
    out = apply_transforms(df, [Transform(kind="derive", params={
        "column": "market", "kind": "suffix_map", "source": "sym",
        "mapping": {".HK": "Hong Kong"}, "default": "Unknown"})])
    assert out["market"].tolist() == ["Unknown", "Unknown"]


def test_filter_on_a_derived_column():
    out = apply_transforms(_orders(), [
        Transform(kind="derive", params={"column": "completion_pct",
                                         "kind": "arithmetic",
                                         "expr": "100 * executed / size"}),
        Transform(kind="filter", params={"column": "completion_pct",
                                         "op": ">", "value": 40}),
    ])
    assert out["id_target"].tolist() == [1, 2]


def test_filter_in_operator():
    out = apply_transforms(_orders(), [Transform(kind="filter", params={
        "column": "id_target", "op": "in", "value": [1, 3]})])
    assert out["id_target"].tolist() == [1, 3]


def test_groupby_aggregates():
    out = apply_transforms(_orders(), [
        Transform(kind="derive", params={
            "column": "market", "kind": "suffix_map", "source": "sym",
            "mapping": {".HK": "Hong Kong", ".JP": "Japan"}, "default": "Unknown"}),
        Transform(kind="groupby", params={"keys": ["market"], "aggs": [
            {"column": "id_target", "func": "nunique", "as": "n_orders"},
            {"column": "size", "func": "sum", "as": "order_qty"},
            {"column": "nReject", "func": "sum", "as": "n_rejections"},
        ]}),
    ])
    hk = out[out["market"] == "Hong Kong"].iloc[0]
    assert hk["n_orders"] == 2
    assert hk["order_qty"] == 300
    assert hk["n_rejections"] == 1
    assert list(out.columns) == ["market", "n_orders", "order_qty", "n_rejections"]


def test_sort_descending():
    out = apply_transforms(_orders(), [Transform(kind="sort", params={
        "columns": ["size"], "ascending": False})])
    assert out["size"].tolist() == [200, 100, 50]


def test_limit():
    out = apply_transforms(_orders(), [Transform(kind="limit", params={"n": 2})])
    assert len(out) == 2


def test_rename():
    out = apply_transforms(_orders(), [Transform(kind="rename", params={
        "mapping": {"size": "order_qty"}})])
    assert "order_qty" in out.columns and "size" not in out.columns


def test_transforms_apply_in_order():
    out = apply_transforms(_orders(), [
        Transform(kind="sort", params={"columns": ["size"], "ascending": False}),
        Transform(kind="limit", params={"n": 1}),
    ])
    assert out["size"].tolist() == [200]


def test_original_frame_is_not_mutated():
    df = _orders()
    apply_transforms(df, [Transform(kind="rename",
                                    params={"mapping": {"size": "order_qty"}})])
    assert "size" in df.columns


def test_empty_frame_survives_every_transform():
    empty = pd.DataFrame(columns=["sym", "size", "executed", "id_target", "nReject"])
    out = apply_transforms(empty, [
        Transform(kind="derive", params={"column": "market", "kind": "suffix_map",
                                         "source": "sym", "mapping": {},
                                         "default": "Unknown"}),
        Transform(kind="groupby", params={"keys": ["market"], "aggs": [
            {"column": "id_target", "func": "nunique", "as": "n_orders"}]}),
    ])
    assert out.empty


def test_unknown_transform_kind_is_an_error():
    with pytest.raises(ValueError, match="unknown transform"):
        apply_transforms(_orders(), [Transform(kind="teleport", params={})])


def test_missing_column_names_the_transform():
    with pytest.raises(ValueError, match="sort: no column 'nope'"):
        apply_transforms(_orders(), [Transform(kind="sort",
                                               params={"columns": ["nope"]})])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_transform.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.core.transform'`

- [ ] **Step 3: Implement it**

Create `kdbmonitor/core/transform.py`:

```python
"""Guided post-query shaping, applied in order to a dataset's frame.

Deliberately a small closed catalogue rather than arbitrary code: dashboards are
stored in the DB and shared between users, so a transform must be data, not a
Python snippet.
"""
from __future__ import annotations

import operator
from typing import Any, Callable

import pandas as pd

AGG_FUNCS = ("count", "nunique", "sum", "mean", "min", "max")

_OPS: dict[str, Callable[[Any, Any], Any]] = {
    "=": operator.eq, "==": operator.eq, "!=": operator.ne, "<>": operator.ne,
    "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}


def _need(df: pd.DataFrame, column: str, kind: str) -> None:
    if column not in df.columns:
        raise ValueError(f"{kind}: no column '{column}' (have: "
                         f"{', '.join(map(str, df.columns))})")


def _derive(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    column, kind = p["column"], p.get("kind", "arithmetic")
    if kind == "arithmetic":
        # pandas' own expression engine: column names only, no attribute access.
        df[column] = df.eval(p["expr"]) if len(df) else pd.Series(dtype="float64")
        return df
    if kind == "suffix_map":
        source, mapping = p["source"], p.get("mapping", {})
        default = p.get("default", "Unknown")
        _need(df, source, "derive")

        def to_market(v: Any) -> str:
            if isinstance(v, str) and "." in v:
                return mapping.get("." + v.rsplit(".", 1)[1], default)
            return default

        df[column] = df[source].map(to_market) if len(df) else pd.Series(dtype=object)
        return df
    raise ValueError(f"unknown derive kind: {kind}")


def _filter(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    column, op, value = p["column"], p["op"], p.get("value")
    _need(df, column, "filter")
    if op == "in":
        return df[df[column].isin(value)].reset_index(drop=True)
    if op not in _OPS:
        raise ValueError(f"unknown filter op: {op}")
    return df[_OPS[op](df[column], value)].reset_index(drop=True)


def _groupby(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    keys, aggs = p["keys"], p["aggs"]
    for k in keys:
        _need(df, k, "groupby")
    for a in aggs:
        _need(df, a["column"], "groupby")
        if a["func"] not in AGG_FUNCS:
            raise ValueError(f"unknown agg func: {a['func']}")
    if df.empty:
        return pd.DataFrame(columns=keys + [a["as"] for a in aggs])
    named = {a["as"]: (a["column"], a["func"]) for a in aggs}
    return df.groupby(keys, as_index=False, dropna=False).agg(**named)


def _sort(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    columns = p["columns"]
    for c in columns:
        _need(df, c, "sort")
    return df.sort_values(columns, ascending=p.get("ascending", True)) \
             .reset_index(drop=True)


def _limit(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    return df.head(int(p["n"])).reset_index(drop=True)


def _rename(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    return df.rename(columns=p["mapping"])


_KINDS: dict[str, Callable[[pd.DataFrame, dict], pd.DataFrame]] = {
    "derive": _derive, "filter": _filter, "groupby": _groupby,
    "sort": _sort, "limit": _limit, "rename": _rename,
}


def apply_transforms(df: pd.DataFrame, transforms) -> pd.DataFrame:
    """Apply transforms in order, returning a new frame. Never mutates ``df``."""
    out = df.copy()
    for t in transforms:
        fn = _KINDS.get(t.kind)
        if fn is None:
            raise ValueError(f"unknown transform: {t.kind}")
        out = fn(out, t.params)
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_transform.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/transform.py tests/test_transform.py
git commit -m "feat: guided dataset transforms"
```

---

## Task 8: Dataset execution

Resolves a dataset's environment and time mode to a connection, builds the q
(injecting the date clause *first* so kdb+ can prune partitions), runs it,
applies transforms, and captures failures instead of raising.

**Files:**
- Modify: `kdbmonitor/core/chain.py:8-15` (make `_filter_clause` public)
- Create: `kdbmonitor/core/dataset.py`
- Test: `tests/test_dataset.py`

- [ ] **Step 1: Make the filter-clause builder reusable**

In `kdbmonitor/core/chain.py`, rename `_filter_clause` to `filter_clause` and
update its one caller inside `build_step_qsql`:

```python
def filter_clause(f) -> str:
    if f.op == "in":
        clause = f"{f.column} in {format_q_list(f.value, f.value_type)}"
    elif f.op == "like":
        clause = f"{f.column} like {format_q_value(f.value, 'string')}"
    else:
        clause = f"{f.column}{f.op}{format_q_value(f.value, f.value_type)}"
    return f"not {clause}" if f.negated else clause


def build_step_qsql(step: Step) -> str:
    if step.mode == "raw":
        return step.raw_qsql or ""
    base = f"select from {step.table}"
    if not step.filters:
        return base
    clauses = ", ".join(filter_clause(f) for f in step.filters)
    return f"{base} where {clauses}"
```

Run: `python -m pytest tests/test_chain.py -q`
Expected: PASS — no behaviour change.

- [ ] **Step 2: Write the failing test**

Create `tests/test_dataset.py`:

```python
from datetime import date

import pandas as pd
import pytest

from kdbmonitor.core.client import ConnectionManager, FakeClient
from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Transform
from kdbmonitor.core.dataset import (
    build_qsql, effective_time, resolve_connection, run_datasets,
)
from kdbmonitor.core.models import Connection, Filter
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.timectx import ResolvedTime

TODAY = date(2026, 7, 26)
RT = ResolvedTime("realtime", None, None)
HIST = ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 30))


@pytest.fixture()
def store(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="order-rdb", host="rdb", port=1,
                                kind="realtime", env="orders"))
    s.add_connection(Connection(id=None, name="order-hdb", host="hdb", port=2,
                                kind="historical", env="orders"))
    return s


def _mgr(responses: dict) -> ConnectionManager:
    client = FakeClient(responses)
    return ConnectionManager(client_factory=lambda host, port: client)


# --- connection resolution -------------------------------------------------

def test_resolve_connection_picks_the_matching_kind(store):
    assert resolve_connection(store, "orders", "realtime").name == "order-rdb"
    assert resolve_connection(store, "orders", "historical").name == "order-hdb"


def test_resolve_connection_reports_a_missing_side(store):
    store.add_connection(Connection(id=None, name="md-rdb", host="h", port=3,
                                    kind="realtime", env="marketdata"))
    with pytest.raises(ValueError, match="no historical server"):
        resolve_connection(store, "marketdata", "historical")


def test_resolve_connection_reports_an_unknown_env(store):
    with pytest.raises(ValueError, match="unknown environment"):
        resolve_connection(store, "nope", "realtime")


# --- effective time --------------------------------------------------------

def test_dataset_inherits_the_dashboard_time():
    ds = Dataset(name="d", env="orders", time_mode="inherit")
    assert effective_time(ds, HIST, TODAY) == HIST


def test_dataset_can_force_realtime():
    ds = Dataset(name="d", env="orders", time_mode="realtime")
    assert effective_time(ds, HIST, TODAY).mode == "realtime"


def test_dataset_can_carry_its_own_range():
    ds = Dataset(name="d", env="orders", time_mode="custom",
                 time_context={"mode": "historical",
                               "range": {"kind": "preset", "name": "yesterday"}})
    got = effective_time(ds, RT, TODAY)
    assert (got.start, got.end) == (date(2026, 7, 25), date(2026, 7, 25))


# --- query building --------------------------------------------------------

def test_guided_realtime_query_has_no_date():
    ds = Dataset(name="d", env="orders", table="target",
                 filters=[Filter(column="side", op="=", value="sellshort",
                                 value_type="symbol")])
    assert build_qsql(ds, RT, {}) == "select from target where side=`sellshort"


def test_guided_historical_puts_date_first():
    ds = Dataset(name="d", env="orders", table="target",
                 filters=[Filter(column="side", op="=", value="sellshort",
                                 value_type="symbol")])
    assert build_qsql(ds, HIST, {}) == (
        "select from target where date within (2026.06.01;2026.06.30), "
        "side=`sellshort")


def test_guided_historical_with_no_filters():
    ds = Dataset(name="d", env="orders", table="target")
    assert build_qsql(ds, HIST, {}) == \
        "select from target where date within (2026.06.01;2026.06.30)"


def test_raw_query_gets_date_placeholders_filled():
    ds = Dataset(name="d", env="orders", mode="raw",
                 raw_qsql="select from target where date within "
                          "({{date_from}};{{date_to}})")
    assert build_qsql(ds, HIST, {}) == \
        "select from target where date within (2026.06.01;2026.06.30)"


def test_raw_query_can_reference_another_dataset():
    ds = Dataset(name="d", env="orders", mode="raw",
                 raw_qsql="select from target_state where id_target in {{ids.id}}")
    outputs = {"ids": pd.DataFrame({"id": [1, 2]})}
    assert build_qsql(ds, RT, outputs) == \
        "select from target_state where id_target in 1 2"


# --- running ---------------------------------------------------------------

def test_run_datasets_applies_transforms(store):
    q = "select from target where side=`sellshort"
    mgr = _mgr({q: pd.DataFrame([
        {"id_target": 1, "sym": "5.HK", "size": 100},
        {"id_target": 2, "sym": "7203.JP", "size": 50},
    ])})
    dash = Dashboard(id=1, name="d", datasets=[Dataset(
        name="orders", env="orders", table="target",
        filters=[Filter(column="side", op="=", value="sellshort",
                        value_type="symbol")],
        transforms=[Transform(kind="derive", params={
            "column": "market", "kind": "suffix_map", "source": "sym",
            "mapping": {".HK": "Hong Kong", ".JP": "Japan"},
            "default": "Unknown"})])])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["orders"].error is None
    assert res["orders"].df["market"].tolist() == ["Hong Kong", "Japan"]
    assert res["orders"].row_count == 2


def test_historical_raw_query_without_a_date_is_refused(store):
    mgr = _mgr({})
    dash = Dashboard(
        id=1, name="d",
        time_context={"mode": "historical",
                      "range": {"kind": "absolute",
                                "from": "2026-06-01", "to": "2026-06-30"}},
        datasets=[Dataset(name="orders", env="orders", mode="raw",
                          raw_qsql="select from target where side=`sellshort")])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["orders"].df is None
    assert "must constrain 'date'" in res["orders"].error
    assert mgr.get(store.list_connections()[0]).calls == []   # never sent


def test_query_failure_is_captured_not_raised(store):
    mgr = _mgr({})                     # FakeClient raises KeyError for anything
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="orders", env="orders", table="target")])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["orders"].df is None
    assert res["orders"].error


def test_one_broken_dataset_does_not_stop_the_others(store):
    good = "select from work_order"
    mgr = _mgr({good: pd.DataFrame({"sym": ["AAPL"]})})
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="broken", env="orders", table="target"),
        Dataset(name="fine", env="orders", table="work_order"),
    ])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["broken"].error
    assert res["fine"].error is None


def test_results_are_capped_at_max_rows(store):
    q = "select from target"
    mgr = _mgr({q: pd.DataFrame({"sym": list("abcdefghij")})})
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="orders", env="orders", table="target", max_rows=3)])

    res = run_datasets(dash, store, mgr, TODAY)
    assert len(res["orders"].df) == 3
    assert res["orders"].row_count == 10
    assert res["orders"].truncated is True


def test_a_dataset_can_consume_an_earlier_one(store):
    first = "select from target"
    second = "select from target_state where id_target in 1 2"
    mgr = _mgr({
        first: pd.DataFrame({"id_target": [1, 2]}),
        second: pd.DataFrame({"id_target": [1, 2], "open": [10, 0]}),
    })
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="ids", env="orders", table="target"),
        Dataset(name="states", env="orders", mode="raw",
                raw_qsql="select from target_state where "
                         "id_target in {{ids.id_target}}"),
    ])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["states"].error is None
    assert res["states"].df["open"].tolist() == [10, 0]


def test_a_forward_reference_is_an_error(store):
    mgr = _mgr({})
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="states", env="orders", mode="raw",
                raw_qsql="select from target_state where "
                         "id_target in {{ids.id_target}}"),
        Dataset(name="ids", env="orders", table="target"),
    ])

    res = run_datasets(dash, store, mgr, TODAY)
    assert "unknown step reference" in res["states"].error
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/test_dataset.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.core.dataset'`

- [ ] **Step 4: Implement it**

Create `kdbmonitor/core/dataset.py`:

```python
"""Running a dashboard's datasets.

One dataset -> one DataFrame. Failures are *captured*, never raised: a dead
server must degrade one panel, not blank the page. The historical date clause is
built here rather than stored in the dataset's filters, which is what makes
flipping a dataset between environments lossless.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

from kdbmonitor.core.chain import filter_clause, substitute_refs
from kdbmonitor.core.dashboard_models import Dashboard, Dataset
from kdbmonitor.core.models import Connection
from kdbmonitor.core.timectx import (
    ResolvedTime, date_clause, has_date_constraint, resolve, substitute_dates,
)
from kdbmonitor.core.transform import apply_transforms


@dataclass
class DatasetResult:
    name: str
    df: Optional[pd.DataFrame]      # None when the dataset failed
    qsql: str                       # the query that was (or would have been) sent
    error: Optional[str]
    row_count: int = 0              # true size before max_rows capping
    truncated: bool = False


def resolve_connection(store, env: str, kind: str) -> Connection:
    """The connection serving ``env`` in the given environment kind."""
    envs = store.list_environments()
    if env not in envs:
        raise ValueError(f"unknown environment: '{env}'")
    conn = envs[env][kind]
    if conn is None:
        raise ValueError(
            f"environment '{env}' has no {kind} server — add one in Admin")
    return conn


def effective_time(ds: Dataset, dashboard_time: ResolvedTime,
                   today: date) -> ResolvedTime:
    """The time context this dataset actually runs under."""
    if ds.time_mode == "realtime":
        return ResolvedTime("realtime", None, None)
    if ds.time_mode == "custom":
        return resolve(ds.time_context or {"mode": "realtime"}, today)
    return dashboard_time


def build_qsql(ds: Dataset, rt: ResolvedTime, outputs: dict) -> str:
    """The query for this dataset, with dates and dataset references resolved."""
    if ds.mode == "raw":
        return substitute_refs(substitute_dates(ds.raw_qsql or "", rt), outputs)

    clauses: list[str] = []
    if rt.mode == "historical":
        clauses.append(date_clause(rt))       # first, so kdb+ prunes partitions
    clauses += [filter_clause(f) for f in ds.filters]
    base = f"select from {ds.table}"
    q = base if not clauses else f"{base} where {', '.join(clauses)}"
    return substitute_refs(q, outputs)


def run_dataset(ds: Dataset, rt: ResolvedTime, store, mgr,
                outputs: dict) -> DatasetResult:
    """Run one dataset, capturing any failure as an error on the result."""
    if rt.mode == "historical" and ds.mode == "raw" \
            and not has_date_constraint(ds.raw_qsql or ""):
        return DatasetResult(
            ds.name, None, ds.raw_qsql or "",
            "historical query must constrain 'date' — add a "
            "date within ({{date_from}};{{date_to}}) clause")

    qsql = ""
    try:
        qsql = build_qsql(ds, rt, outputs)
        conn = resolve_connection(store, ds.env, rt.mode)
        df = mgr.get(conn).query(qsql)
        df = apply_transforms(df, ds.transforms)
    except Exception as exc:      # noqa: BLE001 - a broken panel, not a broken page
        return DatasetResult(ds.name, None, qsql, str(exc))

    total = len(df)
    capped = df.head(ds.max_rows).reset_index(drop=True)
    return DatasetResult(ds.name, capped, qsql, None,
                         row_count=total, truncated=total > len(capped))


def run_datasets(dashboard: Dashboard, store, mgr,
                 today: date) -> dict[str, DatasetResult]:
    """Run every dataset in declaration order, returning results by name.

    Successful frames are fed forward so a later dataset can reference an
    earlier one with ``{{name.column}}``.
    """
    dashboard_time = resolve(dashboard.time_context, today)
    outputs: dict[str, pd.DataFrame] = {}
    results: dict[str, DatasetResult] = {}
    for ds in dashboard.datasets:
        rt = effective_time(ds, dashboard_time, today)
        res = run_dataset(ds, rt, store, mgr, outputs)
        results[ds.name] = res
        if res.df is not None:
            outputs[ds.name] = res.df
    return results
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/test_dataset.py -v`
Expected: PASS (18 tests)

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS — the `filter_clause` rename must not have broken `test_chain.py`.

- [ ] **Step 7: Commit**

```bash
git add kdbmonitor/core/chain.py kdbmonitor/core/dataset.py tests/test_dataset.py
git commit -m "feat: run dashboard datasets against realtime or historical servers"
```

---

## Task 9: Shared theme

The palette both renderers read, lifted out of `short_sell_report.py` so a given
market is the same colour on screen and in print.

**Files:**
- Create: `kdbmonitor/core/theme.py`
- Modify: `requirements.txt`
- Test: `tests/test_theme.py`

- [ ] **Step 1: Add the dependencies**

Replace `requirements.txt`:

```
streamlit>=1.36
pykx>=2.5
pandas>=2.0
requests>=2.31
openpyxl>=3.1
matplotlib>=3.8
seaborn>=0.13
plotly>=5.20
```

Run: `python -m pip install -r requirements.txt`
Expected: matplotlib, seaborn and plotly install cleanly.

- [ ] **Step 2: Write the failing test**

Create `tests/test_theme.py`:

```python
from kdbmonitor.core import theme


def test_categorical_colours_cycle():
    n = len(theme.CATEGORICAL)
    assert theme.color_for(0) == theme.CATEGORICAL[0]
    assert theme.color_for(n) == theme.CATEGORICAL[0]      # wraps
    assert theme.color_for(1) != theme.color_for(0)


def test_every_colour_is_a_hex_string():
    for c in theme.CATEGORICAL + list(theme.SEMANTIC.values()):
        assert c.startswith("#") and len(c) == 7


def test_semantic_names_cover_the_widget_vocabulary():
    assert set(theme.SEMANTIC) >= {"ink", "muted", "good", "critical", "blue"}


def test_resolve_colour_accepts_names_and_literals():
    assert theme.resolve_color("good") == theme.GOOD
    assert theme.resolve_color("#123456") == "#123456"
    assert theme.resolve_color(None) == theme.INK
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/test_theme.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.core.theme'`

- [ ] **Step 4: Implement it**

Create `kdbmonitor/core/theme.py`:

```python
"""One palette, both renderers.

The report surface is light (a PDF is paper); the screen surface is dark to match
the app. Only the *surface* differs — data colours are shared, so a series keeps
its identity between screen and print.
"""
from __future__ import annotations

from typing import Optional

# --- report surface (PDF) — from short_sell_report.py ---------------------- #
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

# --- data colours (shared) ------------------------------------------------- #
BLUE = "#2a78d6"         # magnitude / the default series
CRITICAL = "#d03b3b"     # a concern metric
GOOD = "#006300"         # a positive outcome

CATEGORICAL = [BLUE, "#4b9f6e", "#d98a2b", "#8b5fbf", CRITICAL,
               "#2a9d9b", "#b5762a", "#5a6b8c"]

SEMANTIC = {"ink": INK, "ink2": INK2, "muted": MUTED, "good": GOOD,
            "critical": CRITICAL, "blue": BLUE}

SEQUENTIAL_CMAP = "Blues"    # heatmaps

# --- screen surface (Plotly) ---------------------------------------------- #
PLOTLY_TEMPLATE = "plotly_dark"
SCREEN_SURFACE = "rgba(0,0,0,0)"     # inherit Streamlit's background
SCREEN_INK = "#dfe7ef"
SCREEN_GRID = "#2b3542"


def color_for(i: int) -> str:
    """The i-th categorical colour, cycling."""
    return CATEGORICAL[i % len(CATEGORICAL)]


def resolve_color(name: Optional[str]) -> str:
    """A semantic name ('good'), a hex literal ('#123456'), or ink by default."""
    if not name:
        return INK
    if name.startswith("#"):
        return name
    return SEMANTIC.get(name, INK)


def apply_seaborn_theme() -> None:
    """Configure matplotlib + seaborn for the printed report surface.

    Call once before drawing a PDF; safe to call repeatedly.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    sns.set_theme(style="whitegrid", palette=CATEGORICAL)
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Segoe UI", "DejaVu Sans", "Arial"],
        "text.color": INK,
        "axes.labelcolor": INK2,
        "axes.edgecolor": BASELINE,
        "xtick.color": INK2,
        "ytick.color": INK2,
        "grid.color": GRID,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
    })
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/test_theme.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add kdbmonitor/core/theme.py requirements.txt tests/test_theme.py
git commit -m "feat: shared palette and report theme"
```

---

## Task 10: PlotModel

The single place every numeric, colour and formatting decision is made. Both
renderers consume its output, so they cannot disagree about the data.

**Files:**
- Create: `kdbmonitor/core/plotmodel.py`
- Test: `tests/test_plotmodel.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_plotmodel.py`:

```python
import pandas as pd

from kdbmonitor.core import theme
from kdbmonitor.core.dashboard_models import Widget
from kdbmonitor.core.dataset import DatasetResult
from kdbmonitor.core.plotmodel import build_plot_model


def _ok(df: pd.DataFrame, name="by_market") -> dict:
    return {name: DatasetResult(name, df, "q", None, row_count=len(df))}


def _summary() -> pd.DataFrame:
    return pd.DataFrame([
        {"market": "Hong Kong", "n_orders": 12, "completion_pct": 61.4, "n_rejections": 3},
        {"market": "Japan",     "n_orders": 30, "completion_pct": 88.2, "n_rejections": 0},
        {"market": "Korea",     "n_orders": 5,  "completion_pct": 12.0, "n_rejections": 1},
    ])


# --- errors ----------------------------------------------------------------

def test_missing_dataset_becomes_an_error_model():
    pm = build_plot_model(Widget(type="kpi", dataset="nope", title="X"), {})
    assert pm.kind == "error"
    assert "nope" in pm.error
    assert pm.title == "X"


def test_failed_dataset_carries_its_message():
    results = {"by_market": DatasetResult("by_market", None, "q", "connection refused")}
    pm = build_plot_model(Widget(type="kpi", dataset="by_market"), results)
    assert pm.kind == "error"
    assert "connection refused" in pm.error


def test_missing_column_becomes_an_error_model():
    w = Widget(type="kpi", dataset="by_market", spec={"column": "nope", "agg": "sum"})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.kind == "error"
    assert "nope" in pm.error


# --- kpi -------------------------------------------------------------------

def test_kpi_aggregates_and_formats():
    w = Widget(type="kpi", dataset="by_market", title="Orders",
               spec={"column": "n_orders", "agg": "sum", "fmt": ",.0f"})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.kind == "kpi"
    assert pm.value == "47"
    assert pm.title == "Orders"


def test_kpi_thousands_separator_and_suffix():
    df = pd.DataFrame({"qty": [1234567]})
    w = Widget(type="kpi", dataset="by_market",
               spec={"column": "qty", "agg": "sum", "fmt": ",.0f", "suffix": " sh"})
    pm = build_plot_model(w, _ok(df))
    assert pm.value == "1,234,567 sh"


def test_kpi_threshold_colours_the_value():
    w = Widget(type="kpi", dataset="by_market",
               spec={"column": "n_rejections", "agg": "sum", "fmt": ",.0f",
                     "thresholds": [{"op": ">", "value": 0, "color": "critical"}]})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.value == "4"
    assert pm.value_color == theme.CRITICAL


def test_kpi_without_a_matching_threshold_is_ink():
    w = Widget(type="kpi", dataset="by_market",
               spec={"column": "n_rejections", "agg": "sum", "fmt": ",.0f",
                     "thresholds": [{"op": ">", "value": 99, "color": "critical"}]})
    assert build_plot_model(w, _ok(_summary())).value_color == theme.INK


def test_kpi_on_an_empty_frame_shows_a_dash():
    w = Widget(type="kpi", dataset="by_market",
               spec={"column": "n_orders", "agg": "sum"})
    pm = build_plot_model(w, _ok(pd.DataFrame(columns=["n_orders"])))
    assert pm.value == "—"


# --- table -----------------------------------------------------------------

def test_table_selects_and_formats_columns():
    w = Widget(type="table", dataset="by_market",
               spec={"columns": ["market", "completion_pct"],
                     "formats": {"completion_pct": ".1f"}})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.kind == "table"
    assert pm.columns == ["market", "completion_pct"]
    assert pm.rows[0] == ["Hong Kong", "61.4"]


def test_table_defaults_to_every_column():
    pm = build_plot_model(Widget(type="table", dataset="by_market"), _ok(_summary()))
    assert pm.columns == ["market", "n_orders", "completion_pct", "n_rejections"]


def test_table_highlight_marks_matching_cells():
    w = Widget(type="table", dataset="by_market",
               spec={"columns": ["market", "n_rejections"],
                     "highlight": [{"column": "n_rejections", "op": ">",
                                    "value": 0, "color": "critical"}]})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.cell_colors[(0, 1)] == theme.CRITICAL     # Hong Kong, 3
    assert (1, 1) not in pm.cell_colors                 # Japan, 0


# --- bar / line ------------------------------------------------------------

def test_bar_builds_one_series_with_a_colour():
    w = Widget(type="bar", dataset="by_market",
               spec={"x": "market", "y": "completion_pct"})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.kind == "bar"
    assert len(pm.series) == 1
    assert pm.series[0].x == ["Hong Kong", "Japan", "Korea"]
    assert pm.series[0].y == [61.4, 88.2, 12.0]
    assert pm.series[0].color == theme.color_for(0)
    assert pm.x_label == "market"
    assert pm.y_label == "completion_pct"


def test_bar_sorts_descending_when_asked():
    w = Widget(type="bar", dataset="by_market",
               spec={"x": "market", "y": "completion_pct", "sort": "desc"})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.series[0].x == ["Japan", "Hong Kong", "Korea"]


def test_bar_orientation_defaults_to_vertical():
    w = Widget(type="bar", dataset="by_market",
               spec={"x": "market", "y": "n_orders"})
    assert build_plot_model(w, _ok(_summary())).orientation == "v"


def test_line_supports_several_y_columns():
    df = pd.DataFrame({"date": ["d1", "d2"], "a": [1, 2], "b": [3, 4]})
    w = Widget(type="line", dataset="by_market",
               spec={"x": "date", "y": ["a", "b"]})
    pm = build_plot_model(w, _ok(df))
    assert [s.label for s in pm.series] == ["a", "b"]
    assert pm.series[1].y == [3, 4]
    assert pm.series[1].color == theme.color_for(1)


def test_line_splits_by_hue():
    df = pd.DataFrame({"date": ["d1", "d2", "d1", "d2"],
                       "market": ["HK", "HK", "JP", "JP"],
                       "qty": [1, 2, 3, 4]})
    w = Widget(type="line", dataset="by_market",
               spec={"x": "date", "y": "qty", "hue": "market"})
    pm = build_plot_model(w, _ok(df))
    assert [s.label for s in pm.series] == ["HK", "JP"]
    assert pm.series[0].y == [1, 2]


# --- scatter / hist / box / heatmap / pie / text ---------------------------

def test_scatter_carries_x_and_y_pairs():
    df = pd.DataFrame({"qty": [10, 20], "pct": [1.5, 2.5]})
    w = Widget(type="scatter", dataset="by_market", spec={"x": "qty", "y": "pct"})
    pm = build_plot_model(w, _ok(df))
    assert pm.series[0].x == [10, 20]
    assert pm.series[0].y == [1.5, 2.5]


def test_hist_carries_raw_values_and_bins():
    df = pd.DataFrame({"slip": [1.0, 1.5, 2.0, 9.0]})
    w = Widget(type="hist", dataset="by_market", spec={"x": "slip", "bins": 5})
    pm = build_plot_model(w, _ok(df))
    assert pm.series[0].y == [1.0, 1.5, 2.0, 9.0]
    assert pm.bins == 5


def test_box_groups_values_by_category():
    df = pd.DataFrame({"market": ["HK", "HK", "JP"], "pct": [10.0, 20.0, 90.0]})
    w = Widget(type="box", dataset="by_market", spec={"x": "market", "y": "pct"})
    pm = build_plot_model(w, _ok(df))
    assert [s.label for s in pm.series] == ["HK", "JP"]
    assert pm.series[0].y == [10.0, 20.0]


def test_heatmap_pivots_into_a_matrix():
    df = pd.DataFrame({"market": ["HK", "HK", "JP"], "hour": [9, 10, 9],
                       "n": [1, 2, 3]})
    w = Widget(type="heatmap", dataset="by_market",
               spec={"rows": "market", "cols": "hour", "value": "n", "agg": "sum"})
    pm = build_plot_model(w, _ok(df))
    assert pm.row_labels == ["HK", "JP"]
    assert pm.col_labels == ["9", "10"]
    assert pm.matrix[0] == [1.0, 2.0]
    assert pm.matrix[1][1] == 0.0            # missing cells fill with zero


def test_pie_builds_labelled_slices():
    w = Widget(type="pie", dataset="by_market",
               spec={"by": "market", "value": "n_orders"})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.series[0].x == ["Hong Kong", "Japan", "Korea"]
    assert pm.series[0].y == [12, 30, 5]


def test_text_substitutes_dataset_aggregates():
    w = Widget(type="text", dataset="by_market",
               spec={"markdown": "{{by_market.sum.n_orders}} orders, "
                                 "{{by_market.mean.completion_pct}}% done"})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.text == "47 orders, 53.9% done"


def test_text_leaves_unknown_placeholders_visible():
    w = Widget(type="text", dataset="by_market",
               spec={"markdown": "{{by_market.sum.nope}}"})
    pm = build_plot_model(w, _ok(_summary()))
    assert "nope" in pm.text


def test_unknown_widget_type_is_an_error_model():
    pm = build_plot_model(Widget(type="hologram", dataset="by_market"),
                          _ok(_summary()))
    assert pm.kind == "error"
    assert "hologram" in pm.error
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_plotmodel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.core.plotmodel'`

- [ ] **Step 3: Implement it**

Create `kdbmonitor/core/plotmodel.py`:

```python
"""(Widget, dataset results) -> PlotModel: the resolved, backend-agnostic plot.

Every decision the two renderers could disagree about — which rows, what
aggregation, sort order, colour assignment, decimal places, threshold colouring —
is made here exactly once. The renderers only draw.
"""
from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd

from kdbmonitor.core import theme
from kdbmonitor.core.dashboard_models import Widget

AGGS: dict[str, Callable[[pd.Series], Any]] = {
    "count": lambda s: s.count(),
    "nunique": lambda s: s.nunique(),
    "sum": lambda s: s.sum(),
    "mean": lambda s: s.mean(),
    "min": lambda s: s.min(),
    "max": lambda s: s.max(),
}

_OPS = {"=": operator.eq, "==": operator.eq, "!=": operator.ne,
        "<>": operator.ne, "<": operator.lt, "<=": operator.le,
        ">": operator.gt, ">=": operator.ge}


@dataclass
class Series:
    label: str
    x: list
    y: list
    color: str


@dataclass
class PlotModel:
    kind: str                       # widget type, or "error"
    title: str = ""
    error: Optional[str] = None

    # kpi
    value: Optional[str] = None
    value_color: Optional[str] = None
    caption: str = ""

    # table
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    cell_colors: dict = field(default_factory=dict)      # (row, col) -> hex

    # charts
    series: list[Series] = field(default_factory=list)
    x_label: str = ""
    y_label: str = ""
    orientation: str = "v"          # bar only: v | h
    bins: int = 20                  # hist only
    donut: bool = False             # pie only
    regression: bool = False        # scatter only

    # heatmap
    matrix: list[list[float]] = field(default_factory=list)
    row_labels: list[str] = field(default_factory=list)
    col_labels: list[str] = field(default_factory=list)
    annotate: bool = True

    # text
    text: str = ""


def _err(title: str, message: str) -> PlotModel:
    return PlotModel(kind="error", title=title, error=message)


def _need(df: pd.DataFrame, *columns: str) -> None:
    missing = [c for c in columns if c and c not in df.columns]
    if missing:
        raise KeyError(f"no column {', '.join(repr(m) for m in missing)} "
                       f"(have: {', '.join(map(str, df.columns))})")


def _fmt(value: Any, spec: str = "") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    try:
        return format(value, spec) if spec else str(value)
    except (TypeError, ValueError):
        return str(value)


def _threshold_color(value: Any, thresholds: list[dict]) -> str:
    for t in thresholds or []:
        op = _OPS.get(t.get("op", ">="))
        try:
            if op and op(value, t["value"]):
                return theme.resolve_color(t.get("color"))
        except TypeError:
            continue
    return theme.INK


# --- per-type resolvers ----------------------------------------------------

def _kpi(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    column, agg = spec.get("column", ""), spec.get("agg", "sum")
    _need(df, column)
    if agg not in AGGS:
        raise KeyError(f"unknown agg '{agg}'")
    if df.empty:
        return PlotModel(kind="kpi", title=title, value="—",
                         value_color=theme.INK, caption=spec.get("caption", ""))
    raw = AGGS[agg](df[column])
    text = _fmt(raw, spec.get("fmt", "")) + spec.get("suffix", "")
    return PlotModel(kind="kpi", title=title, value=text,
                     value_color=_threshold_color(raw, spec.get("thresholds", [])),
                     caption=spec.get("caption", ""))


def _table(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    columns = spec.get("columns") or list(df.columns)
    _need(df, *columns)
    formats = spec.get("formats", {})
    rows = [[_fmt(r[c], formats.get(c, "")) for c in columns]
            for _, r in df.iterrows()]

    cell_colors: dict = {}
    for rule in spec.get("highlight", []):
        col = rule["column"]
        if col not in columns:
            continue
        ci = columns.index(col)
        op = _OPS.get(rule.get("op", ">"))
        colour = theme.resolve_color(rule.get("color"))
        for ri, (_, r) in enumerate(df.iterrows()):
            try:
                if op and op(r[col], rule["value"]):
                    cell_colors[(ri, ci)] = colour
            except TypeError:
                continue

    return PlotModel(kind="table", title=title, columns=list(columns),
                     rows=rows, cell_colors=cell_colors)


def _xy_series(df: pd.DataFrame, spec: dict) -> list[Series]:
    """One series per y column, or one per hue value."""
    x = spec["x"]
    hue = spec.get("hue")
    ys = spec["y"] if isinstance(spec.get("y"), list) else [spec["y"]]
    _need(df, x, hue, *ys)

    if hue:
        out = []
        for i, (label, grp) in enumerate(df.groupby(hue, sort=True)):
            out.append(Series(str(label), grp[x].tolist(), grp[ys[0]].tolist(),
                              theme.color_for(i)))
        return out
    return [Series(y, df[x].tolist(), df[y].tolist(), theme.color_for(i))
            for i, y in enumerate(ys)]


def _sorted(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    order = spec.get("sort")
    if order not in ("asc", "desc"):
        return df
    y = spec["y"] if not isinstance(spec.get("y"), list) else spec["y"][0]
    return df.sort_values(y, ascending=order == "asc").reset_index(drop=True)


def _bar(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    d = _sorted(df, spec)
    return PlotModel(kind="bar", title=title, series=_xy_series(d, spec),
                     x_label=spec["x"], y_label=_y_label(spec),
                     orientation=spec.get("orientation", "v"))


def _line(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    return PlotModel(kind="line", title=title, series=_xy_series(df, spec),
                     x_label=spec["x"], y_label=_y_label(spec))


def _scatter(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    return PlotModel(kind="scatter", title=title, series=_xy_series(df, spec),
                     x_label=spec["x"], y_label=_y_label(spec),
                     regression=bool(spec.get("regression")))


def _y_label(spec: dict) -> str:
    y = spec.get("y")
    return ", ".join(y) if isinstance(y, list) else str(y)


def _hist(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    x = spec["x"]
    _need(df, x)
    return PlotModel(kind="hist", title=title, x_label=x, y_label="count",
                     bins=int(spec.get("bins", 20)),
                     series=[Series(x, [], df[x].tolist(), theme.color_for(0))])


def _box(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    x, y = spec.get("x"), spec["y"]
    _need(df, x, y)
    if not x:
        return PlotModel(kind="box", title=title, y_label=y,
                         series=[Series(y, [], df[y].tolist(),
                                        theme.color_for(0))])
    series = [Series(str(label), [], grp[y].tolist(), theme.color_for(i))
              for i, (label, grp) in enumerate(df.groupby(x, sort=True))]
    return PlotModel(kind="box", title=title, x_label=x, y_label=y, series=series)


def _heatmap(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    rows, cols, value = spec["rows"], spec["cols"], spec["value"]
    _need(df, rows, cols, value)
    pivot = df.pivot_table(index=rows, columns=cols, values=value,
                           aggfunc=spec.get("agg", "sum"), fill_value=0,
                           sort=False)
    return PlotModel(
        kind="heatmap", title=title, x_label=cols, y_label=rows,
        row_labels=[str(i) for i in pivot.index],
        col_labels=[str(c) for c in pivot.columns],
        matrix=[[float(v) for v in row] for row in pivot.values],
        annotate=bool(spec.get("annotate", True)))


def _pie(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    by, value = spec["by"], spec["value"]
    _need(df, by, value)
    return PlotModel(kind="pie", title=title, donut=bool(spec.get("donut")),
                     series=[Series(value, df[by].astype(str).tolist(),
                                    df[value].tolist(), theme.color_for(0))])


_PLACEHOLDER = re.compile(r"\{\{(\w+)\.(\w+)\.(\w+)\}\}")


def _text(df: pd.DataFrame, spec: dict, title: str, name: str) -> PlotModel:
    """Markdown with {{dataset.agg.column}} placeholders resolved."""
    def repl(m: re.Match) -> str:
        ds, agg, column = m.groups()
        if ds != name or agg not in AGGS or column not in df.columns:
            return m.group(0)          # leave it visible rather than lying
        return _fmt(AGGS[agg](df[column]), spec.get("fmt", ".1f")
                    if agg == "mean" else spec.get("fmt", ",.0f"))

    return PlotModel(kind="text", title=title,
                     text=_PLACEHOLDER.sub(repl, spec.get("markdown", "")))


_RESOLVERS: dict[str, Callable] = {
    "kpi": _kpi, "table": _table, "bar": _bar, "line": _line,
    "scatter": _scatter, "hist": _hist, "box": _box, "heatmap": _heatmap,
    "pie": _pie,
}


def build_plot_model(widget: Widget, results: dict) -> PlotModel:
    """Resolve a widget against its dataset result. Never raises."""
    title = widget.title
    result = results.get(widget.dataset)
    if result is None:
        return _err(title, f"unknown dataset '{widget.dataset}'")
    if result.error:
        return _err(title, result.error)
    if result.df is None:
        return _err(title, f"dataset '{widget.dataset}' produced no rows")

    try:
        if widget.type == "text":
            return _text(result.df, widget.spec, title, widget.dataset)
        resolver = _RESOLVERS.get(widget.type)
        if resolver is None:
            return _err(title, f"unknown widget type '{widget.type}'")
        return resolver(result.df, widget.spec, title)
    except (KeyError, ValueError, TypeError) as exc:
        return _err(title, str(exc).strip("'"))
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_plotmodel.py -v`
Expected: PASS (25 tests)

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/plotmodel.py tests/test_plotmodel.py
git commit -m "feat: resolve widgets into backend-agnostic plot models"
```

---

## Task 11: Plotly renderer (screen)

Charts only. KPI, table and text are drawn natively by Streamlit
(`st.metric` / `st.dataframe` / `st.markdown`) straight off the `PlotModel`.

**Files:**
- Create: `kdbmonitor/core/render_plotly.py`
- Test: `tests/test_render_plotly.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_plotly.py`:

```python
import pytest

from kdbmonitor.core import theme
from kdbmonitor.core.plotmodel import PlotModel, Series
from kdbmonitor.core.render_plotly import CHART_KINDS, figure


def _series(label="a", x=None, y=None, color=None):
    return Series(label, x or ["HK", "JP"], y or [1.0, 2.0],
                  color or theme.color_for(0))


def test_bar_makes_a_bar_trace():
    fig = figure(PlotModel(kind="bar", title="T", series=[_series()],
                           x_label="market", y_label="qty"))
    assert fig.data[0].type == "bar"
    assert list(fig.data[0].x) == ["HK", "JP"]
    assert fig.layout.title.text == "T"


def test_horizontal_bar_swaps_the_axes():
    fig = figure(PlotModel(kind="bar", series=[_series()], orientation="h"))
    assert fig.data[0].orientation == "h"
    assert list(fig.data[0].y) == ["HK", "JP"]


def test_line_uses_unified_hover_so_every_value_shows():
    fig = figure(PlotModel(kind="line", series=[_series("a"), _series("b")]))
    assert len(fig.data) == 2
    assert fig.data[0].type == "scatter"
    assert fig.data[0].mode == "lines+markers"
    assert fig.layout.hovermode == "x unified"


def test_series_colour_is_carried_through():
    fig = figure(PlotModel(kind="line", series=[_series(color="#123456")]))
    assert fig.data[0].line.color == "#123456"


def test_scatter_uses_markers_only():
    fig = figure(PlotModel(kind="scatter", series=[_series()]))
    assert fig.data[0].mode == "markers"


def test_hist_uses_the_series_values_and_bin_count():
    fig = figure(PlotModel(kind="hist", bins=7,
                           series=[_series(y=[1.0, 2.0, 3.0])]))
    assert fig.data[0].type == "histogram"
    assert list(fig.data[0].x) == [1.0, 2.0, 3.0]
    assert fig.data[0].nbinsx == 7


def test_box_makes_one_trace_per_group():
    fig = figure(PlotModel(kind="box",
                           series=[_series("HK", y=[1.0, 2.0]),
                                   _series("JP", y=[3.0])]))
    assert [t.type for t in fig.data] == ["box", "box"]
    assert fig.data[0].name == "HK"


def test_heatmap_carries_the_matrix_and_labels():
    fig = figure(PlotModel(kind="heatmap", matrix=[[1.0, 2.0], [3.0, 4.0]],
                           row_labels=["HK", "JP"], col_labels=["9", "10"]))
    assert fig.data[0].type == "heatmap"
    assert list(fig.data[0].y) == ["HK", "JP"]


def test_pie_becomes_a_donut_when_asked():
    pm = PlotModel(kind="pie", donut=True,
                   series=[Series("n", ["HK", "JP"], [1, 2], theme.BLUE)])
    fig = figure(pm)
    assert fig.data[0].type == "pie"
    assert fig.data[0].hole > 0


def test_error_model_renders_a_message_not_an_exception():
    fig = figure(PlotModel(kind="error", title="Broken",
                           error="connection refused"))
    assert "connection refused" in fig.layout.annotations[0].text


def test_unsupported_kind_is_rejected_loudly():
    with pytest.raises(ValueError, match="not a chart"):
        figure(PlotModel(kind="kpi", value="12"))


def test_chart_kinds_matches_the_renderer():
    assert CHART_KINDS == {"bar", "line", "scatter", "hist", "box",
                           "heatmap", "pie"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_render_plotly.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.core.render_plotly'`

- [ ] **Step 3: Implement it**

Create `kdbmonitor/core/render_plotly.py`:

```python
"""PlotModel -> plotly figure, for the interactive on-screen dashboard.

A dumb backend: it draws what the PlotModel already decided. Hovering a line
chart shows every series' value at that x, which is the whole reason the screen
does not simply display the PDF's images.
"""
from __future__ import annotations

import plotly.graph_objects as go

from kdbmonitor.core import theme
from kdbmonitor.core.plotmodel import PlotModel

CHART_KINDS = {"bar", "line", "scatter", "hist", "box", "heatmap", "pie"}


def _layout(fig: go.Figure, pm: PlotModel) -> go.Figure:
    fig.update_layout(
        template=theme.PLOTLY_TEMPLATE,
        title=dict(text=pm.title, x=0, font=dict(size=15)),
        margin=dict(l=8, r=8, t=40 if pm.title else 8, b=8),
        paper_bgcolor=theme.SCREEN_SURFACE,
        plot_bgcolor=theme.SCREEN_SURFACE,
        font=dict(color=theme.SCREEN_INK, size=12),
        xaxis_title=pm.x_label or None,
        yaxis_title=pm.y_label or None,
        showlegend=len(pm.series) > 1,
        legend=dict(orientation="h", y=-0.18),
    )
    fig.update_xaxes(gridcolor=theme.SCREEN_GRID, zeroline=False)
    fig.update_yaxes(gridcolor=theme.SCREEN_GRID, zeroline=False)
    return fig


def _error(pm: PlotModel) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=f"⚠ {pm.error}", showarrow=False,
                       font=dict(color=theme.CRITICAL, size=13),
                       xref="paper", yref="paper", x=0.5, y=0.5)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return _layout(fig, pm)


def figure(pm: PlotModel) -> go.Figure:
    """Build the interactive figure for a chart PlotModel."""
    if pm.kind == "error":
        return _error(pm)
    if pm.kind not in CHART_KINDS:
        raise ValueError(f"'{pm.kind}' is not a chart — render it natively")

    fig = go.Figure()

    if pm.kind == "bar":
        for s in pm.series:
            if pm.orientation == "h":
                fig.add_bar(y=s.x, x=s.y, name=s.label, orientation="h",
                            marker_color=s.color)
            else:
                fig.add_bar(x=s.x, y=s.y, name=s.label, marker_color=s.color)

    elif pm.kind in ("line", "scatter"):
        mode = "lines+markers" if pm.kind == "line" else "markers"
        for s in pm.series:
            fig.add_scatter(x=s.x, y=s.y, name=s.label, mode=mode,
                            line=dict(color=s.color),
                            marker=dict(color=s.color, size=7))

    elif pm.kind == "hist":
        for s in pm.series:
            fig.add_histogram(x=s.y, name=s.label, nbinsx=pm.bins,
                              marker_color=s.color)

    elif pm.kind == "box":
        for s in pm.series:
            fig.add_box(y=s.y, name=s.label, marker_color=s.color)

    elif pm.kind == "heatmap":
        fig.add_heatmap(z=pm.matrix, x=pm.col_labels, y=pm.row_labels,
                        colorscale=theme.SEQUENTIAL_CMAP,
                        texttemplate="%{z}" if pm.annotate else None)

    elif pm.kind == "pie":
        s = pm.series[0]
        fig.add_pie(labels=s.x, values=s.y, hole=0.55 if pm.donut else 0.0,
                    marker=dict(colors=[theme.color_for(i)
                                        for i in range(len(s.x))]))

    fig = _layout(fig, pm)
    if pm.kind == "line":
        fig.update_layout(hovermode="x unified")
    return fig
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_render_plotly.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/render_plotly.py tests/test_render_plotly.py
git commit -m "feat: interactive plotly renderer"
```

---

## Task 12: Matplotlib renderer (PDF)

Draws onto an `Axes` supplied by the caller — never a figure it owns. That is
what lets the page assembler place widgets on A4 exactly as
`short_sell_report.py` does.

**Files:**
- Create: `kdbmonitor/core/render_mpl.py`
- Test: `tests/test_render_mpl.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_mpl.py`:

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from kdbmonitor.core import theme
from kdbmonitor.core.plotmodel import PlotModel, Series
from kdbmonitor.core.render_mpl import draw


@pytest.fixture()
def ax():
    fig, ax = plt.subplots()
    yield ax
    plt.close(fig)


def _series(label="a", x=None, y=None, color=None):
    return Series(label, x or ["HK", "JP"], y or [1.0, 2.0],
                  color or theme.color_for(0))


def _texts(ax) -> str:
    return " | ".join(t.get_text() for t in ax.texts)


def test_kpi_draws_its_value_and_label(ax):
    draw(ax, PlotModel(kind="kpi", title="Orders", value="47",
                       value_color=theme.INK))
    assert "47" in _texts(ax)
    assert "Orders" in _texts(ax)


def test_kpi_value_uses_its_threshold_colour(ax):
    draw(ax, PlotModel(kind="kpi", title="Rejections", value="4",
                       value_color=theme.CRITICAL))
    value = [t for t in ax.texts if t.get_text() == "4"][0]
    assert value.get_color() == theme.CRITICAL


def test_table_creates_a_table_artist(ax):
    draw(ax, PlotModel(kind="table", title="By market",
                       columns=["market", "orders"],
                       rows=[["Hong Kong", "12"], ["Japan", "30"]]))
    assert len(ax.tables) == 1
    cells = ax.tables[0].get_celld()
    assert cells[(0, 0)].get_text().get_text() == "market"


def test_table_highlight_colours_the_right_cell(ax):
    draw(ax, PlotModel(kind="table", columns=["market", "rejects"],
                       rows=[["Hong Kong", "3"], ["Japan", "0"]],
                       cell_colors={(0, 1): theme.CRITICAL}))
    cells = ax.tables[0].get_celld()
    assert cells[(1, 1)].get_text().get_color() == theme.CRITICAL   # +1 header row
    assert cells[(2, 1)].get_text().get_color() != theme.CRITICAL


def test_vertical_bar_draws_one_patch_per_value(ax):
    draw(ax, PlotModel(kind="bar", series=[_series()]))
    assert len(ax.patches) == 2


def test_horizontal_bar_still_draws_every_value(ax):
    draw(ax, PlotModel(kind="bar", series=[_series()], orientation="h"))
    assert len(ax.patches) == 2


def test_line_draws_one_line_per_series(ax):
    draw(ax, PlotModel(kind="line", series=[_series("a"), _series("b")]))
    assert len(ax.lines) == 2


def test_line_series_keeps_its_colour(ax):
    draw(ax, PlotModel(kind="line", series=[_series(color="#123456")]))
    assert ax.lines[0].get_color() == "#123456"


def test_scatter_draws_a_collection(ax):
    draw(ax, PlotModel(kind="scatter",
                       series=[_series(x=[1, 2], y=[3.0, 4.0])]))
    assert len(ax.collections) == 1


def test_hist_draws_bars(ax):
    draw(ax, PlotModel(kind="hist", bins=3,
                       series=[_series(y=[1.0, 2.0, 3.0, 4.0])]))
    assert len(ax.patches) > 0


def test_box_draws_one_box_per_group(ax):
    draw(ax, PlotModel(kind="box",
                       series=[_series("HK", y=[1.0, 2.0, 3.0]),
                               _series("JP", y=[4.0, 5.0, 6.0])]))
    assert [t.get_text() for t in ax.get_xticklabels()] == ["HK", "JP"]


def test_heatmap_labels_both_axes(ax):
    draw(ax, PlotModel(kind="heatmap", matrix=[[1.0, 2.0], [3.0, 4.0]],
                       row_labels=["HK", "JP"], col_labels=["9", "10"]))
    assert [t.get_text() for t in ax.get_yticklabels()] == ["HK", "JP"]


def test_pie_draws_one_wedge_per_slice(ax):
    draw(ax, PlotModel(kind="pie",
                       series=[Series("n", ["HK", "JP"], [1, 2], theme.BLUE)]))
    assert len(ax.patches) == 2


def test_text_widget_renders_its_markdown_body(ax):
    draw(ax, PlotModel(kind="text", text="47 orders today"))
    assert "47 orders today" in _texts(ax)


def test_error_model_prints_the_message_in_the_pdf(ax):
    draw(ax, PlotModel(kind="error", title="By market",
                       error="connection refused"))
    assert "connection refused" in _texts(ax)


def test_error_message_is_critical_coloured(ax):
    draw(ax, PlotModel(kind="error", title="X", error="boom"))
    assert any(t.get_color() == theme.CRITICAL for t in ax.texts)


def test_unknown_kind_does_not_raise(ax):
    draw(ax, PlotModel(kind="hologram", title="X"))
    assert "hologram" in _texts(ax)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_render_mpl.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.core.render_mpl'`

- [ ] **Step 3: Implement it**

Create `kdbmonitor/core/render_mpl.py`:

```python
"""PlotModel -> matplotlib axes, for the printed report.

Draws onto an ``Axes`` the caller owns so the page assembler controls layout.
Styling follows short_sell_report.py: light surface, no chart junk, values
labelled directly rather than read off an axis.
"""
from __future__ import annotations

import seaborn as sns

from kdbmonitor.core import theme
from kdbmonitor.core.plotmodel import PlotModel


def _bare(ax, keep_bottom: bool = True) -> None:
    ax.set_facecolor(theme.SURFACE)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_visible(keep_bottom)
    if keep_bottom:
        ax.spines["bottom"].set_color(theme.BASELINE)
    ax.tick_params(length=0, labelsize=9, colors=theme.INK2)


def _title(ax, pm: PlotModel) -> None:
    if pm.title:
        ax.set_title(pm.title, fontsize=12, fontweight="bold", color=theme.INK,
                     loc="left", pad=10)


def _kpi(ax, pm: PlotModel) -> None:
    ax.axis("off")
    ax.text(0, 0.62, pm.value or "—", fontsize=26, fontweight="bold",
            color=pm.value_color or theme.INK, transform=ax.transAxes,
            va="center")
    ax.text(0, 0.24, pm.title, fontsize=10.5, color=theme.MUTED,
            transform=ax.transAxes, va="center")
    if pm.caption:
        ax.text(0, 0.05, pm.caption, fontsize=8.5, color=theme.MUTED,
                transform=ax.transAxes, va="center")


def _table(ax, pm: PlotModel) -> None:
    ax.axis("off")
    _title(ax, pm)
    if not pm.rows:
        ax.text(0, 0.5, "no rows", fontsize=10, color=theme.MUTED,
                transform=ax.transAxes)
        return

    # bbox=[0,0,1,1] makes the table fill its axes exactly (no internal gap).
    table = ax.table(cellText=pm.rows, colLabels=pm.columns, cellLoc="right",
                     colLoc="right", bbox=[0, 0, 1, 1])
    table.auto_set_font_size(False)
    table.set_fontsize(10.5)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(theme.GRID)
        cell.set_linewidth(0.8)
        if col == 0:
            cell.get_text().set_ha("left")
        if row == 0:
            cell.set_facecolor(theme.INK)
            cell.set_text_props(color="white", fontweight="bold")
        else:
            cell.set_facecolor(theme.SURFACE if row % 2 else "#f4f3f0")
            colour = pm.cell_colors.get((row - 1, col))   # row 0 is the header
            if colour:
                cell.get_text().set_color(colour)
                cell.get_text().set_fontweight("bold")


def _bar(ax, pm: PlotModel) -> None:
    _bare(ax)
    _title(ax, pm)
    n = len(pm.series)
    for i, s in enumerate(pm.series):
        positions = [p + i * (0.8 / n) for p in range(len(s.x))]
        if pm.orientation == "h":
            ax.barh(positions, s.y, height=0.8 / n, color=s.color,
                    label=s.label, zorder=3)
        else:
            ax.bar(positions, s.y, width=0.8 / n, color=s.color,
                   label=s.label, zorder=3)
    labels = [str(v) for v in pm.series[0].x] if pm.series else []
    centres = [p + 0.4 - 0.4 / n for p in range(len(labels))]
    if pm.orientation == "h":
        ax.set_yticks(centres, labels)
        ax.set_xticks([])
    else:
        ax.set_xticks(centres, labels)
    if n > 1:
        ax.legend(frameon=False, fontsize=9)


def _line(ax, pm: PlotModel) -> None:
    _bare(ax)
    _title(ax, pm)
    for s in pm.series:
        ax.plot(s.x, s.y, color=s.color, label=s.label, marker="o",
                markersize=3.5, linewidth=1.8)
    ax.grid(axis="y", color=theme.GRID, linewidth=0.8)
    if len(pm.series) > 1:
        ax.legend(frameon=False, fontsize=9)


def _scatter(ax, pm: PlotModel) -> None:
    _bare(ax)
    _title(ax, pm)
    for s in pm.series:
        ax.scatter(s.x, s.y, color=s.color, label=s.label, s=26, zorder=3)
        if pm.regression and len(s.x) > 1:
            sns.regplot(x=list(s.x), y=list(s.y), ax=ax, scatter=False,
                        color=s.color, line_kws={"linewidth": 1.4})
    ax.set_xlabel(pm.x_label, fontsize=9.5, color=theme.INK2)
    ax.set_ylabel(pm.y_label, fontsize=9.5, color=theme.INK2)
    if len(pm.series) > 1:
        ax.legend(frameon=False, fontsize=9)


def _hist(ax, pm: PlotModel) -> None:
    _bare(ax)
    _title(ax, pm)
    for s in pm.series:
        sns.histplot(x=s.y, bins=pm.bins, ax=ax, color=s.color, kde=False)
    ax.set_xlabel(pm.x_label, fontsize=9.5, color=theme.INK2)
    ax.set_ylabel("count", fontsize=9.5, color=theme.INK2)


def _box(ax, pm: PlotModel) -> None:
    _bare(ax)
    _title(ax, pm)
    data = [s.y for s in pm.series]
    labels = [s.label for s in pm.series]
    parts = ax.boxplot(data, tick_labels=labels, patch_artist=True,
                       medianprops={"color": theme.INK})
    for patch, s in zip(parts["boxes"], pm.series):
        patch.set_facecolor(s.color)
        patch.set_alpha(0.75)
    ax.set_ylabel(pm.y_label, fontsize=9.5, color=theme.INK2)


def _heatmap(ax, pm: PlotModel) -> None:
    _title(ax, pm)
    sns.heatmap(pm.matrix, ax=ax, cmap=theme.SEQUENTIAL_CMAP,
                annot=pm.annotate, fmt=".0f", cbar=False,
                xticklabels=pm.col_labels, yticklabels=pm.row_labels,
                linewidths=0.5, linecolor=theme.SURFACE)
    ax.tick_params(length=0, labelsize=9, colors=theme.INK2)
    ax.set_ylabel("")
    ax.set_xlabel("")


def _pie(ax, pm: PlotModel) -> None:
    _title(ax, pm)
    s = pm.series[0]
    colors = [theme.color_for(i) for i in range(len(s.x))]
    ax.pie(s.y, labels=[str(v) for v in s.x], colors=colors,
           autopct="%1.0f%%", textprops={"fontsize": 9, "color": theme.INK},
           wedgeprops={"width": 0.45} if pm.donut else None)
    ax.set_aspect("equal")


def _text(ax, pm: PlotModel) -> None:
    ax.axis("off")
    _title(ax, pm)
    ax.text(0, 0.95, pm.text, fontsize=10.5, color=theme.INK2, wrap=True,
            transform=ax.transAxes, va="top")


def _error(ax, pm: PlotModel, message: str) -> None:
    ax.axis("off")
    _title(ax, pm)
    ax.text(0.5, 0.5, f"⚠ {message}", fontsize=10.5, color=theme.CRITICAL,
            ha="center", va="center", wrap=True, transform=ax.transAxes)


_DRAWERS = {
    "kpi": _kpi, "table": _table, "bar": _bar, "line": _line,
    "scatter": _scatter, "hist": _hist, "box": _box, "heatmap": _heatmap,
    "pie": _pie, "text": _text,
}


def draw(ax, pm: PlotModel) -> None:
    """Draw a PlotModel onto ``ax``. Never raises — a broken panel prints as a
    visible error, because a silently missing chart reads as 'nothing to report'."""
    if pm.kind == "error":
        _error(ax, pm, pm.error or "unknown error")
        return
    drawer = _DRAWERS.get(pm.kind)
    if drawer is None:
        _error(ax, pm, f"unknown widget type '{pm.kind}'")
        return
    try:
        drawer(ax, pm)
    except Exception as exc:      # noqa: BLE001 - never break the whole page
        ax.clear()
        _error(ax, pm, str(exc))
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_render_mpl.py -v`
Expected: PASS (17 tests). If your matplotlib predates 3.9, `boxplot` takes
`labels=` rather than `tick_labels=`; adjust `_box` accordingly.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/render_mpl.py tests/test_render_mpl.py
git commit -m "feat: matplotlib/seaborn renderer for the printed report"
```

---

## Task 13: PDF page assembly

Places widgets on A4 and paginates. Rendering comes from the frames already on
screen, so the downloaded page shows the numbers the user is looking at.

**Files:**
- Create: `kdbmonitor/core/dashpdf.py`
- Test: `tests/test_dashpdf.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashpdf.py`:

```python
from datetime import date, datetime

import pandas as pd

from kdbmonitor.core.dashboard_models import Dashboard, Row, Widget
from kdbmonitor.core.dashpdf import (
    CONTENT_H_FIRST, dashboard_to_pdf_bytes, paginate, pdf_filename,
)
from kdbmonitor.core.dataset import DatasetResult
from kdbmonitor.core.timectx import ResolvedTime

AS_OF = datetime(2026, 7, 26, 9, 15)
RT = ResolvedTime("realtime", None, None)
HIST = ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 30))


def _results() -> dict:
    df = pd.DataFrame([{"market": "Hong Kong", "n_orders": 12, "pct": 61.4},
                       {"market": "Japan", "n_orders": 30, "pct": 88.2}])
    return {"by_market": DatasetResult("by_market", df, "q", None, row_count=2)}


def _dash(rows) -> Dashboard:
    return Dashboard(id=1, name="Short sell", description="by market", rows=rows)


# --- pagination ------------------------------------------------------------

def test_rows_that_fit_stay_on_one_page():
    rows = [Row(height_in=2.0), Row(height_in=2.0)]
    pages = paginate(rows)
    assert len(pages) == 1
    assert len(pages[0]) == 2


def test_first_row_starts_below_the_header():
    (first_row, y_top), = paginate([Row(height_in=1.0)])[0]
    assert y_top > 0


def test_rows_overflow_onto_a_second_page():
    rows = [Row(height_in=3.0) for _ in range(5)]     # 15in > one A4 page
    pages = paginate(rows)
    assert len(pages) >= 2
    assert sum(len(p) for p in pages) == 5


def test_a_row_taller_than_a_page_still_gets_placed():
    pages = paginate([Row(height_in=99.0), Row(height_in=1.0)])
    assert sum(len(p) for p in pages) == 2            # no infinite loop


def test_continuation_pages_fit_more_rows_than_the_first():
    assert CONTENT_H_FIRST < 11.69                    # header eats into page 1


# --- rendering -------------------------------------------------------------

def test_pdf_bytes_look_like_a_pdf():
    dash = _dash([Row(height_in=1.0, widgets=[
        Widget(type="kpi", dataset="by_market", title="Orders",
               spec={"column": "n_orders", "agg": "sum", "fmt": ",.0f"})])])
    out = dashboard_to_pdf_bytes(dash, _results(), RT, AS_OF)
    assert out.startswith(b"%PDF")
    assert len(out) > 1000


def test_a_full_page_of_mixed_widgets_renders():
    dash = _dash([
        Row(height_in=0.9, widgets=[
            Widget(type="kpi", dataset="by_market", title="Orders",
                   spec={"column": "n_orders", "agg": "sum", "fmt": ",.0f"}),
            Widget(type="kpi", dataset="by_market", title="Completion",
                   spec={"column": "pct", "agg": "mean", "fmt": ".1f",
                         "suffix": "%"})]),
        Row(height_in=2.0, widgets=[
            Widget(type="table", dataset="by_market", title="By market")]),
        Row(height_in=2.6, widgets=[
            Widget(type="bar", dataset="by_market", title="Completion",
                   spec={"x": "market", "y": "pct"}),
            Widget(type="line", dataset="by_market", title="Orders",
                   spec={"x": "market", "y": "n_orders"})]),
    ])
    assert dashboard_to_pdf_bytes(dash, _results(), HIST, AS_OF).startswith(b"%PDF")


def test_a_broken_dataset_still_produces_a_pdf():
    results = {"by_market": DatasetResult("by_market", None, "q",
                                          "connection refused")}
    dash = _dash([Row(height_in=2.0, widgets=[
        Widget(type="table", dataset="by_market", title="By market")])])
    assert dashboard_to_pdf_bytes(dash, results, RT, AS_OF).startswith(b"%PDF")


def test_an_empty_dashboard_still_produces_a_pdf():
    assert dashboard_to_pdf_bytes(_dash([]), {}, RT, AS_OF).startswith(b"%PDF")


def test_widget_widths_do_not_have_to_be_equal():
    dash = _dash([Row(height_in=2.0, widgets=[
        Widget(type="table", dataset="by_market", width=3.0),
        Widget(type="kpi", dataset="by_market", width=1.0,
               spec={"column": "n_orders", "agg": "sum"})])])
    assert dashboard_to_pdf_bytes(dash, _results(), RT, AS_OF).startswith(b"%PDF")


# --- filename --------------------------------------------------------------

def test_filename_is_slugged_and_stamped():
    assert pdf_filename(_dash([]), AS_OF) == "short_sell_2026-07-26_0915.pdf"


def test_filename_strips_awkward_characters():
    dash = Dashboard(id=1, name="P&L / risk (EOD)")
    assert pdf_filename(dash, AS_OF) == "p_l_risk_eod_2026-07-26_0915.pdf"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_dashpdf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.core.dashpdf'`

- [ ] **Step 3: Implement it**

Create `kdbmonitor/core/dashpdf.py`:

```python
"""Dashboard -> A4 PDF.

Renders from the dataset results already on screen — never a fresh query — so the
downloaded page is the state the user was looking at, not a near-miss taken a
moment later.
"""
from __future__ import annotations

import io
import re
from datetime import datetime

from kdbmonitor.core import theme
from kdbmonitor.core.dashboard_models import Dashboard, Row
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.render_mpl import draw
from kdbmonitor.core.timectx import ResolvedTime

PAGE_W, PAGE_H = 8.27, 11.69       # A4 portrait, inches
MARGIN = 0.6
HEADER_H_FIRST = 1.05              # title band on page 1
HEADER_H_CONT = 0.45               # slimmer band on continuation pages
FOOTER_H = 0.45
GUTTER = 0.28                      # between rows and between widgets

CONTENT_H_FIRST = PAGE_H - MARGIN * 2 - HEADER_H_FIRST - FOOTER_H
CONTENT_H_CONT = PAGE_H - MARGIN * 2 - HEADER_H_CONT - FOOTER_H
CONTENT_W = PAGE_W - MARGIN * 2


def paginate(rows: list[Row]) -> list[list[tuple[Row, float]]]:
    """Split rows into pages of ``(row, y_top)``, y measured in inches from the
    top of the page. A row taller than a whole page is placed anyway rather than
    looping forever — it simply overflows its page."""
    pages: list[list[tuple[Row, float]]] = []
    page: list[tuple[Row, float]] = []
    y = MARGIN + HEADER_H_FIRST
    limit = PAGE_H - MARGIN - FOOTER_H

    for row in rows:
        if page and y + row.height_in > limit:
            pages.append(page)
            page = []
            y = MARGIN + HEADER_H_CONT
        page.append((row, y))
        y += row.height_in + GUTTER

    if page:
        pages.append(page)
    return pages


def _rect(x_in: float, y_top_in: float, w_in: float, h_in: float) -> list[float]:
    """Inches from the top-left -> matplotlib figure coordinates."""
    return [x_in / PAGE_W, 1.0 - (y_top_in + h_in) / PAGE_H,
            w_in / PAGE_W, h_in / PAGE_H]


def _header(fig, dashboard: Dashboard, rt: ResolvedTime, as_of: datetime,
            first: bool) -> None:
    import matplotlib.pyplot as plt

    if first:
        fig.text(MARGIN / PAGE_W, 1 - 0.42 / PAGE_H, dashboard.name,
                 fontsize=22, fontweight="bold", color=theme.INK, va="top")
        subtitle = rt.label
        if dashboard.description:
            subtitle = f"{dashboard.description}  ·  {subtitle}"
        if rt.mode == "realtime":
            subtitle += f"  ·  as of {as_of:%Y-%m-%d %H:%M}"
        fig.text(MARGIN / PAGE_W, 1 - 0.78 / PAGE_H, subtitle,
                 fontsize=11, color=theme.INK2, va="top")
        rule_y = 1 - (MARGIN + HEADER_H_FIRST - 0.18) / PAGE_H
    else:
        fig.text(MARGIN / PAGE_W, 1 - 0.42 / PAGE_H,
                 f"{dashboard.name} (continued)", fontsize=12,
                 fontweight="bold", color=theme.INK2, va="top")
        rule_y = 1 - (MARGIN + HEADER_H_CONT - 0.12) / PAGE_H

    fig.add_artist(plt.Line2D([MARGIN / PAGE_W, 1 - MARGIN / PAGE_W],
                              [rule_y, rule_y], color=theme.GRID, lw=1,
                              transform=fig.transFigure))


def _footer(fig, as_of: datetime, page_no: int, total: int) -> None:
    import matplotlib.pyplot as plt

    y = (MARGIN + FOOTER_H - 0.14) / PAGE_H
    fig.add_artist(plt.Line2D([MARGIN / PAGE_W, 1 - MARGIN / PAGE_W], [y, y],
                              color=theme.GRID, lw=1, transform=fig.transFigure))
    fig.text(MARGIN / PAGE_W, y - 0.2 / PAGE_H,
             f"Generated {as_of:%Y-%m-%d %H:%M}  ·  KdbMonitor",
             fontsize=8.5, color=theme.MUTED, va="top")
    fig.text(1 - MARGIN / PAGE_W, y - 0.2 / PAGE_H, f"{page_no} / {total}",
             fontsize=8.5, color=theme.MUTED, va="top", ha="right")


def dashboard_to_pdf_bytes(dashboard: Dashboard, results: dict,
                           rt: ResolvedTime, as_of: datetime) -> bytes:
    """Render the dashboard's current state to a multi-page A4 PDF."""
    theme.apply_seaborn_theme()
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    pages = paginate(dashboard.rows) or [[]]
    buf = io.BytesIO()

    with PdfPages(buf) as pdf:
        for page_no, page in enumerate(pages, start=1):
            fig = plt.figure(figsize=(PAGE_W, PAGE_H))
            fig.patch.set_facecolor(theme.SURFACE)
            _header(fig, dashboard, rt, as_of, first=page_no == 1)

            for row, y_top in page:
                widgets = row.widgets
                if not widgets:
                    continue
                total_w = sum(max(w.width, 0.01) for w in widgets)
                usable = CONTENT_W - GUTTER * (len(widgets) - 1)
                x = MARGIN
                for widget in widgets:
                    w_in = usable * (max(widget.width, 0.01) / total_w)
                    ax = fig.add_axes(_rect(x, y_top, w_in, row.height_in))
                    draw(ax, build_plot_model(widget, results))
                    x += w_in + GUTTER

            _footer(fig, as_of, page_no, len(pages))
            pdf.savefig(fig)
            plt.close(fig)

    return buf.getvalue()


def pdf_filename(dashboard: Dashboard, as_of: datetime) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", dashboard.name.lower()).strip("_") or "dashboard"
    return f"{slug}_{as_of:%Y-%m-%d_%H%M}.pdf"
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_dashpdf.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Eyeball one**

```bash
python -c "
from datetime import datetime
import pandas as pd
from kdbmonitor.core.dashboard_models import Dashboard, Row, Widget
from kdbmonitor.core.dataset import DatasetResult
from kdbmonitor.core.dashpdf import dashboard_to_pdf_bytes
from kdbmonitor.core.timectx import ResolvedTime
df = pd.DataFrame([{'market':'Hong Kong','n':12,'pct':61.4},
                   {'market':'Japan','n':30,'pct':88.2},
                   {'market':'Korea','n':5,'pct':12.0}])
res = {'m': DatasetResult('m', df, 'q', None, row_count=3)}
d = Dashboard(id=1, name='Smoke test', description='layout check', rows=[
  Row(height_in=0.9, widgets=[
     Widget(type='kpi', dataset='m', title='Orders', spec={'column':'n','agg':'sum','fmt':',.0f'}),
     Widget(type='kpi', dataset='m', title='Completion', spec={'column':'pct','agg':'mean','fmt':'.1f','suffix':'%'})]),
  Row(height_in=2.2, widgets=[Widget(type='table', dataset='m', title='By market')]),
  Row(height_in=2.8, widgets=[
     Widget(type='bar', dataset='m', title='Completion', spec={'x':'market','y':'pct','orientation':'h'}),
     Widget(type='pie', dataset='m', title='Share', spec={'by':'market','value':'n','donut':True})]),
])
open('smoke.pdf','wb').write(dashboard_to_pdf_bytes(d, res, ResolvedTime('realtime',None,None), datetime.now()))
print('wrote smoke.pdf')
"
```

Open `smoke.pdf`. Expected: an A4 page with a title band, two KPIs, a table, a
horizontal bar chart and a donut, all inside the margins with no overlap. Delete
it afterwards (`rm smoke.pdf`) — it is a scratch file, do not commit it.

- [ ] **Step 6: Commit**

```bash
git add kdbmonitor/core/dashpdf.py tests/test_dashpdf.py
git commit -m "feat: assemble dashboards onto A4 pages"
```

---

## Task 14: Admin — environments

**Files:**
- Modify: `kdbmonitor/ui/admin.py:28-47` (demo blurb), `:50-70` (add form), `:72-102` (server list)
- Test: `tests/test_ui_smoke.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_smoke.py`:

```python
def test_admin_module_exposes_the_environment_view():
    from kdbmonitor.ui import admin
    assert hasattr(admin, "_render_environments")
```

Run: `python -m pytest tests/test_ui_smoke.py -k environment -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 2: Add env and kind to the add-connection form**

In `kdbmonitor/ui/admin.py`, replace the `add_conn` form block:

```python
    st.markdown("**Add a KDB connection**")
    with st.form("add_conn", clear_on_submit=True, border=True):
        f = st.columns([2, 2, 1, 1.6, 1.4, 1.2], vertical_alignment="bottom")
        name = f[0].text_input("Name", placeholder="e.g. order-rdb")
        host = f[1].text_input("Host", value="localhost")
        port = f[2].number_input("Port", 1, 65535, 5010)
        env = f[3].text_input("Environment", placeholder="e.g. orders",
                              help="Pair a real-time and a historical server by "
                                   "giving them the same environment name.")
        kind = f[4].selectbox("Kind", ["realtime", "historical"],
                              help="Historical servers carry a date column; "
                                   "dashboards inject the date range for you.")
        submitted = f[5].form_submit_button("Add", icon=":material/add:",
                                            use_container_width=True)
        if submitted and name:
            try:
                store.add_connection(Connection(
                    id=None, name=name.strip(), host=host.strip(), port=int(port),
                    kind=kind, env=env.strip()))
            except ValueError as exc:
                st.error(str(exc), icon=":material/error:")
            except Exception as exc:  # noqa: BLE001 — DB failure shouldn't crash the page
                st.error(f"Could not add connection: {exc}", icon=":material/error:")
            else:
                st.toast(f"Added '{name}'", icon=":material/check:")
                st.rerun()
        elif submitted and not name:
            st.error("Connection needs a name.", icon=":material/error:")
```

- [ ] **Step 3: Badge the kind in the server list**

In the `for c in conns:` loop, replace the name cell so the environment and kind
are visible at a glance:

```python
            badge = (" :blue-badge[demo]" if is_demo else "")
            badge += (" :violet-badge[historical]" if c.kind == "historical"
                      else " :gray-badge[real-time]")
            row[0].markdown(f"**{c.name}**{badge}"
                            + (f"<br>:gray[env: {c.env or c.name}]"),
                            unsafe_allow_html=True)
```

- [ ] **Step 4: Add the environments panel**

Add this function to `kdbmonitor/ui/admin.py`, above `render`:

```python
def _render_environments(store) -> None:
    """Show real-time/historical pairs, and flag any half-configured one.

    A dashboard can only offer 'historical' for an environment that actually has
    a historical server, so a missing side is worth surfacing here rather than as
    a query error later.
    """
    st.markdown("**Environments**")
    envs = store.list_environments()
    if not envs:
        st.caption("None yet — environments appear once you add connections.")
        return

    with st.container(border=True):
        st.caption("A real-time and a historical server sharing an environment "
                   "name form a pair. Dashboards pick the environment; the date "
                   "range decides which server is queried.")
        for env, pair in sorted(envs.items()):
            e = st.columns([2, 2.5, 2.5], vertical_alignment="center")
            e[0].markdown(f"**{env}**")
            for i, kind in enumerate(("realtime", "historical"), start=1):
                conn = pair[kind]
                label = "Real-time" if kind == "realtime" else "Historical"
                if conn is not None:
                    e[i].markdown(f":green-badge[{label}] `{conn.name}`")
                else:
                    e[i].markdown(f":orange-badge[{label} missing]")
            if pair["historical"] is None:
                st.caption(f":orange[Environment '{env}' has no historical server "
                           f"— dashboards on it cannot query date ranges.]")
```

- [ ] **Step 5: Call it**

In `render`, immediately after the registered-servers loop and before the SMTP
section, add:

```python
    _render_environments(store)
```

- [ ] **Step 6: Update the demo blurb**

Replace the two demo caption/condition lines so the third server is mentioned and
the "already loaded" check covers it:

```python
        d[0].caption("Adds `kdp_demo` (QATT), `orders_demo` (target, work_order, "
                     "target_state) and `orders_hdb_demo` (the same tables with a "
                     "date column) with live synthetic data.")
        existing = {c.name for c in conns}
        already = existing.issuperset({"kdp_demo", "orders_demo", "orders_hdb_demo"})
```

- [ ] **Step 7: Run the tests and the app**

Run: `python -m pytest -q`
Expected: PASS

Run: `python -m streamlit run app.py`
Open Admin. Expected: the add form has Environment and Kind; loading the demo
servers creates three connections; the Environments panel shows `orders` with
both sides green and `marketdata` flagged as missing its historical side.

- [ ] **Step 8: Commit**

```bash
git add kdbmonitor/ui/admin.py tests/test_ui_smoke.py
git commit -m "feat: manage realtime/historical environments in Admin"
```

---

## Task 15: Dashboards page — gallery, tabs, view, PDF

**Files:**
- Create: `kdbmonitor/ui/dashboards.py`
- Modify: `app.py:8` (import), `:64-90` (page wiring)
- Test: `tests/test_ui_dashboards.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_dashboards.py`:

```python
import pandas as pd

from kdbmonitor.core.dashboard_models import Dashboard, Row, Widget
from kdbmonitor.core.dataset import DatasetResult
from kdbmonitor.ui import dashboards


def _results() -> dict:
    df = pd.DataFrame([{"market": "HK", "n": 12}, {"market": "JP", "n": 30}])
    return {"m": DatasetResult("m", df, "q", None, row_count=2)}


def test_module_imports_without_a_running_streamlit_app():
    assert hasattr(dashboards, "render")


def test_time_context_options_cover_realtime_and_the_presets():
    labels = list(dashboards.TIME_OPTIONS)
    assert labels[0] == "Real-time"
    assert "Last 30 days" in labels
    assert "Custom range…" in labels


def test_time_option_roundtrips_to_a_spec():
    spec = dashboards.spec_for_option("Last 30 days")
    assert spec == {"mode": "historical",
                    "range": {"kind": "preset", "name": "last_30d"}}
    assert dashboards.option_for_spec(spec) == "Last 30 days"


def test_realtime_spec_roundtrips():
    assert dashboards.spec_for_option("Real-time") == {"mode": "realtime"}
    assert dashboards.option_for_spec({"mode": "realtime"}) == "Real-time"


def test_absolute_spec_maps_to_the_custom_option():
    spec = {"mode": "historical",
            "range": {"kind": "absolute", "from": "2026-06-01", "to": "2026-06-30"}}
    assert dashboards.option_for_spec(spec) == "Custom range…"


def test_native_kinds_are_the_ones_plotly_does_not_draw():
    from kdbmonitor.core.render_plotly import CHART_KINDS
    assert dashboards.NATIVE_KINDS.isdisjoint(CHART_KINDS)
    assert dashboards.NATIVE_KINDS == {"kpi", "table", "text", "error"}


def test_row_height_converts_inches_to_pixels():
    assert dashboards.row_height_px(2.0) == 192          # 2in at 96 dpi
    assert dashboards.row_height_px(0.9) == 86
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_ui_dashboards.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.ui.dashboards'`

- [ ] **Step 3: Implement the page**

Create `kdbmonitor/ui/dashboards.py`:

```python
# kdbmonitor/ui/dashboards.py
"""The Dashboards page: gallery, tab strip, live view, PDF export.

The tab strip is st.pills rather than st.tabs on purpose — st.tabs executes every
tab's body on each rerun, which under a refresh timer would fire every
dashboard's queries at KDB continuously. Pills keep exactly one dashboard live.
"""
from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from kdbmonitor.core.dashboard_models import Dashboard
from kdbmonitor.core.dashpdf import dashboard_to_pdf_bytes, pdf_filename
from kdbmonitor.core.dataset import run_datasets
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.render_plotly import figure
from kdbmonitor.core.timectx import PRESET_LABELS, PRESETS, resolve

NATIVE_KINDS = {"kpi", "table", "text", "error"}   # drawn by Streamlit, not plotly
DPI = 96

REFRESH_OPTIONS = {"Off": 0, "5s": 5, "10s": 10, "15s": 15, "30s": 30,
                   "1m": 60, "5m": 300, "15m": 900}

TIME_OPTIONS = {"Real-time": None, **{PRESET_LABELS[p]: p for p in PRESETS},
                "Custom range…": "custom"}


def spec_for_option(label: str) -> dict:
    preset = TIME_OPTIONS.get(label)
    if preset is None:
        return {"mode": "realtime"}
    if preset == "custom":
        return {"mode": "historical",
                "range": {"kind": "absolute", "from": "", "to": ""}}
    return {"mode": "historical", "range": {"kind": "preset", "name": preset}}


def option_for_spec(spec: dict) -> str:
    if (spec or {}).get("mode") != "historical":
        return "Real-time"
    rng = spec.get("range") or {}
    if rng.get("kind") == "preset" and rng.get("name") in PRESET_LABELS:
        return PRESET_LABELS[rng["name"]]
    return "Custom range…"


def row_height_px(height_in: float) -> int:
    return int(round(height_in * DPI))


# --- widget rendering ------------------------------------------------------

def _render_widget(pm, height_px: int) -> None:
    if pm.kind == "error":
        st.error(f"{pm.title or 'Widget'}: {pm.error}", icon=":material/error:")
        return
    if pm.kind == "kpi":
        st.metric(pm.title, pm.value, help=pm.caption or None)
        return
    if pm.kind == "text":
        if pm.title:
            st.markdown(f"**{pm.title}**")
        st.markdown(pm.text)
        return
    if pm.kind == "table":
        if pm.title:
            st.markdown(f"**{pm.title}**")
        st.dataframe({c: [r[i] for r in pm.rows] for i, c in enumerate(pm.columns)},
                     use_container_width=True, height=height_px, hide_index=True)
        return
    st.plotly_chart(figure(pm), use_container_width=True,
                    key=f"chart_{pm.title}_{id(pm)}")


def _render_rows(dashboard: Dashboard, results: dict) -> None:
    if not dashboard.rows:
        st.info("This dashboard has no widgets yet — open Edit to add some.",
                icon=":material/dashboard:")
        return
    for r_i, row in enumerate(dashboard.rows):
        if not row.widgets:
            continue
        cols = st.columns([max(w.width, 0.01) for w in row.widgets],
                          vertical_alignment="top")
        for c_i, widget in enumerate(row.widgets):
            with cols[c_i]:
                _render_widget(build_plot_model(widget, results),
                               row_height_px(row.height_in))


# --- state helpers ---------------------------------------------------------

def _frames_key(dashboard_id: int) -> str:
    return f"dash_frames_{dashboard_id}"


def _refresh(store, mgr, dashboard: Dashboard) -> dict:
    """Run every dataset and cache the frames, stamped with when they were taken."""
    results = run_datasets(dashboard, store, mgr, date.today())
    payload = {"results": results, "as_of": datetime.now(),
               "rt": resolve(dashboard.time_context, date.today())}
    st.session_state[_frames_key(dashboard.id)] = payload
    return payload


def _active_id(store) -> int | None:
    """The open dashboard, from the URL so it is bookmarkable."""
    raw = st.query_params.get("dash")
    ids = [d.id for d in store.list_dashboards()]
    if raw and raw.isdigit() and int(raw) in ids:
        return int(raw)
    return None


def _open(dashboard_id: int) -> None:
    st.query_params["dash"] = str(dashboard_id)
    st.rerun()


# --- gallery ---------------------------------------------------------------

def _render_gallery(store) -> None:
    st.subheader(":material/dashboard: Dashboards")
    head = st.columns([6, 1.4], vertical_alignment="center")
    head[0].caption("Saved views built from KDB queries. Open one to watch it "
                    "live, or export the current state as a PDF.")
    if head[1].button("New dashboard", icon=":material/add:", type="primary",
                      use_container_width=True):
        new_id = store.add_dashboard(Dashboard(id=None, name="New dashboard"))
        st.session_state["dash_edit_id"] = new_id
        st.session_state["dash_mode"] = "edit"
        st.rerun()

    dashboards_ = store.list_dashboards()
    if not dashboards_:
        st.info("No dashboards yet. Create one to get started.",
                icon=":material/dashboard:")
        return

    for d in dashboards_:
        with st.container(border=True):
            c = st.columns([3, 2.4, 1, 1, 1, 0.8], vertical_alignment="center")
            c[0].markdown(f"**{d.name}**"
                          + (f"<br>:gray[{d.description}]" if d.description else ""),
                          unsafe_allow_html=True)
            envs = sorted({ds.env for ds in d.datasets if ds.env})
            n_widgets = sum(len(r.widgets) for r in d.rows)
            c[1].markdown(f":gray[{len(d.datasets)} dataset(s) · {n_widgets} widget(s)]"
                          + (f"<br>:gray[env: {', '.join(envs)}]" if envs else ""),
                          unsafe_allow_html=True)
            if c[2].button("Open", key=f"open_{d.id}", icon=":material/open_in_new:",
                           use_container_width=True):
                _open(d.id)
            if c[3].button("Edit", key=f"edit_{d.id}", icon=":material/edit:",
                           use_container_width=True):
                st.session_state["dash_edit_id"] = d.id
                st.session_state["dash_mode"] = "edit"
                st.rerun()
            if c[4].button("Duplicate", key=f"dup_{d.id}", icon=":material/content_copy:",
                           use_container_width=True):
                copy = store.get_dashboard(d.id)
                copy.id = None
                copy.name = f"{copy.name} (copy)"
                store.add_dashboard(copy)
                st.toast(f"Duplicated '{d.name}'", icon=":material/check:")
                st.rerun()
            with c[5].popover("", icon=":material/delete:"):
                st.warning(f"Delete '{d.name}'?")
                if st.button("Confirm", key=f"delok_{d.id}", type="primary"):
                    store.delete_dashboard(d.id)
                    st.rerun()


# --- view ------------------------------------------------------------------

def _render_header(store, dashboard: Dashboard, payload: dict) -> None:
    bar = st.columns([3, 2, 1.4, 1.2], vertical_alignment="bottom")

    option = bar[0].selectbox("Period", list(TIME_OPTIONS),
                              index=list(TIME_OPTIONS).index(
                                  option_for_spec(dashboard.time_context)),
                              key=f"tc_{dashboard.id}")
    spec = spec_for_option(option)

    if option == "Custom range…":
        existing = (dashboard.time_context.get("range") or {})
        d1 = bar[1].date_input(
            "From", value=date.fromisoformat(existing["from"])
            if existing.get("from") else date.today(), key=f"tcf_{dashboard.id}")
        d2 = bar[2].date_input(
            "To", value=date.fromisoformat(existing["to"])
            if existing.get("to") else date.today(), key=f"tct_{dashboard.id}")
        spec = {"mode": "historical",
                "range": {"kind": "absolute", "from": d1.isoformat(),
                          "to": d2.isoformat()}}

    if spec != dashboard.time_context:
        dashboard.time_context = spec
        store.update_dashboard(dashboard)
        st.session_state.pop(_frames_key(dashboard.id), None)
        st.rerun()

    label = payload["rt"].label if payload else ""
    stamp = payload["as_of"].strftime("%H:%M:%S") if payload else "—"
    bar[3].markdown(f":gray[{label}]<br>:gray[updated {stamp}]",
                    unsafe_allow_html=True)


def _render_view(store, mgr, dashboard: Dashboard) -> None:
    all_dash = store.list_dashboards()
    names = {d.name: d.id for d in all_dash}
    picked = st.pills("Dashboards", list(names), default=dashboard.name,
                      label_visibility="collapsed", key="dash_pills")
    if picked and names[picked] != dashboard.id:
        _open(names[picked])

    top = st.columns([4, 1.3, 1.3, 1.3], vertical_alignment="bottom")
    top[0].subheader(dashboard.name)

    labels = list(REFRESH_OPTIONS)
    current = next((k for k, v in REFRESH_OPTIONS.items()
                    if v == dashboard.refresh_secs), "15s")
    chosen = top[1].selectbox("Refresh", labels, index=labels.index(current),
                              key=f"rf_{dashboard.id}")
    if REFRESH_OPTIONS[chosen] != dashboard.refresh_secs:
        dashboard.refresh_secs = REFRESH_OPTIONS[chosen]
        store.update_dashboard(dashboard)
        st.rerun()

    if top[2].button("Edit", icon=":material/edit:", use_container_width=True):
        st.session_state["dash_edit_id"] = dashboard.id
        st.session_state["dash_mode"] = "edit"
        st.rerun()
    if top[3].button("Gallery", icon=":material/grid_view:", use_container_width=True):
        st.query_params.pop("dash", None)
        st.rerun()

    payload = st.session_state.get(_frames_key(dashboard.id))
    _render_header(store, dashboard, payload)

    every = dashboard.refresh_secs or None

    @st.fragment(run_every=every)
    def _live() -> None:
        data = _refresh(store, mgr, dashboard)
        _render_rows(dashboard, data["results"])

    _live()

    _render_export(dashboard)


def _render_export(dashboard: Dashboard) -> None:
    payload = st.session_state.get(_frames_key(dashboard.id))
    st.divider()
    e = st.columns([1.6, 1.6, 4], vertical_alignment="center")

    if e[0].button("Generate PDF", icon=":material/picture_as_pdf:",
                   type="primary", use_container_width=True, disabled=not payload):
        st.session_state[f"pdf_{dashboard.id}"] = dashboard_to_pdf_bytes(
            dashboard, payload["results"], payload["rt"], payload["as_of"])

    data = st.session_state.get(f"pdf_{dashboard.id}")
    if data and payload:
        e[1].download_button("Download", data=data,
                             file_name=pdf_filename(dashboard, payload["as_of"]),
                             mime="application/pdf", icon=":material/download:",
                             use_container_width=True)
        e[2].caption("The PDF renders the numbers currently on screen — it does "
                     "not re-query.")


# --- entry point -----------------------------------------------------------

def render(store, mgr) -> None:
    if st.session_state.get("dash_mode") == "edit":
        from kdbmonitor.ui import dashboard_editor
        dashboard_editor.render(store, mgr)
        return

    active = _active_id(store)
    if active is None:
        _render_gallery(store)
        return

    dashboard = store.get_dashboard(active)
    if dashboard is None:
        st.query_params.pop("dash", None)
        st.rerun()
    _render_view(store, mgr, dashboard)
```

- [ ] **Step 4: Wire it into the app shell**

In `app.py`, add `dashboards` to the UI import:

```python
from kdbmonitor.ui import admin, builder, dashboards, monitor, result, reports, engine
```

Add the page function next to the others:

```python
def dashboards_page():
    dashboards.render(store, mgr)
```

Register the page and add it to the nav, between Reports and Admin:

```python
dashboards_pg = st.Page(dashboards_page, title="Dashboards", url_path="dashboards",
                        icon=":material/dashboard:")
```

```python
st.navigation([monitor_pg, builder_pg, dashboards_pg, reports_pg, admin_pg,
               result_pg]).run()
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_ui_dashboards.py -v`
Expected: PASS (7 tests)

Run: `python -m pytest -q`
Expected: PASS — including the app smoke test.

- [ ] **Step 6: Commit**

```bash
git add kdbmonitor/ui/dashboards.py app.py tests/test_ui_dashboards.py
git commit -m "feat: dashboards page with tab strip, live refresh and PDF export"
```

---

## Task 16: Editor — datasets

**Files:**
- Create: `kdbmonitor/ui/dashboard_editor.py`
- Test: `tests/test_ui_dashboard_editor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_dashboard_editor.py`:

```python
from kdbmonitor.core.dashboard_models import Dataset, Transform
from kdbmonitor.ui import dashboard_editor as ed


def test_module_imports_without_a_running_streamlit_app():
    assert hasattr(ed, "render")


def test_columns_for_a_guided_dataset_come_from_the_connection_schema():
    class FakeConn:
        schema = {"target": ["sym", "size", "side"]}

    ds = Dataset(name="d", env="orders", table="target")
    assert ed.dataset_columns(ds, FakeConn()) == ["sym", "size", "side"]


def test_columns_include_transform_outputs():
    class FakeConn:
        schema = {"target": ["sym", "size"]}

    ds = Dataset(name="d", env="orders", table="target", transforms=[
        Transform(kind="derive", params={"column": "market"}),
        Transform(kind="groupby", params={"keys": ["market"], "aggs": [
            {"column": "size", "func": "sum", "as": "order_qty"}]}),
    ])
    cols = ed.dataset_columns(ds, FakeConn())
    assert "market" in cols
    assert "order_qty" in cols


def test_groupby_output_replaces_the_upstream_columns():
    class FakeConn:
        schema = {"target": ["sym", "size"]}

    ds = Dataset(name="d", env="orders", table="target", transforms=[
        Transform(kind="groupby", params={"keys": ["sym"], "aggs": [
            {"column": "size", "func": "sum", "as": "order_qty"}]}),
    ])
    assert ed.dataset_columns(ds, FakeConn()) == ["sym", "order_qty"]


def test_rename_is_reflected_in_the_column_list():
    class FakeConn:
        schema = {"target": ["sym", "size"]}

    ds = Dataset(name="d", env="orders", table="target", transforms=[
        Transform(kind="rename", params={"mapping": {"size": "order_qty"}})])
    assert ed.dataset_columns(ds, FakeConn()) == ["sym", "order_qty"]


def test_raw_datasets_have_no_predictable_columns():
    ds = Dataset(name="d", env="orders", mode="raw", raw_qsql="select from t")
    assert ed.dataset_columns(ds, None) == []


def test_unique_name_avoids_collisions():
    assert ed.unique_name("orders", ["orders", "orders_2"]) == "orders_3"
    assert ed.unique_name("fills", ["orders"]) == "fills"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_ui_dashboard_editor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.ui.dashboard_editor'`

- [ ] **Step 3: Implement the editor shell and the Data section**

Create `kdbmonitor/ui/dashboard_editor.py`:

```python
# kdbmonitor/ui/dashboard_editor.py
"""Dashboard editor: datasets (the data) and rows of widgets (the layout).

Session-state driven, following ui/builder.py. The draft lives in
``st.session_state['dash_draft']`` and is written back to the DB only on Save, so
half-built datasets never reach the view.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Row, Transform, Widget,
)
from kdbmonitor.core.dataset import run_datasets
from kdbmonitor.core.timectx import PRESET_LABELS, PRESETS

OPS = ["=", "<>", "<", "<=", ">", ">=", "in", "like"]
VALUE_TYPES = ["symbol", "number", "string"]
AGG_FUNCS = ["count", "nunique", "sum", "mean", "min", "max"]
TRANSFORM_KINDS = ["derive", "filter", "groupby", "sort", "limit", "rename"]

RAW_HELP = (
    "Raw q. In historical mode you MUST constrain `date` — use "
    "`{{date_from}}` / `{{date_to}}` / `{{date_list}}`. Reference another "
    "dataset with `{{name.column}}`."
)


# --- pure helpers (unit-tested) -------------------------------------------

def unique_name(base: str, taken: list[str]) -> str:
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def dataset_columns(ds: Dataset, conn) -> list[str]:
    """The columns a dataset is expected to produce, so widget forms can offer
    a picker instead of a free-text box. Raw datasets are unpredictable."""
    if ds.mode == "raw" or conn is None:
        return []
    cols = list(getattr(conn, "schema", {}).get(ds.table, []))
    for t in ds.transforms:
        p = t.params
        if t.kind == "derive" and p.get("column"):
            cols.append(p["column"])
        elif t.kind == "groupby":
            cols = list(p.get("keys", [])) + [a["as"] for a in p.get("aggs", [])]
        elif t.kind == "rename":
            mapping = p.get("mapping", {})
            cols = [mapping.get(c, c) for c in cols]
    return list(dict.fromkeys(cols))


# --- draft state -----------------------------------------------------------

def _draft(store) -> Dashboard:
    draft = st.session_state.get("dash_draft")
    wanted = st.session_state.get("dash_edit_id")
    if draft is None or draft.id != wanted:
        draft = store.get_dashboard(wanted) or Dashboard(id=None, name="New dashboard")
        st.session_state["dash_draft"] = draft
    return draft


def _close() -> None:
    st.session_state.pop("dash_draft", None)
    st.session_state.pop("dash_mode", None)
    st.session_state.pop("dash_edit_id", None)


# --- dataset section -------------------------------------------------------

def _connection_for(store, ds: Dataset):
    envs = store.list_environments()
    pair = envs.get(ds.env) or {}
    return pair.get("realtime") or pair.get("historical")


def _filters_form(ds: Dataset, columns: list[str], key: str) -> None:
    st.caption("Filters — combined with AND, sent to KDB as the where clause.")
    for i, f in enumerate(list(ds.filters)):
        c = st.columns([2, 1.2, 2, 1.4, 1, 0.6], vertical_alignment="bottom")
        f.column = c[0].selectbox("Column", columns or [f.column],
                                  index=(columns.index(f.column)
                                         if f.column in columns else 0),
                                  key=f"{key}_fc_{i}")
        f.op = c[1].selectbox("Op", OPS, index=OPS.index(f.op) if f.op in OPS else 0,
                              key=f"{key}_fo_{i}")
        raw = c[2].text_input("Value", value=", ".join(map(str, f.value))
                              if isinstance(f.value, list) else str(f.value),
                              key=f"{key}_fv_{i}")
        f.value_type = c[3].selectbox("Type", VALUE_TYPES,
                                      index=VALUE_TYPES.index(f.value_type),
                                      key=f"{key}_ft_{i}")
        f.value = ([v.strip() for v in raw.split(",")] if f.op == "in"
                   else _coerce(raw, f.value_type))
        f.negated = c[4].checkbox("not", value=f.negated, key=f"{key}_fn_{i}")
        if c[5].button("", icon=":material/close:", key=f"{key}_fx_{i}"):
            ds.filters.pop(i)
            st.rerun()

    if st.button("Add filter", icon=":material/add:", key=f"{key}_addf"):
        from kdbmonitor.core.models import Filter
        ds.filters.append(Filter(column=columns[0] if columns else "",
                                 op="=", value="", value_type="symbol"))
        st.rerun()


def _coerce(raw: str, value_type: str):
    if value_type != "number":
        return raw
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return raw


def _transform_form(t: Transform, columns: list[str], key: str) -> None:
    p = t.params
    if t.kind == "derive":
        c = st.columns([1.6, 1.4, 3], vertical_alignment="bottom")
        p["column"] = c[0].text_input("New column", value=p.get("column", ""),
                                      key=f"{key}_dc")
        p["kind"] = c[1].selectbox("How", ["arithmetic", "suffix_map"],
                                   index=0 if p.get("kind", "arithmetic") == "arithmetic" else 1,
                                   key=f"{key}_dk")
        if p["kind"] == "arithmetic":
            p["expr"] = c[2].text_input("Expression", value=p.get("expr", ""),
                                        placeholder="100 * executed / size",
                                        key=f"{key}_de")
        else:
            p["source"] = c[2].selectbox("From column", columns or [p.get("source", "")],
                                         key=f"{key}_ds")
            m = st.text_area("Suffix → label (one per line, e.g. `.HK = Hong Kong`)",
                             value="\n".join(f"{k} = {v}" for k, v
                                             in p.get("mapping", {}).items()),
                             key=f"{key}_dm", height=90)
            p["mapping"] = dict(
                (a.strip(), b.strip())
                for a, _, b in (line.partition("=") for line in m.splitlines())
                if a.strip() and b.strip())
            p["default"] = st.text_input("Fallback", value=p.get("default", "Unknown"),
                                         key=f"{key}_dd")

    elif t.kind == "filter":
        c = st.columns([2, 1, 2], vertical_alignment="bottom")
        p["column"] = c[0].selectbox("Column", columns or [p.get("column", "")],
                                     key=f"{key}_fc")
        p["op"] = c[1].selectbox("Op", ["=", "!=", "<", "<=", ">", ">=", "in"],
                                 key=f"{key}_fo")
        p["value"] = _coerce(c[2].text_input("Value", value=str(p.get("value", "")),
                                             key=f"{key}_fv"), "number")

    elif t.kind == "groupby":
        p["keys"] = st.multiselect("Group by", columns, default=p.get("keys", []),
                                   key=f"{key}_gk")
        st.caption("Aggregations")
        for i, a in enumerate(list(p.get("aggs", []))):
            c = st.columns([2, 1.4, 2, 0.6], vertical_alignment="bottom")
            a["column"] = c[0].selectbox("Column", columns or [a["column"]],
                                         key=f"{key}_gc_{i}")
            a["func"] = c[1].selectbox("Func", AGG_FUNCS,
                                       index=AGG_FUNCS.index(a["func"]),
                                       key=f"{key}_gf_{i}")
            a["as"] = c[2].text_input("As", value=a["as"], key=f"{key}_ga_{i}")
            if c[3].button("", icon=":material/close:", key=f"{key}_gx_{i}"):
                p["aggs"].pop(i)
                st.rerun()
        if st.button("Add aggregation", icon=":material/add:", key=f"{key}_gadd"):
            p.setdefault("aggs", []).append(
                {"column": columns[0] if columns else "", "func": "sum",
                 "as": "value"})
            st.rerun()

    elif t.kind == "sort":
        c = st.columns([3, 1.4], vertical_alignment="bottom")
        p["columns"] = c[0].multiselect("Sort by", columns,
                                        default=p.get("columns", []), key=f"{key}_sc")
        p["ascending"] = c[1].selectbox("Order", [True, False],
                                        index=0 if p.get("ascending", True) else 1,
                                        format_func=lambda v: "Ascending" if v else "Descending",
                                        key=f"{key}_sa")

    elif t.kind == "limit":
        p["n"] = int(st.number_input("Keep first N rows", 1, 1_000_000,
                                     int(p.get("n", 100)), key=f"{key}_ln"))

    elif t.kind == "rename":
        m = st.text_area("Old = New (one per line)",
                         value="\n".join(f"{k} = {v}" for k, v
                                         in p.get("mapping", {}).items()),
                         key=f"{key}_rm", height=90)
        p["mapping"] = dict(
            (a.strip(), b.strip())
            for a, _, b in (line.partition("=") for line in m.splitlines())
            if a.strip() and b.strip())


def _dataset_card(store, ds: Dataset, index: int, draft: Dashboard) -> None:
    key = f"ds{index}"
    conn = _connection_for(store, ds)
    columns = dataset_columns(ds, conn)

    with st.expander(f"**{ds.name}** · {ds.env or 'no environment'}", expanded=True):
        head = st.columns([2, 2, 1.8, 1.6, 0.7], vertical_alignment="bottom")
        ds.name = head[0].text_input("Name", value=ds.name, key=f"{key}_n")
        envs = sorted(store.list_environments())
        ds.env = head[1].selectbox("Environment", envs or [ds.env],
                                   index=envs.index(ds.env) if ds.env in envs else 0,
                                   key=f"{key}_e")
        modes = ["inherit", "realtime", "custom"]
        ds.time_mode = head[2].selectbox(
            "Period", modes, index=modes.index(ds.time_mode), key=f"{key}_tm",
            help="inherit = follow the dashboard's period control")
        ds.max_rows = int(head[3].number_input("Max rows", 1, 1_000_000,
                                               ds.max_rows, step=100,
                                               key=f"{key}_mr"))
        if head[4].button("", icon=":material/delete:", key=f"{key}_del"):
            draft.datasets.pop(index)
            st.rerun()

        if ds.time_mode == "custom":
            labels = [PRESET_LABELS[p] for p in PRESETS]
            current = ((ds.time_context or {}).get("range") or {}).get("name", "last_30d")
            chosen = st.selectbox("Its own period", labels,
                                  index=list(PRESETS).index(current)
                                  if current in PRESETS else 3, key=f"{key}_tc")
            preset = PRESETS[labels.index(chosen)]
            ds.time_context = {"mode": "historical",
                               "range": {"kind": "preset", "name": preset}}

        ds.mode = st.radio("Query", ["guided", "raw"], horizontal=True,
                           index=0 if ds.mode == "guided" else 1, key=f"{key}_m")

        if ds.mode == "guided":
            tables = sorted(getattr(conn, "schema", {}) or {})
            ds.table = st.selectbox("Table", tables or [ds.table],
                                    index=tables.index(ds.table)
                                    if ds.table in tables else 0, key=f"{key}_t")
            _filters_form(ds, dataset_columns(ds, conn), key)
        else:
            ds.raw_qsql = st.text_area("q", value=ds.raw_qsql or "", height=160,
                                       help=RAW_HELP, key=f"{key}_q")

        st.markdown("**Transforms**")
        for i, t in enumerate(list(ds.transforms)):
            with st.container(border=True):
                c = st.columns([2, 5, 0.6, 0.6, 0.6], vertical_alignment="bottom")
                t.kind = c[0].selectbox("Kind", TRANSFORM_KINDS,
                                        index=TRANSFORM_KINDS.index(t.kind),
                                        key=f"{key}_tk_{i}")
                if c[2].button("", icon=":material/arrow_upward:",
                               key=f"{key}_tu_{i}", disabled=i == 0):
                    ds.transforms[i - 1], ds.transforms[i] = \
                        ds.transforms[i], ds.transforms[i - 1]
                    st.rerun()
                if c[3].button("", icon=":material/arrow_downward:",
                               key=f"{key}_td_{i}",
                               disabled=i == len(ds.transforms) - 1):
                    ds.transforms[i + 1], ds.transforms[i] = \
                        ds.transforms[i], ds.transforms[i + 1]
                    st.rerun()
                if c[4].button("", icon=":material/close:", key=f"{key}_tx_{i}"):
                    ds.transforms.pop(i)
                    st.rerun()
                _transform_form(t, dataset_columns(ds, conn), f"{key}_t{i}")

        if st.button("Add transform", icon=":material/add:", key=f"{key}_taddb"):
            ds.transforms.append(Transform(kind="derive",
                                           params={"column": "", "kind": "arithmetic",
                                                   "expr": ""}))
            st.rerun()


def _render_data(store, mgr, draft: Dashboard) -> None:
    if not store.list_environments():
        st.warning("No connections yet — add one in Admin first.",
                   icon=":material/warning:")

    for i, ds in enumerate(list(draft.datasets)):
        _dataset_card(store, ds, i, draft)

    if st.button("Add dataset", icon=":material/add:", type="primary"):
        envs = sorted(store.list_environments())
        draft.datasets.append(Dataset(
            name=unique_name("dataset", [d.name for d in draft.datasets]),
            env=envs[0] if envs else ""))
        st.rerun()

    if draft.datasets and st.button("Preview datasets", icon=":material/play_arrow:"):
        results = run_datasets(draft, store, mgr, date.today())
        for name, res in results.items():
            with st.container(border=True):
                st.markdown(f"**{name}**")
                st.code(res.qsql or "(no query)", language="q")
                if res.error:
                    st.error(res.error, icon=":material/error:")
                else:
                    st.caption(f"{res.row_count} row(s)"
                               + (" — capped" if res.truncated else ""))
                    st.dataframe(res.df, use_container_width=True, height=220)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_ui_dashboard_editor.py -v`
Expected: PASS (7 tests). The `render` attribute test fails until Task 17 adds
`render` — that is expected; run the other six now with
`python -m pytest tests/test_ui_dashboard_editor.py -v -k "not imports"`.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/ui/dashboard_editor.py tests/test_ui_dashboard_editor.py
git commit -m "feat: dashboard dataset editor"
```

---

## Task 17: Editor — layout, validation, save

**Files:**
- Modify: `kdbmonitor/ui/dashboard_editor.py` (append)
- Test: `tests/test_ui_dashboard_editor.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_dashboard_editor.py`:

```python
from kdbmonitor.core.dashboard_models import Dashboard, Row, Widget
from kdbmonitor.core.models import Connection
from kdbmonitor.core.storage import Storage


def _store(tmp_path) -> Storage:
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="rdb", host="h", port=1,
                                kind="realtime", env="orders",
                                schema={"target": ["sym", "size"]}))
    return s


def test_a_valid_dashboard_has_no_complaints(tmp_path):
    d = Dashboard(id=1, name="D",
                  datasets=[Dataset(name="orders", env="orders", table="target")],
                  rows=[Row(widgets=[Widget(type="kpi", dataset="orders",
                                            spec={"column": "size", "agg": "sum"})])])
    assert ed.validate(d, _store(tmp_path)) == []


def test_widget_pointing_at_a_missing_dataset_is_reported(tmp_path):
    d = Dashboard(id=1, name="D", rows=[
        Row(widgets=[Widget(type="kpi", dataset="ghost")])])
    assert any("ghost" in m for m in ed.validate(d, _store(tmp_path)))


def test_historical_raw_dataset_without_a_date_is_reported(tmp_path):
    d = Dashboard(id=1, name="D",
                  time_context={"mode": "historical",
                                "range": {"kind": "preset", "name": "last_30d"}},
                  datasets=[Dataset(name="o", env="orders", mode="raw",
                                    raw_qsql="select from target")])
    assert any("date" in m for m in ed.validate(d, _store(tmp_path)))


def test_historical_guided_dataset_needs_no_date_of_its_own(tmp_path):
    d = Dashboard(id=1, name="D",
                  time_context={"mode": "historical",
                                "range": {"kind": "preset", "name": "last_30d"}},
                  datasets=[Dataset(name="o", env="orders", table="target")])
    assert ed.validate(d, _store(tmp_path)) == []


def test_environment_without_a_historical_side_is_reported(tmp_path):
    d = Dashboard(id=1, name="D",
                  time_context={"mode": "historical",
                                "range": {"kind": "preset", "name": "last_30d"}},
                  datasets=[Dataset(name="o", env="orders", table="target")])
    messages = ed.validate(d, _store(tmp_path))
    assert messages == []          # guided is fine...

    d.datasets[0].env = "nowhere"
    assert any("nowhere" in m for m in ed.validate(d, _store(tmp_path)))


def test_duplicate_dataset_names_are_reported(tmp_path):
    d = Dashboard(id=1, name="D", datasets=[
        Dataset(name="o", env="orders", table="target"),
        Dataset(name="o", env="orders", table="target")])
    assert any("duplicate" in m.lower() for m in ed.validate(d, _store(tmp_path)))


def test_forward_dataset_reference_is_reported(tmp_path):
    d = Dashboard(id=1, name="D", datasets=[
        Dataset(name="second", env="orders", mode="raw",
                raw_qsql="select from t where id in {{first.id}}"),
        Dataset(name="first", env="orders", table="target")])
    assert any("first" in m for m in ed.validate(d, _store(tmp_path)))


def test_a_row_may_not_hold_more_than_four_widgets(tmp_path):
    d = Dashboard(id=1, name="D",
                  datasets=[Dataset(name="o", env="orders", table="target")],
                  rows=[Row(widgets=[Widget(type="kpi", dataset="o")] * 5)])
    assert any("4 widgets" in m for m in ed.validate(d, _store(tmp_path)))


def test_widget_spec_fields_are_declared_for_every_type():
    from kdbmonitor.core.plotmodel import _RESOLVERS
    assert set(ed.WIDGET_TYPES) == set(_RESOLVERS) | {"text"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_ui_dashboard_editor.py -k validate -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'validate'`

- [ ] **Step 3: Implement validation, the layout editor and the entry point**

Append to `kdbmonitor/ui/dashboard_editor.py`:

```python
import re

from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.timectx import has_date_constraint, resolve
from kdbmonitor.ui.dashboards import row_height_px

WIDGET_TYPES = ["kpi", "table", "text", "bar", "line", "scatter", "hist",
                "box", "heatmap", "pie"]

_REF = re.compile(r"\{\{(\w+)\.(\w+)\}\}")


# --- validation ------------------------------------------------------------

def validate(draft: Dashboard, store) -> list[str]:
    """Everything wrong with this dashboard, in plain English. Empty when fine."""
    problems: list[str] = []
    envs = store.list_environments()
    dashboard_time = resolve(draft.time_context, date.today())

    seen: list[str] = []
    for ds in draft.datasets:
        if ds.name in seen:
            problems.append(f"Duplicate dataset name '{ds.name}'.")
        seen.append(ds.name)

        rt = dashboard_time if ds.time_mode == "inherit" else resolve(
            {"mode": "realtime"} if ds.time_mode == "realtime"
            else (ds.time_context or {"mode": "realtime"}), date.today())

        if ds.env not in envs:
            problems.append(f"Dataset '{ds.name}' uses unknown environment "
                            f"'{ds.env}'.")
        elif rt.mode == "historical" and envs[ds.env]["historical"] is None:
            problems.append(f"Dataset '{ds.name}': environment '{ds.env}' has no "
                            f"historical server — add one in Admin.")

        if rt.mode == "historical" and ds.mode == "raw" \
                and not has_date_constraint(ds.raw_qsql or ""):
            problems.append(
                f"Dataset '{ds.name}' is historical but its q never constrains "
                f"'date'. Add date within ({{{{date_from}}}};{{{{date_to}}}}).")

        if ds.mode == "guided" and not ds.table:
            problems.append(f"Dataset '{ds.name}' has no table selected.")

        for ref, _ in _REF.findall(ds.raw_qsql or ""):
            if ref not in seen[:-1]:
                problems.append(f"Dataset '{ds.name}' references '{ref}', which is "
                                f"not defined above it.")

    names = {ds.name for ds in draft.datasets}
    for i, row in enumerate(draft.rows, start=1):
        if len(row.widgets) > 4:
            problems.append(f"Row {i} has {len(row.widgets)} widgets — a row holds "
                            f"at most 4 widgets.")
        for w in row.widgets:
            if w.dataset not in names:
                problems.append(f"Row {i}: widget '{w.title or w.type}' uses unknown "
                                f"dataset '{w.dataset}'.")
            if w.width <= 0:
                problems.append(f"Row {i}: widget '{w.title or w.type}' has a "
                                f"non-positive width.")
    return problems


# --- widget spec forms -----------------------------------------------------

def _pick(container, label: str, columns: list[str], current: str, key: str) -> str:
    options = columns or ([current] if current else [""])
    index = options.index(current) if current in options else 0
    return container.selectbox(label, options, index=index, key=key)


def _widget_form(w: Widget, columns: list[str], key: str) -> None:
    s = w.spec
    if w.type == "kpi":
        c = st.columns([2, 1.4, 1.2, 1.2], vertical_alignment="bottom")
        s["column"] = _pick(c[0], "Column", columns, s.get("column", ""), f"{key}_c")
        s["agg"] = c[1].selectbox("Aggregate", AGG_FUNCS,
                                  index=AGG_FUNCS.index(s.get("agg", "sum")),
                                  key=f"{key}_a")
        s["fmt"] = c[2].text_input("Format", value=s.get("fmt", ",.0f"),
                                   help="Python format spec, e.g. ,.0f or .1f",
                                   key=f"{key}_f")
        s["suffix"] = c[3].text_input("Suffix", value=s.get("suffix", ""),
                                      key=f"{key}_sfx")
        red = st.checkbox("Turn red when above zero", key=f"{key}_thr",
                          value=bool(s.get("thresholds")))
        s["thresholds"] = ([{"op": ">", "value": 0, "color": "critical"}]
                           if red else [])

    elif w.type == "table":
        s["columns"] = st.multiselect("Columns (empty = all)", columns,
                                      default=[c for c in s.get("columns", [])
                                               if c in columns], key=f"{key}_cols")
        hl = st.selectbox("Highlight when above zero", ["(none)"] + columns,
                          index=0, key=f"{key}_hl")
        s["highlight"] = ([] if hl == "(none)"
                          else [{"column": hl, "op": ">", "value": 0,
                                 "color": "critical"}])

    elif w.type == "text":
        s["markdown"] = st.text_area(
            "Markdown", value=s.get("markdown", ""), height=120, key=f"{key}_md",
            help="Use {{dataset.agg.column}} to inline a number, e.g. "
                 "{{by_market.sum.n_orders}}")

    elif w.type in ("bar", "line", "scatter"):
        c = st.columns([2, 2, 1.6, 1.4], vertical_alignment="bottom")
        s["x"] = _pick(c[0], "X", columns, s.get("x", ""), f"{key}_x")
        s["y"] = _pick(c[1], "Y", columns, s.get("y", "") if isinstance(s.get("y"), str)
                       else "", f"{key}_y")
        hue = _pick(c[2], "Split by", ["(none)"] + columns,
                    s.get("hue") or "(none)", f"{key}_h")
        s["hue"] = None if hue == "(none)" else hue
        if w.type == "bar":
            s["orientation"] = c[3].selectbox("Direction", ["v", "h"],
                                              index=0 if s.get("orientation", "v") == "v" else 1,
                                              format_func=lambda v: "Vertical" if v == "v" else "Horizontal",
                                              key=f"{key}_o")
            s["sort"] = st.selectbox("Sort", [None, "asc", "desc"],
                                     index=[None, "asc", "desc"].index(s.get("sort")),
                                     format_func=lambda v: v or "(source order)",
                                     key=f"{key}_s")
        if w.type == "scatter":
            s["regression"] = c[3].checkbox("Trend line",
                                            value=bool(s.get("regression")),
                                            key=f"{key}_r")

    elif w.type == "hist":
        c = st.columns([2, 1.4], vertical_alignment="bottom")
        s["x"] = _pick(c[0], "Value", columns, s.get("x", ""), f"{key}_x")
        s["bins"] = int(c[1].number_input("Bins", 2, 200, int(s.get("bins", 20)),
                                          key=f"{key}_b"))

    elif w.type == "box":
        c = st.columns(2, vertical_alignment="bottom")
        s["x"] = _pick(c[0], "Group by", columns, s.get("x", ""), f"{key}_x")
        s["y"] = _pick(c[1], "Value", columns, s.get("y", ""), f"{key}_y")

    elif w.type == "heatmap":
        c = st.columns([2, 2, 2, 1.4], vertical_alignment="bottom")
        s["rows"] = _pick(c[0], "Rows", columns, s.get("rows", ""), f"{key}_r")
        s["cols"] = _pick(c[1], "Columns", columns, s.get("cols", ""), f"{key}_c")
        s["value"] = _pick(c[2], "Value", columns, s.get("value", ""), f"{key}_v")
        s["agg"] = c[3].selectbox("Aggregate", ["sum", "mean", "count"],
                                  key=f"{key}_a")

    elif w.type == "pie":
        c = st.columns([2, 2, 1.2], vertical_alignment="bottom")
        s["by"] = _pick(c[0], "Slice by", columns, s.get("by", ""), f"{key}_b")
        s["value"] = _pick(c[1], "Value", columns, s.get("value", ""), f"{key}_v")
        s["donut"] = c[2].checkbox("Donut", value=bool(s.get("donut")),
                                   key=f"{key}_d")


# --- layout section --------------------------------------------------------

def _render_layout(store, draft: Dashboard) -> None:
    if not draft.datasets:
        st.warning("Add a dataset first — widgets read from datasets.",
                   icon=":material/warning:")
        return

    names = [ds.name for ds in draft.datasets]
    by_name = {ds.name: ds for ds in draft.datasets}

    for r_i, row in enumerate(list(draft.rows)):
        with st.container(border=True):
            head = st.columns([3, 1.6, 0.6, 0.6, 0.6], vertical_alignment="bottom")
            head[0].markdown(f"**Row {r_i + 1}** · {len(row.widgets)} widget(s)")
            row.height_in = float(head[1].number_input(
                "Height (in)", 0.4, 9.0, float(row.height_in), step=0.1,
                key=f"r{r_i}_h", help="Printed height on the A4 page."))
            if head[2].button("", icon=":material/arrow_upward:", key=f"r{r_i}_u",
                              disabled=r_i == 0):
                draft.rows[r_i - 1], draft.rows[r_i] = draft.rows[r_i], draft.rows[r_i - 1]
                st.rerun()
            if head[3].button("", icon=":material/arrow_downward:", key=f"r{r_i}_d",
                              disabled=r_i == len(draft.rows) - 1):
                draft.rows[r_i + 1], draft.rows[r_i] = draft.rows[r_i], draft.rows[r_i + 1]
                st.rerun()
            if head[4].button("", icon=":material/delete:", key=f"r{r_i}_x"):
                draft.rows.pop(r_i)
                st.rerun()

            for w_i, w in enumerate(list(row.widgets)):
                key = f"r{r_i}w{w_i}"
                with st.container(border=True):
                    c = st.columns([1.6, 1.8, 2.4, 1.1, 0.6, 0.6],
                                   vertical_alignment="bottom")
                    w.type = c[0].selectbox("Type", WIDGET_TYPES,
                                            index=WIDGET_TYPES.index(w.type),
                                            key=f"{key}_t")
                    w.dataset = c[1].selectbox("Dataset", names,
                                               index=names.index(w.dataset)
                                               if w.dataset in names else 0,
                                               key=f"{key}_ds")
                    w.title = c[2].text_input("Title", value=w.title, key=f"{key}_ti")
                    w.width = float(c[3].number_input("Width", 0.2, 8.0,
                                                      float(w.width), step=0.1,
                                                      key=f"{key}_w"))
                    if c[4].button("", icon=":material/arrow_back:", key=f"{key}_l",
                                   disabled=w_i == 0):
                        row.widgets[w_i - 1], row.widgets[w_i] = \
                            row.widgets[w_i], row.widgets[w_i - 1]
                        st.rerun()
                    if c[5].button("", icon=":material/close:", key=f"{key}_x"):
                        row.widgets.pop(w_i)
                        st.rerun()

                    ds = by_name.get(w.dataset)
                    columns = (dataset_columns(ds, _connection_for(store, ds))
                               if ds else [])
                    _widget_form(w, columns, key)

            if st.button("Add widget", icon=":material/add:", key=f"r{r_i}_add",
                         disabled=len(row.widgets) >= 4):
                row.widgets.append(Widget(type="kpi", dataset=names[0], title=""))
                st.rerun()

    if st.button("Add row", icon=":material/add:", type="primary"):
        draft.rows.append(Row(widgets=[], height_in=2.5))
        st.rerun()


def _render_preview(store, mgr, draft: Dashboard) -> None:
    if not draft.datasets:
        return
    if not st.button("Refresh preview", icon=":material/play_arrow:"):
        st.caption("Run the datasets to see the real page.")
        return
    results = run_datasets(draft, store, mgr, date.today())
    for row in draft.rows:
        if not row.widgets:
            continue
        cols = st.columns([max(w.width, 0.01) for w in row.widgets],
                          vertical_alignment="top")
        for i, w in enumerate(row.widgets):
            with cols[i]:
                from kdbmonitor.ui.dashboards import _render_widget
                _render_widget(build_plot_model(w, results),
                               row_height_px(row.height_in))


# --- entry point -----------------------------------------------------------

def render(store, mgr) -> None:
    draft = _draft(store)

    head = st.columns([3, 3, 1.2, 1.2, 1.2], vertical_alignment="bottom")
    draft.name = head[0].text_input("Dashboard name", value=draft.name)
    draft.description = head[1].text_input("Description", value=draft.description)

    if head[2].button("Save", icon=":material/save:", type="primary",
                      use_container_width=True):
        problems = validate(draft, store)
        if problems:
            for p in problems:
                st.error(p, icon=":material/error:")
        else:
            store.update_dashboard(draft) if draft.id else store.add_dashboard(draft)
            st.toast(f"Saved '{draft.name}'", icon=":material/check:")
            _close()
            st.rerun()

    if head[3].button("Open", icon=":material/open_in_new:",
                      use_container_width=True, disabled=draft.id is None):
        _close()
        st.query_params["dash"] = str(draft.id)
        st.rerun()

    if head[4].button("Close", icon=":material/close:", use_container_width=True):
        _close()
        st.rerun()

    section = st.segmented_control("Section", ["Data", "Layout", "Preview"],
                                   default="Data", key="dash_edit_section")
    st.divider()
    if section == "Data":
        _render_data(store, mgr, draft)
    elif section == "Layout":
        _render_layout(store, draft)
    else:
        _render_preview(store, mgr, draft)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_ui_dashboard_editor.py -v`
Expected: PASS (16 tests)

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Drive it in the app**

Run: `python -m streamlit run app.py`

In Admin, load the demo servers. Then on Dashboards: New dashboard → Data → Add
dataset (env `orders`, table `target`) → Preview datasets shows rows. Layout →
Add row → Add widget (kpi on `qty`) → Save → Open. Expected: the KPI renders and
refreshes on the chosen interval; Generate PDF then Download produces a one-page
A4 file.

- [ ] **Step 6: Commit**

```bash
git add kdbmonitor/ui/dashboard_editor.py tests/test_ui_dashboard_editor.py
git commit -m "feat: dashboard layout editor with save-time validation"
```

---

## Task 18: Export and import dashboards

Additive, following the `export_connections_json` precedent: the existing
`import_bundle_json` signature is left alone so no current caller breaks.

**Files:**
- Modify: `kdbmonitor/core/portability.py` (append)
- Test: `tests/test_portability.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_portability.py`:

```python
import pytest

from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Row, Widget
from kdbmonitor.core.portability import (
    export_dashboards_json, import_dashboards_json,
)


def _dash() -> Dashboard:
    return Dashboard(
        id=9, name="Short sell", description="by market",
        time_context={"mode": "historical",
                      "range": {"kind": "preset", "name": "last_30d"}},
        datasets=[Dataset(name="orders", env="orders", table="target")],
        rows=[Row(height_in=0.9, widgets=[
            Widget(type="kpi", dataset="orders", title="Orders",
                   spec={"column": "size", "agg": "sum"})])])


def test_dashboards_survive_an_export_import_roundtrip():
    back = import_dashboards_json(export_dashboards_json([_dash()]))
    assert len(back) == 1
    assert back[0].name == "Short sell"
    assert back[0].datasets[0].table == "target"
    assert back[0].rows[0].widgets[0].spec["column"] == "size"


def test_export_drops_ids_so_import_creates_fresh_rows():
    assert import_dashboards_json(export_dashboards_json([_dash()]))[0].id is None


def test_time_context_is_preserved():
    back = import_dashboards_json(export_dashboards_json([_dash()]))
    assert back[0].time_context["range"]["name"] == "last_30d"


def test_importing_a_non_export_file_is_a_clear_error():
    with pytest.raises(ValueError, match="Not a KdbMonitor export"):
        import_dashboards_json('{"kind": "something-else"}')


def test_importing_broken_json_is_a_clear_error():
    with pytest.raises(ValueError, match="Not valid JSON"):
        import_dashboards_json("{oh no")


def test_importing_a_bundle_without_dashboards_yields_none():
    assert import_dashboards_json(
        '{"kind": "kdbmonitor-export", "version": 2, "alerts": []}') == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_portability.py -k dashboard -v`
Expected: FAIL — `ImportError: cannot import name 'export_dashboards_json'`

- [ ] **Step 3: Implement it**

Append to `kdbmonitor/core/portability.py`:

```python
from kdbmonitor.core.dashboard_models import (
    Dashboard, dashboard_from_dict, dashboard_to_dict,
)


def export_dashboards_json(dashboards: Iterable[Dashboard],
                           exported_at: Optional[str] = None) -> str:
    """A bundle carrying only dashboards.

    Uses the same envelope as the alert bundle so one importer can recognise
    every KdbMonitor file. IDs are dropped, so importing always creates fresh
    rows. Dashboards reference *environments* by name, not connection ids, so a
    bundle lands cleanly on any machine whose Admin has the same env names.
    """
    payload = {
        "kind": EXPORT_KIND,
        "version": EXPORT_VERSION,
        "exported_at": exported_at,
        "connections": [],
        "alerts": [],
        "dashboards": [{**dashboard_to_dict(d), "id": None} for d in dashboards],
    }
    return json.dumps(payload, indent=2)


def import_dashboards_json(raw: str) -> list[Dashboard]:
    """Parse the dashboards out of an export document, each with id=None."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Not valid JSON: {exc}")

    if not isinstance(payload, dict) or payload.get("kind") not in (EXPORT_KIND,
                                                                    LEGACY_KIND):
        raise ValueError("Not a KdbMonitor export file "
                         f"(expected kind '{EXPORT_KIND}').")

    raw_dashboards = payload.get("dashboards", [])
    if not isinstance(raw_dashboards, list):
        raise ValueError("Export file 'dashboards' must be a list.")

    out: list[Dashboard] = []
    for i, d in enumerate(raw_dashboards):
        try:
            dash = dashboard_from_dict(d)
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Dashboard #{i + 1} is malformed: {exc}")
        dash.id = None
        out.append(dash)
    return out
```

- [ ] **Step 4: Add the buttons to the gallery**

In `kdbmonitor/ui/dashboards.py`, inside `_render_gallery`, after the dashboard
loop, add:

```python
    st.divider()
    io_cols = st.columns([1.6, 3], vertical_alignment="center")
    io_cols[0].download_button(
        "Export all", data=export_dashboards_json(dashboards_),
        file_name="kdbmonitor_dashboards.json", mime="application/json",
        icon=":material/download:", use_container_width=True)

    uploaded = io_cols[1].file_uploader("Import dashboards", type=["json"],
                                        label_visibility="collapsed")
    if uploaded is not None:
        try:
            incoming = import_dashboards_json(uploaded.getvalue().decode("utf-8"))
        except ValueError as exc:
            st.error(str(exc), icon=":material/error:")
        else:
            existing = {d.name for d in dashboards_}
            for d in incoming:
                if d.name in existing:
                    d.name = f"{d.name} (imported)"
                store.add_dashboard(d)
            st.toast(f"Imported {len(incoming)} dashboard(s)",
                     icon=":material/check:")
            st.rerun()
```

And add the import at the top of the file:

```python
from kdbmonitor.core.portability import (
    export_dashboards_json, import_dashboards_json,
)
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add kdbmonitor/core/portability.py kdbmonitor/ui/dashboards.py tests/test_portability.py
git commit -m "feat: export and import dashboards"
```

---

## Task 19: Acceptance — rebuild the short-sell report as a dashboard

The feature is done when `short_sell_report.py` can be reproduced in the app with
no Python, and the numbers match.

**Files:**
- Create: `tests/test_short_sell_acceptance.py`
- Create: `docs/examples/short_sell_dashboard.json`
- Modify: `README.md`

- [ ] **Step 1: Write the acceptance test**

Create `tests/test_short_sell_acceptance.py`:

```python
"""The short-sell report, rebuilt as a dashboard definition.

Asserts the dashboard's datasets and widgets produce the same numbers as
short_sell_report.py's summarise_by_market, and that the page renders to a PDF.
"""
from datetime import date, datetime

import pandas as pd
import pytest

from kdbmonitor.core.client import ConnectionManager, FakeClient
from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Row, Transform, Widget,
)
from kdbmonitor.core.dashpdf import dashboard_to_pdf_bytes
from kdbmonitor.core.dataset import run_datasets
from kdbmonitor.core.models import Connection, Filter
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.timectx import ResolvedTime

QUERY = "select from target where side=`sellshort"

RAW = pd.DataFrame([
    {"id_target": 1, "sym": "5.HK",    "size": 100, "executed": 50,  "nReject": 0},
    {"id_target": 2, "sym": "700.HK",  "size": 200, "executed": 200, "nReject": 1},
    {"id_target": 3, "sym": "7203.JP", "size": 400, "executed": 100, "nReject": 2},
    {"id_target": 4, "sym": "5930.KS", "size": 100, "executed": 0,   "nReject": 0},
])

MARKETS = {".HK": "Hong Kong", ".JP": "Japan", ".KS": "Korea",
           ".MK": "Malaysia", ".TB": "Thailand"}


@pytest.fixture()
def store(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="order-rdb", host="rdb", port=1,
                                kind="realtime", env="orders",
                                schema={"target": list(RAW.columns)}))
    return s


@pytest.fixture()
def mgr():
    client = FakeClient({QUERY: RAW})
    return ConnectionManager(client_factory=lambda host, port: client)


def short_sell_dashboard() -> Dashboard:
    """The dashboard a user would build in the editor for this report."""
    return Dashboard(
        id=1, name="Short sell", description="By market", refresh_secs=15,
        datasets=[Dataset(
            name="by_market", env="orders", table="target",
            filters=[Filter(column="side", op="=", value="sellshort",
                            value_type="symbol")],
            transforms=[
                Transform(kind="derive", params={
                    "column": "market", "kind": "suffix_map", "source": "sym",
                    "mapping": MARKETS, "default": "Unknown"}),
                Transform(kind="groupby", params={
                    "keys": ["market"], "aggs": [
                        {"column": "id_target", "func": "nunique", "as": "n_orders"},
                        {"column": "size", "func": "sum", "as": "order_qty"},
                        {"column": "executed", "func": "sum", "as": "executed_qty"},
                        {"column": "nReject", "func": "sum", "as": "n_rejections"}]}),
                Transform(kind="derive", params={
                    "column": "completion_pct", "kind": "arithmetic",
                    "expr": "100 * executed_qty / order_qty"}),
                Transform(kind="sort", params={"columns": ["market"],
                                               "ascending": True}),
            ])],
        rows=[
            Row(height_in=0.9, widgets=[
                Widget(type="kpi", dataset="by_market", title="Short-sell orders",
                       spec={"column": "n_orders", "agg": "sum", "fmt": ",.0f"}),
                Widget(type="kpi", dataset="by_market", title="Overall completion",
                       spec={"column": "completion_pct", "agg": "mean",
                             "fmt": ".1f", "suffix": "%"}),
                Widget(type="kpi", dataset="by_market", title="Rejections",
                       spec={"column": "n_rejections", "agg": "sum", "fmt": ",.0f",
                             "thresholds": [{"op": ">", "value": 0,
                                             "color": "critical"}]})]),
            Row(height_in=2.4, widgets=[
                Widget(type="table", dataset="by_market", title="By market",
                       spec={"columns": ["market", "n_orders", "order_qty",
                                         "executed_qty", "completion_pct",
                                         "n_rejections"],
                             "formats": {"completion_pct": ".1f"},
                             "highlight": [{"column": "n_rejections", "op": ">",
                                            "value": 0, "color": "critical"}]})]),
            Row(height_in=3.0, widgets=[
                Widget(type="bar", dataset="by_market", title="Completion by market",
                       spec={"x": "market", "y": "completion_pct",
                             "orientation": "h", "sort": "asc"}),
                Widget(type="bar", dataset="by_market", title="Rejections by market",
                       spec={"x": "market", "y": "n_rejections",
                             "orientation": "h", "sort": "asc"})]),
        ])


def test_the_dataset_matches_summarise_by_market(store, mgr):
    results = run_datasets(short_sell_dashboard(), store, mgr, date.today())
    df = results["by_market"].df
    assert results["by_market"].error is None

    by_market = df.set_index("market")
    assert by_market.loc["Hong Kong", "n_orders"] == 2
    assert by_market.loc["Hong Kong", "order_qty"] == 300
    assert by_market.loc["Hong Kong", "executed_qty"] == 250
    assert round(by_market.loc["Hong Kong", "completion_pct"], 1) == 83.3
    assert by_market.loc["Japan", "n_rejections"] == 2
    assert by_market.loc["Korea", "completion_pct"] == 0.0


def test_the_kpis_read_correctly(store, mgr):
    dash = short_sell_dashboard()
    results = run_datasets(dash, store, mgr, date.today())
    kpis = [build_plot_model(w, results) for w in dash.rows[0].widgets]
    assert kpis[0].value == "4"           # 4 short-sell orders
    assert kpis[2].value == "3"           # 3 rejections
    assert kpis[2].value_color != kpis[0].value_color   # rejections flagged red


def test_the_table_flags_markets_with_rejections(store, mgr):
    dash = short_sell_dashboard()
    results = run_datasets(dash, store, mgr, date.today())
    pm = build_plot_model(dash.rows[1].widgets[0], results)
    flagged = {pm.rows[r][0] for (r, c) in pm.cell_colors}
    assert flagged == {"Hong Kong", "Japan"}      # Korea has none


def test_the_whole_page_renders_to_a_pdf(store, mgr):
    dash = short_sell_dashboard()
    results = run_datasets(dash, store, mgr, date.today())
    out = dashboard_to_pdf_bytes(dash, results,
                                 ResolvedTime("realtime", None, None),
                                 datetime(2026, 7, 26, 9, 15))
    assert out.startswith(b"%PDF")
    assert len(out) > 5000
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_short_sell_acceptance.py -v`
Expected: PASS (4 tests). If completion percentages are off, check that the
`derive` of `completion_pct` runs *after* the `groupby` — order matters.

- [ ] **Step 3: Ship it as an importable example**

```bash
python -c "
import json, sys
sys.path.insert(0, 'tests')
from test_short_sell_acceptance import short_sell_dashboard
from kdbmonitor.core.portability import export_dashboards_json
import os
os.makedirs('docs/examples', exist_ok=True)
open('docs/examples/short_sell_dashboard.json','w').write(
    export_dashboards_json([short_sell_dashboard()]))
print('wrote docs/examples/short_sell_dashboard.json')
"
```

Expected: a JSON bundle importable from the Dashboards gallery on any machine
with an `orders` environment.

- [ ] **Step 4: Document the feature**

Add a `## Dashboards` section to `README.md`, after the alerts sections:

````markdown
## Dashboards

Saved pages built from KDB queries: KPIs, tables and charts that refresh while
the page is open and export to a PDF of exactly what is on screen.

**Datasets** are query + shaping. Guided mode builds the where clause from the
table's real columns; raw mode takes q directly. Either way an ordered list of
transforms (derive, filter, group by, sort, limit, rename) shapes the result — no
Python required.

**Environments** pair a real-time and a historical server under one name (set in
Admin). A dataset targets the environment, not a server; the dashboard's period
control decides which one is queried. In historical mode the date range is
injected as the first where-clause — `date within (2026.06.01;2026.06.30)` — so
kdb+ prunes partitions. Raw historical queries must constrain `date` themselves
via `{{date_from}}` / `{{date_to}}` / `{{date_list}}`; saving is refused
otherwise, because an unconstrained query on a partitioned HDB will not error, it
will just read years of data and hang the page.

**Layout** is rows of 1–4 widgets: kpi, table, text, bar, line, scatter, hist,
box, heatmap, pie. On screen they are interactive (Plotly — hover a line chart to
read every series at that x); in the PDF the same resolved plot model is drawn by
matplotlib/seaborn onto A4.

**Refresh** happens only while a dashboard is open, on its own interval. Nothing
runs in the background, so a dashboard you are not looking at costs nothing.

**PDF** renders from the frames already on screen — it never re-queries, so the
downloaded page shows the numbers you were looking at.

Try it: Admin → Load demo servers, then Dashboards → Import and pick
`docs/examples/short_sell_dashboard.json`.
````

- [ ] **Step 5: Full suite and a manual pass**

Run: `python -m pytest -q`
Expected: PASS — all suites, old and new.

Run: `python -m streamlit run app.py`
Walk through: Admin (environments show `orders` paired) → Dashboards → import the
example → Open → switch the period to Last 30 days (the demo HDB answers) →
Generate PDF → Download.

- [ ] **Step 6: Commit**

```bash
git add tests/test_short_sell_acceptance.py docs/examples/short_sell_dashboard.json README.md
git commit -m "test: short-sell report rebuilt as a dashboard; document dashboards"
```

---

## Done

The feature is complete when:

- `python -m pytest -q` passes, including `tests/test_short_sell_acceptance.py`
- Admin pairs real-time and historical servers into environments and flags
  half-configured ones
- A dashboard can be created, edited, duplicated, deleted, exported and imported
- Switching a dashboard between Real-time and a date range re-queries the other
  server with no edit to the dataset
- A raw historical query without a `date` constraint cannot be saved
- Charts are interactive on screen and the PDF matches them
- A broken dataset shows an error card on screen *and* in the PDF
