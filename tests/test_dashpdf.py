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
    pages = paginate([Row(height_in=2.0), Row(height_in=2.0)])
    assert len(pages) == 1
    assert len(pages[0]) == 2


def test_first_row_starts_below_the_header():
    (_, y_top), = paginate([Row(height_in=1.0)])[0]
    assert y_top > 0


def test_rows_overflow_onto_a_second_page():
    pages = paginate([Row(height_in=3.0) for _ in range(5)])     # 15in > one page
    assert len(pages) >= 2
    assert sum(len(p) for p in pages) == 5


def test_a_row_taller_than_a_page_still_gets_placed():
    pages = paginate([Row(height_in=99.0), Row(height_in=1.0)])
    assert sum(len(p) for p in pages) == 2            # no infinite loop


def test_the_header_eats_into_the_first_page():
    assert CONTENT_H_FIRST < 11.69


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


def test_a_multi_page_dashboard_renders_every_page():
    rows = [Row(height_in=3.0, widgets=[
        Widget(type="bar", dataset="by_market", title=f"Row {i}",
               spec={"x": "market", "y": "pct"})]) for i in range(5)]
    out = dashboard_to_pdf_bytes(_dash(rows), _results(), RT, AS_OF)
    assert out.startswith(b"%PDF")
    assert out.count(b"/Type /Page\n") >= 2 or b"/Count 2" in out or len(out) > 20000


# --- filename --------------------------------------------------------------

def test_filename_is_slugged_and_stamped():
    assert pdf_filename(_dash([]), AS_OF) == "short_sell_2026-07-26_0915.pdf"


def test_filename_strips_awkward_characters():
    assert pdf_filename(Dashboard(id=1, name="P&L / risk (EOD)"), AS_OF) == \
        "p_l_risk_eod_2026-07-26_0915.pdf"
