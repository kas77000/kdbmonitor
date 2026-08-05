"""A dataset that is fetched once.

Reference data — an instrument list, a book mapping, a universe loaded at start
of day — answers the same thing on every refresh, and a dashboard refreshing
every 15 seconds asks it 240 times an hour to be told so. Marking the dataset
static sends the query once and reuses the frame.

What must stay true: the *rest* of the dashboard is unaffected (a static dataset
is an ordinary one from the moment it has a frame), and the frame is only reused
for the question it answered — change the query, a parameter in it, or the
period, and it is asked again.
"""
from datetime import date

import pandas as pd
import pytest

from kdbmonitor.core.client import ConnectionManager, FakeClient
from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Parameter, Transform,
)
from kdbmonitor.core.dataset import run_datasets, trace_datasets
from kdbmonitor.core.models import Connection
from kdbmonitor.core.qcache import QueryCache
from kdbmonitor.core.storage import Storage

TODAY = date(2026, 8, 5)
UNIVERSE = "select from universe"
ORDERS = "select from target"


@pytest.fixture()
def store(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="order-rdb", host="rdb", port=1,
                                kind="realtime", env="orders"))
    s.add_connection(Connection(id=None, name="order-hdb", host="hdb", port=2,
                                kind="historical", env="orders"))
    return s


@pytest.fixture()
def client():
    return FakeClient({
        UNIVERSE: pd.DataFrame({"sym": ["AAPL", "MSFT"], "book": ["A", "B"]}),
        ORDERS: pd.DataFrame({"sym": ["AAPL"], "qty": [10]}),
        "select from universe where book=`A":
            pd.DataFrame({"sym": ["AAPL"], "book": ["A"]}),
        "select from universe where book=`B":
            pd.DataFrame({"sym": ["MSFT"], "book": ["B"]}),
    })


@pytest.fixture()
def mgr(client):
    return ConnectionManager(client_factory=lambda host, port: client)


def _dash(*datasets, **kw) -> Dashboard:
    return Dashboard(id=1, name="d", datasets=list(datasets), **kw)


def _universe(**kw) -> Dataset:
    return Dataset(name="universe", env="orders", mode="raw",
                   raw_qsql=UNIVERSE, **kw)


def _orders() -> Dataset:
    return Dataset(name="orders", env="orders", mode="raw", raw_qsql=ORDERS)


# --- fetched once ----------------------------------------------------------

def test_a_static_dataset_is_fetched_once_over_several_runs(store, mgr, client):
    dash, cache = _dash(_universe(static=True)), QueryCache()
    for _ in range(3):
        run_datasets(dash, store, mgr, TODAY, cache=cache)
    assert client.calls == [UNIVERSE]


def test_an_ordinary_dataset_is_fetched_every_run(store, mgr, client):
    """The default is unchanged: nothing starts being held by itself."""
    dash, cache = _dash(_universe()), QueryCache()
    for _ in range(3):
        run_datasets(dash, store, mgr, TODAY, cache=cache)
    assert client.calls == [UNIVERSE] * 3


def test_the_held_frame_is_the_one_the_dashboard_shows(store, mgr, client):
    dash, cache = _dash(_universe(static=True)), QueryCache()
    first = run_datasets(dash, store, mgr, TODAY, cache=cache)["universe"]
    again = run_datasets(dash, store, mgr, TODAY, cache=cache)["universe"]
    assert again.df.equals(first.df)
    assert again.row_count == first.row_count


def test_the_rest_of_the_dashboard_still_refreshes(store, mgr, client):
    """One held dataset must not hold the page: the panel beside it is live."""
    dash = _dash(_universe(static=True), _orders())
    cache = QueryCache()
    run_datasets(dash, store, mgr, TODAY, cache=cache)
    run_datasets(dash, store, mgr, TODAY, cache=cache)
    assert client.calls == [UNIVERSE, ORDERS, ORDERS]


def test_without_a_cache_a_static_dataset_queries_like_any_other(store, mgr,
                                                                 client):
    """What the editor's preview does: an explicit Run goes and asks."""
    dash = _dash(_universe(static=True))
    run_datasets(dash, store, mgr, TODAY)
    run_datasets(dash, store, mgr, TODAY)
    assert client.calls == [UNIVERSE] * 2


# --- only for the question it answered --------------------------------------

def test_a_changed_query_is_fetched_again(store, mgr, client):
    cache = QueryCache()
    run_datasets(_dash(_universe(static=True)), store, mgr, TODAY, cache=cache)
    edited = Dataset(name="universe", env="orders", mode="raw",
                     raw_qsql="select from universe where book=`A", static=True)
    run_datasets(_dash(edited), store, mgr, TODAY, cache=cache)
    assert client.calls == [UNIVERSE, "select from universe where book=`A"]


def test_a_parameter_in_the_query_is_asked_again_when_it_changes(store, mgr,
                                                                 client):
    dash = _dash(Dataset(name="universe", env="orders", mode="raw",
                         raw_qsql="select from universe where book={{param:book}}",
                         static=True),
                 parameters=[Parameter(name="book", kind="text",
                                       q_type="symbol", default="A")])
    cache = QueryCache()
    run_datasets(dash, store, mgr, TODAY, chosen={"book": "A"}, cache=cache)
    run_datasets(dash, store, mgr, TODAY, chosen={"book": "A"}, cache=cache)
    run_datasets(dash, store, mgr, TODAY, chosen={"book": "B"}, cache=cache)
    assert client.calls == ["select from universe where book=`A",
                            "select from universe where book=`B"]


