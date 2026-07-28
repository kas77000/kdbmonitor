"""The short-sell report, rebuilt as a dashboard definition.

Asserts the dashboard's datasets and widgets produce the same numbers as
short_sell_report.py's summarise_by_market, and that the page renders to a PDF.
This is the feature's acceptance test: the standalone script should be
reproducible in the app with no Python.
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
                    "mapping": MARKETS, "length": 3, "default": "Unknown"}),
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
                             "fmt": ".1f", "suffix": "%",
                             "thresholds": [{"op": "<", "value": 0,
                                             "color": "critical"}]}),
                Widget(type="kpi", dataset="by_market", title="Rejections",
                       spec={"column": "n_rejections", "agg": "sum", "fmt": ",.0f",
                             "thresholds": [{"op": ">", "value": 0,
                                             "color": "critical"}]})]),
            Row(height_in=2.4, widgets=[
                Widget(type="table", dataset="by_market", title="By market",
                       spec={"columns": ["market", "n_orders", "order_qty",
                                         "executed_qty", "completion_pct",
                                         "n_rejections"],
                             "labels": {"market": "Market", "n_orders": "Orders",
                                        "order_qty": "Order qty",
                                        "executed_qty": "Executed",
                                        "completion_pct": "Completion",
                                        "n_rejections": "Rejections"},
                             "formats": {"completion_pct": ".1f",
                                         "order_qty": ",.0f",
                                         "executed_qty": ",.0f"},
                             "highlight": [{"column": "n_rejections", "op": ">",
                                            "value": 0, "color": "critical"},
                                           {"column": "completion_pct", "op": "<",
                                            "value": 0, "color": "critical"},
                                           {"column": "order_qty", "op": "<=",
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
    assert results["by_market"].error is None

    by_market = results["by_market"].df.set_index("market")
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
    flagged = {pm.rows[r][0] for (r, _) in pm.cell_colors}
    assert flagged == {"Hong Kong", "Japan"}      # Korea has none


def test_the_whole_page_renders_to_a_pdf(store, mgr):
    dash = short_sell_dashboard()
    results = run_datasets(dash, store, mgr, date.today())
    out = dashboard_to_pdf_bytes(dash, results,
                                 ResolvedTime("realtime", None, None),
                                 datetime(2026, 7, 26, 9, 15))
    assert out.startswith(b"%PDF")
    assert len(out) > 5000


def test_the_same_dashboard_runs_against_the_historical_server(store, mgr):
    """The whole point of environments: one dropdown, no dataset edit."""
    store.add_connection(Connection(id=None, name="order-hdb", host="hdb", port=2,
                                    kind="historical", env="orders",
                                    schema={"target": ["date"] + list(RAW.columns)}))
    hist_query = ("select from target where date within "
                  "(2026.06.01;2026.06.30), side=`sellshort")
    client = FakeClient({hist_query: RAW})
    hist_mgr = ConnectionManager(client_factory=lambda host, port: client)

    dash = short_sell_dashboard()
    dash.time_context = {"mode": "historical",
                         "range": {"kind": "absolute", "from": "2026-06-01",
                                   "to": "2026-06-30"}}

    results = run_datasets(dash, store, hist_mgr, date.today())
    assert results["by_market"].error is None
    assert results["by_market"].qsql == hist_query
    assert len(results["by_market"].df) == 3      # HK, JP, KS
