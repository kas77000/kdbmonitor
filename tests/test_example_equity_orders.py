"""The shipped example that reads one database to decide what to ask another.

A dashboard is allowed to span servers: one dataset asks EQUITY DATA which
symbols matched yesterday, and a second asks the live OMS which of those
currently carry an activated order. The second query is built from the first
one's answer, the way an alert chain builds a later step from an earlier one.

These tests hold that arrangement in place. It works today, but nothing was
asserting it — so a change to reference substitution or to how a dataset picks
its server could have broken cross-server dashboards silently.
"""
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

BUNDLE = Path(__file__).resolve().parents[1] / "docs" / "examples" \
    / "equity_active_orders_dashboard.json"

TODAY = date(2026, 7, 31)

EQUITY_Q = ('select sym from equity where date=.z.D-1, sym like "*.IB", '
            "ID_ISIN in `INE180A01020")
ORDERS_Q = ("{[s] t:select from target where sym in s; "
            "live:exec id_target from (0!select last state by id_target "
            "from target_state where id_target in t`id_target) "
            "where state=`activated; "
            "select from t where id_target in live}[`RELIANCE.IB`INFY.IB]")

EQUITY_ROWS = pd.DataFrame({"sym": ["RELIANCE.IB", "INFY.IB"]})
ORDER_ROWS = pd.DataFrame({"id_target": [11, 12],
                           "sym": ["RELIANCE.IB", "INFY.IB"],
                           "side": ["BUY", "SELL"],
                           "qty": [125000, 40000]})


@pytest.fixture()
def store(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    # Reference data: no live/historical pair, and none is coming.
    s.add_connection(Connection(id=None, name="equity", host="equity-host",
                                port=1, kind="marketdata", env="EQUITY DATA"))
    s.add_connection(Connection(id=None, name="oms", host="oms-host", port=2,
                                kind="realtime", env="OMS RT"))
    return s


class _Watching(FakeClient):
    """A client that remembers which host each query was sent to."""

    def __init__(self, host, responses, log):
        super().__init__(responses)
        self.host, self.log = host, log

    def query(self, qsql):
        self.log.append((self.host, qsql))
        return super().query(qsql)


@pytest.fixture()
def sent():
    return []


@pytest.fixture()
def mgr(sent):
    responses = {EQUITY_Q: EQUITY_ROWS, ORDERS_Q: ORDER_ROWS}
    return ConnectionManager(
        client_factory=lambda host, port: _Watching(host, responses, sent))


@pytest.fixture()
def dash():
    return import_dashboards_json(BUNDLE.read_text(encoding="utf-8"))[0]


def test_the_shipped_bundle_still_parses(dash):
    assert dash.name.startswith("Active orders")
    assert [d.name for d in dash.datasets] == ["equity_syms", "active_orders"]


def test_each_query_goes_to_its_own_database(dash, store, mgr, sent):
    """The point of the example: two datasets, two servers, one dashboard."""
    run_datasets(dash, store, mgr, TODAY)
    assert [host for host, _ in sent] == ["equity-host", "oms-host"]


def test_the_second_query_is_built_from_the_first_ones_answer(dash, store, mgr,
                                                              sent):
    run_datasets(dash, store, mgr, TODAY)
    _, orders_query = sent[1]
    assert "`RELIANCE.IB`INFY.IB" in orders_query
    assert "{{equity_syms.sym}}" not in orders_query


def test_the_activated_state_is_carried_into_the_query(dash, store, mgr, sent):
    """target and target_state are joined on id_target inside the query, so the
    dashboard never has to fetch a state table it does not display."""
    run_datasets(dash, store, mgr, TODAY)
    _, orders_query = sent[1]
    assert "target_state" in orders_query and "state=`activated" in orders_query
    assert "id_target" in orders_query


def test_the_state_is_the_latest_one_not_any_one_that_ever_said_activated():
    """A target has many target_state rows. Asking whether *a* row says
    activated answers a different question from whether the *current* state
    does — an order activated an hour ago and cancelled since would still be
    listed. The state is taken as the last row per id_target."""
    dash = import_dashboards_json(BUNDLE.read_text(encoding="utf-8"))[0]
    query = dash.datasets[1].raw_qsql
    assert "last state by id_target" in query
    # the naive form filters the state table before reducing it, which is the
    # question we are deliberately not asking
    assert "from target_state where state=" not in query


def test_the_state_table_is_narrowed_before_it_is_grouped():
    """This runs on every refresh. Grouping the whole of target_state to learn
    about a handful of targets makes the cost of the dashboard the size of the
    book rather than the size of the answer, so the ids are found first and the
    state table is cut down to them before the grouping happens."""
    dash = import_dashboards_json(BUNDLE.read_text(encoding="utf-8"))[0]
    query = dash.datasets[1].raw_qsql
    assert "target_state where id_target in" in query
    grouped_at = query.index("last state by id_target")
    narrowed_at = query.index("id_target in t`id_target")
    assert narrowed_at > grouped_at        # the where sits inside the sub-select


def test_target_is_read_once_not_twice():
    """The obvious way to narrow the state table scans target for its ids and
    again for the rows, on a table that is the whole live book."""
    dash = import_dashboards_json(BUNDLE.read_text(encoding="utf-8"))[0]
    assert dash.datasets[1].raw_qsql.count("select from target") == 1


def test_the_dashboard_ends_on_the_columns_of_target(dash, store, mgr):
    results = run_datasets(dash, store, mgr, TODAY)
    table = next(w for row in dash.rows for w in row.widgets
                 if w.type == "table")
    model = build_plot_model(table, results)
    assert model.columns == ["id_target", "sym", "side", "qty"]
    assert len(model.rows) == 2


def test_no_symbol_matched_yesterday_leaves_a_valid_query(dash, store, sent):
    """A holiday, or an ISIN that matched nothing. The second query must stay
    valid q rather than becoming a syntax error nobody can read."""
    empty = pd.DataFrame({"sym": pd.Series([], dtype=object)})
    responses = {EQUITY_Q: empty,
                 ORDERS_Q.replace("`RELIANCE.IB`INFY.IB", "`$()"):
                     ORDER_ROWS.head(0)}
    mgr = ConnectionManager(
        client_factory=lambda h, p: _Watching(h, responses, sent))
    results = run_datasets(dash, store, mgr, TODAY)
    assert results["active_orders"].error is None
    assert "`$()" in sent[1][1]


def test_a_dead_reference_database_degrades_one_panel(dash, store, mgr):
    """EQUITY DATA unreachable must not blank the page — the orders panel says
    what went wrong and the dashboard still renders."""
    store.conn.execute("DELETE FROM connections WHERE env='EQUITY DATA'")
    store.conn.commit()
    results = run_datasets(dash, store, mgr, TODAY)
    assert results["equity_syms"].error is not None
    assert results["active_orders"].error is not None      # it had nothing to ask