def test_a_query_that_failed_is_not_held(store, mgr, client):
    """A held error would be an error nobody can clear."""
    broken = Dataset(name="universe", env="orders", mode="raw",
                     raw_qsql="select from nosuchtable", static=True)
    cache = QueryCache()
    assert run_datasets(_dash(broken), store, mgr, TODAY,
                        cache=cache)["universe"].error
    assert len(cache) == 0


def test_a_transform_still_runs_over_a_held_frame(store, mgr, client):
    """Held is about the round trip, not about the shaping: the transforms are
    this run's work over that frame."""
    ds = _universe(static=True, transforms=[
        Transform(kind="filter", params={"column": "book", "op": "=",
                                         "value": "A"})])
    cache = QueryCache()
    run_datasets(_dash(ds), store, mgr, TODAY, cache=cache)
    out = run_datasets(_dash(ds), store, mgr, TODAY, cache=cache)["universe"]
    assert list(out.df["sym"]) == ["AAPL"]
    assert client.calls == [UNIVERSE]


def test_a_dataset_derived_from_a_held_one_reads_the_held_rows(store, mgr,
                                                               client):
    dash = _dash(_universe(static=True),
                 Dataset(name="books", env="", source="derived",
                         base="universe"))
    cache = QueryCache()
    run_datasets(dash, store, mgr, TODAY, cache=cache)
    out = run_datasets(dash, store, mgr, TODAY, cache=cache)
    assert list(out["books"].df["sym"]) == ["AAPL", "MSFT"]
    assert client.calls == [UNIVERSE]


# --- saying so --------------------------------------------------------------

def test_the_run_that_fetched_it_reports_no_cache_stamp(store, mgr, client):
    """A stamp means 'you are looking at held rows'. The run that went to the
    server is not that."""
    cache = QueryCache()
    out = run_datasets(_dash(_universe(static=True)), store, mgr, TODAY,
                       cache=cache)["universe"]
    assert out.cached_at is None


def test_a_reused_frame_reports_when_it_was_fetched(store, mgr, client):
    cache = QueryCache()
    dash = _dash(_universe(static=True))
    run_datasets(dash, store, mgr, TODAY, cache=cache)
    out = run_datasets(dash, store, mgr, TODAY, cache=cache)["universe"]
    assert out.cached_at == cache.get(("rdb", 1, UNIVERSE)).at


def test_a_trace_reports_it_too(store, mgr, client):
    cache = QueryCache()
    dash = _dash(_universe(static=True))
    trace_datasets(dash, store, mgr, TODAY, cache=cache)
    assert trace_datasets(dash, store, mgr, TODAY,
                          cache=cache)["universe"].cached_at is not None


# --- the flag survives a save and an import ---------------------------------

def test_the_flag_round_trips_through_json():
    from kdbmonitor.core.dashboard_models import (
        dashboard_from_json, dashboard_to_json,
    )
    dash = _dash(_universe(static=True), _orders())
    back = dashboard_from_json(dashboard_to_json(dash))
    assert [d.static for d in back.datasets] == [True, False]


def test_a_dashboard_saved_before_this_existed_asks_every_time():
    from kdbmonitor.core.dashboard_models import dashboard_from_dict
    back = dashboard_from_dict({"name": "d", "datasets": [
        {"name": "universe", "env": "orders", "mode": "raw",
         "raw_qsql": UNIVERSE}]})
    assert back.datasets[0].static is False


# --- what the page says about it --------------------------------------------

def test_the_page_names_the_datasets_it_is_holding():
    from kdbmonitor.ui.dashboards import static_datasets
    dash = _dash(_universe(static=True), _orders())
    assert static_datasets(dash) == ["universe"]


def test_a_file_or_derived_dataset_is_never_named_as_held():
    """Neither sends a query, so neither has a round trip to be spared."""
    from kdbmonitor.ui.dashboards import static_datasets
    dash = _dash(Dataset(name="upload", env="", source="file", static=True),
                 Dataset(name="books", env="", source="derived", base="upload",
                         static=True))
    assert static_datasets(dash) == []


def test_the_page_reports_the_oldest_frame_it_is_holding(store, mgr, client):
    """The oldest, because it is the one a reader would be wrong about for
    longest."""
    from kdbmonitor.ui.dashboards import held_since
    dash = _dash(_universe(static=True))
    cache = QueryCache()
    run_datasets(dash, store, mgr, TODAY, cache=cache)
    payload = {"results": run_datasets(dash, store, mgr, TODAY, cache=cache)}
    assert held_since(dash, payload) == cache.get(("rdb", 1, UNIVERSE)).at


def test_a_page_holding_nothing_yet_says_nothing(store, mgr, client):
    from kdbmonitor.ui.dashboards import held_since
    dash = _dash(_universe(static=True))
    payload = {"results": run_datasets(dash, store, mgr, TODAY,
                                       cache=QueryCache())}
    assert held_since(dash, payload) is None
    assert held_since(dash, None) is None
