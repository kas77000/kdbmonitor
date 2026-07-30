from datetime import date

import pandas as pd
import pytest

from kdbmonitor.core.client import ConnectionManager, FakeClient
from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Transform
from kdbmonitor.core.dataset import (
    build_qsql, effective_time, resolve_connection, run_datasets,
    substitute_connections,
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


def test_a_realtime_dataset_on_a_historical_dashboard_hits_the_rdb(store):
    q = "select from target"
    mgr = _mgr({q: pd.DataFrame({"sym": ["AAPL"]})})
    dash = Dashboard(
        id=1, name="d",
        time_context={"mode": "historical",
                      "range": {"kind": "preset", "name": "last_30d"}},
        datasets=[Dataset(name="live", env="orders", table="target",
                          time_mode="realtime")])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["live"].error is None
    assert res["live"].qsql == q          # no date clause injected


# --- market data ------------------------------------------------------------

@pytest.fixture()
def md_store(tmp_path):
    s = Storage(str(tmp_path / "md.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="refdata", host="ref", port=9,
                                kind="marketdata", env="marketdata"))
    return s


def test_a_marketdata_env_resolves_to_its_server(md_store):
    from kdbmonitor.core.dataset import resolve_target
    conn, eff = resolve_target(md_store, "marketdata", RT)
    assert conn.name == "refdata"
    assert eff.mode == "realtime"


def test_a_marketdata_env_ignores_a_historical_period(md_store):
    """Reference data is not partitioned by date, so no date clause applies."""
    from kdbmonitor.core.dataset import resolve_target
    conn, eff = resolve_target(md_store, "marketdata", HIST)
    assert conn.name == "refdata"
    assert eff.mode == "realtime"
    assert eff.start is None


def test_a_marketdata_dataset_gets_no_date_clause_on_a_historical_dashboard(md_store):
    q = "select from instrument"
    mgr = _mgr({q: pd.DataFrame({"sym": ["AAPL"], "sector": ["Technology"]})})
    dash = Dashboard(
        id=1, name="d",
        time_context={"mode": "historical",
                      "range": {"kind": "preset", "name": "last_30d"}},
        datasets=[Dataset(name="ref", env="marketdata", table="instrument")])

    res = run_datasets(dash, md_store, mgr, TODAY)
    assert res["ref"].error is None
    assert res["ref"].qsql == q                 # no date within (...)


def test_a_raw_marketdata_query_needs_no_date_constraint(md_store):
    q = "select from instrument where sector=`Technology"
    mgr = _mgr({q: pd.DataFrame({"sym": ["AAPL"]})})
    dash = Dashboard(
        id=1, name="d",
        time_context={"mode": "historical",
                      "range": {"kind": "preset", "name": "last_30d"}},
        datasets=[Dataset(name="ref", env="marketdata", mode="raw", raw_qsql=q)])

    res = run_datasets(dash, md_store, mgr, TODAY)
    assert res["ref"].error is None


def test_a_realtime_env_still_refuses_a_dateless_historical_raw_query(store):
    mgr = _mgr({})
    dash = Dashboard(
        id=1, name="d",
        time_context={"mode": "historical",
                      "range": {"kind": "preset", "name": "last_30d"}},
        datasets=[Dataset(name="o", env="orders", mode="raw",
                          raw_qsql="select from target")])
    assert "must constrain 'date'" in run_datasets(dash, store, mgr, TODAY)["o"].error


def test_environments_expose_all_three_kinds(md_store):
    pair = md_store.list_environments()["marketdata"]
    assert set(pair) == {"realtime", "historical", "marketdata"}
    assert pair["marketdata"].name == "refdata"


def test_an_unknown_environment_is_reported_before_any_query(md_store):
    mgr = _mgr({})
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="x", env="nope", table="instrument")])
    assert "unknown environment" in run_datasets(dash, md_store, mgr, TODAY)["x"].error


# --- one query, both modes --------------------------------------------------

GUARDED_Q = ("select from target where "
             "{{#historical}}date within ({{date_from}};{{date_to}}), {{/historical}}"
             "side=`sellshort")


def test_a_guarded_raw_query_runs_in_both_modes(store):
    rt_q = "select from target where side=`sellshort"
    hist_q = ("select from target where date within (2026.06.01;2026.06.30), "
              "side=`sellshort")
    mgr = _mgr({rt_q: pd.DataFrame({"sym": ["A"]}),
                hist_q: pd.DataFrame({"sym": ["A"], "date": ["d"]})})

    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="o", env="orders", mode="raw", raw_qsql=GUARDED_Q)])

    dash.time_context = {"mode": "realtime"}
    assert run_datasets(dash, store, mgr, TODAY)["o"].qsql == rt_q

    dash.time_context = {"mode": "historical",
                         "range": {"kind": "absolute",
                                   "from": "2026-06-01", "to": "2026-06-30"}}
    assert run_datasets(dash, store, mgr, TODAY)["o"].qsql == hist_q


