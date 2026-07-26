from datetime import date, datetime

import pandas as pd

from kdbmonitor.core.dashboard_models import Dashboard, Row, Widget
from kdbmonitor.core.dashpdf import (
    CONTENT_H_FIRST, dashboard_page_png_bytes, dashboard_to_pdf_bytes,
    page_count, page_limit, paginate, pdf_filename, plan_rows,
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


# --- page plan for the editor ----------------------------------------------

def test_every_row_gets_a_placement():
    rows = [Row(height_in=h) for h in (0.9, 2.4, 3.0, 3.0, 2.0)]
    assert [p.index for p in plan_rows(rows)] == [0, 1, 2, 3, 4]


def test_placements_agree_with_the_pdf_pagination():
    """The editor must never disagree with what actually prints."""
    rows = [Row(height_in=h) for h in (0.9, 2.4, 3.0, 3.0, 2.0, 4.0)]
    pages = paginate(rows)
    plan = plan_rows(rows)
    assert max(p.page for p in plan) == len(pages)
    for page_no, page in enumerate(pages, start=1):
        on_page = [p for p in plan if p.page == page_no]
        assert len(on_page) == len(page)
        assert [p.y_top for p in on_page] == [y for _, y in page]


def test_the_first_row_of_each_page_is_flagged():
    rows = [Row(height_in=3.0) for _ in range(6)]
    starts = [p.index for p in plan_rows(rows) if p.starts_page]
    assert starts[0] == 0
    assert len(starts) == len(paginate(rows))


def test_free_space_shrinks_down_the_page():
    rows = [Row(height_in=2.0), Row(height_in=2.0), Row(height_in=2.0)]
    free = [p.free_after for p in plan_rows(rows)]
    assert free == sorted(free, reverse=True)
    assert all(f >= 0 for f in free)


def test_free_space_is_never_negative_for_an_oversized_row():
    assert plan_rows([Row(height_in=99.0)])[0].free_after == 0.0


def test_page_one_holds_less_than_later_pages():
    """The title band only appears on page 1."""
    assert page_limit(1) < page_limit(2)
    assert page_limit(2) == page_limit(3)


def test_an_empty_dashboard_plans_nothing():
    assert plan_rows([]) == []


# --- per-page PNG preview ---------------------------------------------------

def _multipage_dash() -> Dashboard:
    rows = [Row(height_in=3.0, widgets=[
        Widget(type="bar", dataset="by_market", title=f"Chart {i + 1}",
               spec={"x": "market", "y": "pct"})]) for i in range(5)]
    return _dash(rows)


def test_the_preview_renders_more_than_one_page():
    dash = _multipage_dash()
    assert page_count(dash) >= 2


def test_each_page_renders_different_content():
    """A page argument that is accepted but ignored would look identical."""
    dash = _multipage_dash()
    one = dashboard_page_png_bytes(dash, _results(), RT, AS_OF, page_no=1)
    two = dashboard_page_png_bytes(dash, _results(), RT, AS_OF, page_no=2)
    assert one.startswith(b"\x89PNG") and two.startswith(b"\x89PNG")
    assert one != two


def test_an_out_of_range_page_is_clamped_not_crashed():
    dash = _multipage_dash()
    last = dashboard_page_png_bytes(dash, _results(), RT, AS_OF,
                                    page_no=page_count(dash))
    assert dashboard_page_png_bytes(dash, _results(), RT, AS_OF, page_no=99) == last
    assert dashboard_page_png_bytes(dash, _results(), RT, AS_OF, page_no=0) == \
        dashboard_page_png_bytes(dash, _results(), RT, AS_OF, page_no=1)


def test_a_single_page_dashboard_previews_its_only_page():
    dash = _dash([Row(height_in=1.0, widgets=[
        Widget(type="kpi", dataset="by_market",
               spec={"column": "n_orders", "agg": "sum"})])])
    assert page_count(dash) == 1
    assert dashboard_page_png_bytes(dash, _results(), RT, AS_OF).startswith(b"\x89PNG")


def test_continuation_pages_carry_no_header():
    """No repeated '<name> (continued)' band — the footer's page number is enough."""
    import matplotlib.pyplot as plt
    from kdbmonitor.core.dashpdf import _header

    fig = plt.figure()
    _header(fig, _dash([]), RT, AS_OF, first=False)
    assert fig.texts == []
    plt.close(fig)

    fig = plt.figure()
    _header(fig, _dash([]), RT, AS_OF, first=True)
    assert any("Short sell" in t.get_text() for t in fig.texts)
    plt.close(fig)


def test_dropping_the_header_gives_later_pages_more_room():
    from kdbmonitor.core.dashpdf import HEADER_H_CONT
    assert HEADER_H_CONT < 0.3
    assert page_limit(2) > page_limit(1)


# --- report period line -----------------------------------------------------

def test_a_date_range_prints_as_a_range():
    from kdbmonitor.core.dashpdf import report_period
    assert report_period(HIST, AS_OF) == "2026-06-01 → 2026-06-30"


def test_a_single_day_range_prints_one_date():
    from kdbmonitor.core.dashpdf import report_period
    one_day = ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 1))
    assert report_period(one_day, AS_OF) == "2026-06-01"


def test_realtime_prints_the_moment_it_was_taken():
    from kdbmonitor.core.dashpdf import report_period
    assert report_period(RT, AS_OF) == "2026-07-26 09:15"


def test_the_period_line_carries_no_words():
    """Just the dates — no 'Real-time'/'Historical', no description."""
    from kdbmonitor.core.dashpdf import report_period
    for rt in (RT, HIST):
        line = report_period(rt, AS_OF)
        assert not any(w in line.lower() for w in
                       ("real", "historical", "as of", "generated"))


def test_the_header_shows_only_the_name_and_the_period():
    import matplotlib.pyplot as plt
    from kdbmonitor.core.dashpdf import _header, report_period

    dash = _dash([])                       # description "by market"
    fig = plt.figure()
    _header(fig, dash, HIST, AS_OF, first=True)
    texts = [t.get_text() for t in fig.texts]
    plt.close(fig)

    assert texts == ["Short sell", report_period(HIST, AS_OF)]
    assert dash.description not in texts   # description stays off the page
