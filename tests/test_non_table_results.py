"""A query whose result is not a table.

q does not only return tables. A function with no return gives `::`, `count`
gives a scalar, `exec sym from t` gives a vector, and a keyed lookup gives a
dictionary. A dataset is built on tables — its columns are what widgets are
drawn from — so none of those can be plotted, but every one of them is
something a person can type into the raw-q box by accident.

Before this, two of the four crashed the page with a Streamlit traceback and
the other two reported themselves as "'NoneType' object has no attribute
'copy'". A panel may fail; the page may not, and the message has to say what
actually happened.
"""
from datetime import date

import pandas as pd
import pytest

from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Row, Widget
from kdbmonitor.core.dataset import run_datasets
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.storage import Storage


class _Returns:
    """A client that hands back whatever q gave, table or not."""
    def __init__(self, value):
        self.value = value

    def query(self, qsql):
        return self.value


@pytest.fixture()
def store() -> Storage:
    s = Storage(":memory:")
    s.init_db()
    for spec in demo_connection_specs():
        s.add_connection(spec)
    return s


def _dashboard() -> Dashboard:
    return Dashboard(
        id=None, name="d", description="", refresh_secs=0,
        datasets=[Dataset(name="ds", env="orders", mode="raw",
                          raw_qsql="myFunction[]")],
        rows=[Row(widgets=[
            Widget(type="kpi", dataset="ds", title="K",
                   spec={"column": "qty", "agg": "sum"}),
            Widget(type="table", dataset="ds", title="T", spec={}),
            Widget(type="bar", dataset="ds", title="B",
                   spec={"x": "sym", "y": "qty"})])])


def _run(store, value):
    mgr = ConnectionManager(mock_factory=lambda conn: _Returns(value))
    return run_datasets(_dashboard(), store, mgr, date.today())


NOT_TABLES = {
    "a q function that returns nothing": None,
    "a scalar": 42,
    "a vector": pd.Series(["AAPL", "MSFT"]),
    "an empty vector": pd.Series([], dtype="object"),
    "a dictionary": {"a": 1},
    "a bare string": "AAPL",
}


@pytest.mark.parametrize("label,value", list(NOT_TABLES.items()),
                         ids=list(NOT_TABLES))
def test_a_result_that_is_not_a_table_is_reported_not_raised(store, label, value):
    results = _run(store, value)          # must not raise
    res = results["ds"]
    assert res.df is None
    assert res.error, f"{label} produced no error"


@pytest.mark.parametrize("label,value", list(NOT_TABLES.items()),
                         ids=list(NOT_TABLES))
def test_the_message_says_the_query_did_not_return_a_table(store, label, value):
    """'NoneType' object has no attribute 'copy' tells the reader nothing about
    the query they just typed."""
    error = _run(store, value)["ds"].error
    assert "table" in error.lower(), error
    assert "copy" not in error, error


@pytest.mark.parametrize("label,value", list(NOT_TABLES.items()),
                         ids=list(NOT_TABLES))
def test_every_widget_over_it_draws_an_error_rather_than_crashing(store, label,
                                                                  value):
    results = _run(store, value)
    for widget in _dashboard().rows[0].widgets:
        pm = build_plot_model(widget, results)      # must not raise
        assert pm.kind == "error"


def test_an_ordinary_table_is_untouched(store):
    """The guard must not cost a working dataset anything."""
    df = pd.DataFrame({"sym": ["AAPL"], "qty": [10.0]})
    res = _run(store, df)["ds"]
    assert res.error is None
    assert res.df is not None
    assert list(res.df.columns) == ["sym", "qty"]


def test_an_empty_table_with_columns_is_still_a_table(store):
    """Zero rows is data, not a failure: a filter that matched nothing must
    keep drawing the dashboard."""
    df = pd.DataFrame({"sym": pd.Series([], dtype="object"),
                       "qty": pd.Series([], dtype="float64")})
    res = _run(store, df)["ds"]
    assert res.error is None
    assert res.df is not None and res.df.empty

    results = {"ds": res}
    for widget in _dashboard().rows[0].widgets:
        assert build_plot_model(widget, results).kind != "error"


def test_build_plot_model_never_raises_even_handed_a_non_frame():
    """Its docstring promises this. A backstop, so a frame that reaches a
    widget by some other route still costs one panel rather than the page."""
    from kdbmonitor.core.dataset import DatasetResult

    for value in (pd.Series([1, 2]), {"a": 1}, 42, "text"):
        res = DatasetResult(name="ds", df=value, qsql="q", error=None)
        pm = build_plot_model(
            Widget(type="kpi", dataset="ds", title="K",
                   spec={"column": "qty", "agg": "sum"}), {"ds": res})
        assert pm.kind == "error"
