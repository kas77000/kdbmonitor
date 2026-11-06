"""The OMS dashboard listing live targets that have no market data.

Targets started today on the listed algos with no target_strike row, among
those activated in the last 30 seconds. It is a real-time question only, so the
dashboard offers no historical period and its query must reach the OMS RT
server, never OMS HT.
"""
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from kdbmonitor.core.client import ConnectionManager, FakeClient
from kdbmonitor.core.dataset import run_datasets
from kdbmonitor.core.models import Connection
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.portability import import_dashboards_json
from kdbmonitor.core.storage import Storage

BUNDLE = Path(__file__).resolve().parents[1] / "mon_dashboard.json"
NAME = "Target missing market data"
TODAY = date(2026, 11, 6)

ROWS = pd.DataFrame({
    "id_target": [101, 102], "trader": ["jdoe", "asmith"],
    "basket": ["B1", "B2"], "sym": ["VOD.L", "BARC.L"],
    "side": ["BUY", "SELL"], "size": [5000, 12000],
    "otype": ["LIMIT", "MARKET"], "limit_price": [72.5, float("nan")],
    "algo": ["VWAP", "TMX"], "ordstart": ["09:01:02.000", "09:05:00.000"],
})

BY_TRADER = pd.DataFrame({"trader": ["ALGO_EU_1", "ALGO_EU_2"],
                          "targets": [14, 3]})


@pytest.fixture()
def dash():
    dashes = import_dashboards_json(BUNDLE.read_text(encoding="utf-8"))
    return next(d for d in dashes if d.name == NAME)


@pytest.fixture()
def store(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="OMS RT", host="rt-host", port=1,
                                kind="realtime", env="OMS"))
    s.add_connection(Connection(id=None, name="OMS HT", host="ht-host", port=2,
                                kind="historical", env="OMS"))
    return s


@pytest.fixture()
def sent():
    return []


@pytest.fixture()
def mgr(dash, sent):
    responses = {dash.datasets[0].raw_qsql: ROWS,
                 dash.datasets[1].raw_qsql: BY_TRADER}

    class Watching(FakeClient):
        def __init__(self, host):
            super().__init__(responses)
            self.host = host

        def query(self, qsql):
            sent.append((self.host, qsql))
            return super().query(qsql)

    return ConnectionManager(client_factory=lambda host, port: Watching(host))


def test_the_bundle_holds_this_one_dashboard():
    names = [d["name"] for d in json.loads(
        BUNDLE.read_text(encoding="utf-8"))["dashboards"]]
    assert names == [NAME]


def test_it_is_real_time_only_on_the_oms_environment(dash):
    assert dash.periods == "realtime"
    assert [d.name for d in dash.datasets] == ["missing_md",
                                               "activated_by_trader"]
    for ds in dash.datasets:
        assert (ds.env, ds.time_mode, ds.mode) == ("OMS", "realtime", "raw")


def test_the_query_reaches_oms_rt_and_nothing_else(dash, store, mgr, sent):
    results = run_datasets(dash, store, mgr, TODAY)
    assert all(r.error is None for r in results.values())
    assert [host for host, _ in sent] == ["rt-host", "rt-host"]
    # Sent exactly as written: no date or reference substitution touched them.
    assert [q for _, q in sent] == [ds.raw_qsql for ds in dash.datasets]


def test_the_query_keeps_the_logic_it_was_written_with(dash):
    q = dash.datasets[0].raw_qsql
    for part in ("algo in (`VWAP`TMX`PART`LINE`IIS`SDMA`DLP)",
                 "t_start<.z.T", "except", "from target_strike",
                 "time>.z.T-00:00:30.000", "state=`activated",
                 "id_target in idTgts", "id_target in activeIds",
                 "ordstart:t_start"):
        assert part in q, part


def test_it_leaves_no_globals_on_the_live_server(dash):
    """Run every refresh: the intermediate tables are a lambda's locals, not
    top-level assignments that would sit in the OMS server's namespace."""
    for ds in dash.datasets:
        q = ds.raw_qsql
        assert q.startswith("{[] ") and q.endswith("}[]")
        assert "\n" not in q and "::" not in q


def test_the_table_shows_the_columns_the_query_selects(dash, store, mgr):
    results = run_datasets(dash, store, mgr, TODAY)
    table = next(w for row in dash.rows for w in row.widgets
                 if w.type == "table" and w.dataset == "missing_md")
    model = build_plot_model(table, results)
    assert model.columns == list(ROWS.columns)
    assert len(model.rows) == 2


def test_every_widget_renders(dash, store, mgr):
    results = run_datasets(dash, store, mgr, TODAY)
    for row in dash.rows:
        for w in row.widgets:
            model = build_plot_model(w, results)
            assert model.kind != "error" and not model.error, (w.title, model)


# --- activated targets per instance -------------------------------------------

def test_activated_means_the_current_state_not_any_state_today(dash):
    """A target has many target_state rows. One activated this morning and
    filled since must not be counted, so the state is the last one per target."""
    q = dash.datasets[1].raw_qsql
    assert "select last state by id_target from target_state" in q
    assert "where state=`activated" in q
    assert "from target_state where state=" not in q


def test_it_counts_targets_per_trader_column_busiest_first(dash):
    q = dash.datasets[1].raw_qsql
    assert "count distinct id_target by trader from target" in q
    assert "id_target in live" in q
    # unkeyed, so trader is a column the table can show
    assert "`targets xdesc 0!select" in q


def test_the_per_instance_table_shows_trader_and_count(dash, store, mgr):
    results = run_datasets(dash, store, mgr, TODAY)
    table = next(w for row in dash.rows for w in row.widgets
                 if w.type == "table" and w.dataset == "activated_by_trader")
    model = build_plot_model(table, results)
    assert model.columns == ["trader", "targets"]
    assert len(model.rows) == 2