def test_an_unguarded_placeholder_is_refused_in_realtime(store):
    """It would otherwise reach KDB verbatim as '{{date_from}}'."""
    mgr = _mgr({})
    dash = Dashboard(id=1, name="d", time_context={"mode": "realtime"}, datasets=[
        Dataset(name="o", env="orders", mode="raw",
                raw_qsql="select from target where date within "
                         "({{date_from}};{{date_to}})")])
    res = run_datasets(dash, store, mgr, TODAY)["o"]
    assert res.df is None
    assert "{{#historical}}" in res.error


# --- cross-process federation ({{conn:ENV}} -> hopen) -----------------------

def _with_quotes(store, historical: bool = False):
    """Add a 'quotes' environment (a second KDB process) to the orders store."""
    store.add_connection(Connection(id=None, name="quote-rdb", host="qhost",
                                    port=9, kind="realtime", env="quotes"))
    if historical:
        store.add_connection(Connection(id=None, name="quote-hdb", host="qhdb",
                                        port=10, kind="historical", env="quotes"))
    return store


def test_substitute_connections_resolves_a_handle(store):
    _with_quotes(store)
    q = 'h:hopen {{conn:quotes}}; h"select from qatt"'
    assert substitute_connections(q, store, RT) == \
        'h:hopen `:qhost:9; h"select from qatt"'


def test_build_qsql_injects_the_federated_handle(store):
    _with_quotes(store)
    ds = Dataset(name="d", env="orders", mode="raw",
                 raw_qsql='q1:(hopen {{conn:quotes}})"select from qatt"')
    assert build_qsql(ds, RT, {}, store) == \
        'q1:(hopen `:qhost:9)"select from qatt"'


def test_build_qsql_without_a_store_leaves_the_conn_token(store):
    """build_qsql stays pure when no store is passed (existing callers/tests)."""
    ds = Dataset(name="d", env="orders", mode="raw",
                 raw_qsql="hopen {{conn:quotes}}")
    assert build_qsql(ds, RT, {}) == "hopen {{conn:quotes}}"


def test_a_federated_env_follows_the_period_to_its_historical_twin(store):
    _with_quotes(store, historical=True)
    ds = Dataset(name="d", env="orders", mode="raw",
                 raw_qsql="hopen {{conn:quotes}}")
    assert build_qsql(ds, HIST, {}, store) == "hopen `:qhdb:10"


def test_run_dataset_federates_across_two_processes(store):
    _with_quotes(store)
    resolved = 'q1:(hopen `:qhost:9)"select state from qatt"; q1'
    mgr = _mgr({resolved: pd.DataFrame({"sym": ["A"], "state": ["up"]})})
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="lim", env="orders", mode="raw",
                raw_qsql='q1:(hopen {{conn:quotes}})"select state from qatt"; q1',
                extra_connections=["quotes"])])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["lim"].error is None
    assert res["lim"].df["state"].tolist() == ["up"]


def test_an_unknown_federated_env_is_captured_as_a_panel_error(store):
    mgr = _mgr({})
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="lim", env="orders", mode="raw",
                raw_qsql="hopen {{conn:nope}}")])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["lim"].df is None
    # A handle may name an environment or one connection, so a name that is
    # neither says so and lists what there is, rather than calling it an
    # unknown environment when it might have been meant as a server.
    assert "nope" in res["lim"].error
    assert "orders" in res["lim"].error


def test_naming_a_side_of_an_unknown_environment_still_says_so(store):
    """The ENV:kind form can only mean an environment, so it names that."""
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="lim", env="orders", mode="raw",
                raw_qsql="hopen {{conn:nope:realtime}}")])
    res = run_datasets(dash, store, _mgr({}), TODAY)
    assert "unknown environment" in res["lim"].error


def test_a_federated_env_missing_the_historical_side_errors_clearly(store):
    _with_quotes(store, historical=False)          # only a realtime quote server
    mgr = _mgr({})
    dash = Dashboard(
        id=1, name="d",
        time_context={"mode": "historical",
                      "range": {"kind": "absolute",
                                "from": "2026-06-01", "to": "2026-06-30"}},
        datasets=[Dataset(name="lim", env="orders", mode="raw",
                          raw_qsql="select date from target where date within "
                                   "({{date_from}};{{date_to}}); hopen {{conn:quotes}}")])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["lim"].df is None
    assert "no historical server" in res["lim"].error
