from datetime import date, datetime

import pandas as pd

from kdbmonitor.core.dashboard_models import (
    Dashboard, Row, Widget, dashboard_from_dict, dashboard_to_dict,
)
from kdbmonitor.core.dashpdf import (
    CONTENT_H_FIRST, LANDSCAPE, PORTRAIT, choose_page, dashboard_page_png_bytes,
    dashboard_to_pdf_bytes, page_count, page_limit, paginate, pdf_filename,
    plan_rows, split_rows,
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


# --- a long table carries on over the pages --------------------------------

def _long_results(n: int) -> dict:
    df = pd.DataFrame([{"market": f"M{i}", "n_orders": i, "pct": float(i)}
                       for i in range(n)])
    return {"by_market": DatasetResult("by_market", df, "q", None, row_count=n)}


def _table_dash(height_in: float = 3.4) -> Dashboard:
    return _dash([Row(height_in=height_in, widgets=[
        Widget(type="table", dataset="by_market", title="Affected orders")])])


def _rows_printed(parts) -> int:
    """Every table row the parts between them will print."""
    seen = set()
    for part in parts:
        for start, count in part.slices.values():
            seen.update(range(start, start + count))
    return len(seen)


def test_a_table_that_fits_is_one_part():
    parts = split_rows(_table_dash().rows, _long_results(8))
    assert len(parts) == 1
    assert parts[0].slices == {}


def test_a_long_table_is_split_into_parts():
    parts = split_rows(_table_dash().rows, _long_results(60))
    assert len(parts) > 1
    assert all(p.slices for p in parts)


def test_every_row_of_a_long_table_is_printed_somewhere():
    """The point of the whole exercise: nothing is left out."""
    for n in (25, 60, 200):
        parts = split_rows(_table_dash().rows, _long_results(n))
        printed = _rows_printed(parts)
        assert printed >= n, f"{n} rows -> only {printed} printed"


def test_the_parts_run_in_order_and_do_not_overlap():
    parts = split_rows(_table_dash().rows, _long_results(60))
    starts = [p.slices[0][0] for p in parts]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)
    assert starts[0] == 0


def test_a_longer_table_needs_more_pages():
    short = page_count(_table_dash(), _long_results(10))
    long = page_count(_table_dash(), _long_results(120))
    assert long > short


def test_the_row_height_stops_mattering_once_a_table_flows():
    """Continuations coalesce to fill each page, so the report's length follows
    the data and the page — not the slot the table happened to start in."""
    short, tall = _table_dash(2.5), _table_dash(7.0)
    assert abs(page_count(short, _long_results(90))
               - page_count(tall, _long_results(90))) <= 1
    for dash in (short, tall):
        assert _rows_printed(split_rows(dash.rows, _long_results(90))) >= 90


def test_page_count_without_results_counts_only_the_layout():
    """The editor has no data at layout time; it must not guess."""
    assert page_count(_table_dash()) == 1


def test_a_runaway_table_is_bounded_and_says_so():
    """A dataset capped at 20,000 rows must not try to print 1,000 pages."""
    from kdbmonitor.core.dashpdf import MAX_TABLE_PARTS
    parts = split_rows(_table_dash().rows, _long_results(20_000))
    assert len(parts) == MAX_TABLE_PARTS
    # The renderer declares the shortfall, because rows are being left out.
    assert _rows_printed(parts) < 20_000


def test_consecutive_parts_on_one_page_become_a_single_block():
    """Two chunks of the same table under two headers read as two tables."""
    pages = paginate(_table_dash(2.0).rows, _long_results(60))
    for page in pages:
        rows_on_page = [p for p, _ in page]
        assert len(rows_on_page) == len(set(id(p) for p in rows_on_page))
        for part, _ in page:
            assert part.height_in >= 2.0        # merged blocks are taller


def test_a_page_of_one_table_holds_exactly_one_block():
    """Merging stopped after two chunks, so a third started again under its own
    header halfway down the page."""
    pages = paginate(_table_dash(2.0).rows, _long_results(200))
    for page in pages:
        assert len([p for p, _ in page if p.slices]) == 1
    assert any(p.spans >= 3 for page in pages for p, _ in page)


def test_a_merged_block_still_prints_every_row():
    pages = paginate(_table_dash(2.0).rows, _long_results(60))
    printed = _rows_printed([p for page in pages for p, _ in page])
    assert printed >= 60


def test_a_long_table_renders_to_a_multi_page_pdf():
    out = dashboard_to_pdf_bytes(_table_dash(), _long_results(80), RT, AS_OF)
    assert out.startswith(b"%PDF")
    assert page_count(_table_dash(), _long_results(80)) >= 2


def test_each_page_of_a_flowing_table_looks_different():
    dash, results = _table_dash(), _long_results(80)
    one = dashboard_page_png_bytes(dash, results, RT, AS_OF, page_no=1)
    two = dashboard_page_png_bytes(dash, results, RT, AS_OF, page_no=2)
    assert one != two


def test_other_widgets_print_once_not_on_every_page():
    """A chart beside a flowing table belongs to the first part only."""
    rows = [Row(height_in=3.4, widgets=[
        Widget(type="table", dataset="by_market", title="Rows"),
        Widget(type="bar", dataset="by_market", title="Chart",
               spec={"x": "market", "y": "pct"})])]
    parts = split_rows(rows, _long_results(120))
    assert len(parts) > 1
    assert all(set(p.slices) == {0} for p in parts)      # only the table carries
    assert parts[0].part == 0 and parts[1].part == 1


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


def _page_texts(dash: Dashboard, results: dict, page_no: int) -> list[str]:
    """Every string printed on one page of the report."""
    import matplotlib.pyplot as plt
    from kdbmonitor.core.dashpdf import _render_page

    pages = paginate(dash.rows, results)
    fig = _render_page(dash, pages[page_no - 1], results, RT, AS_OF,
                       page_no, len(pages))
    out = [t.get_text() for t in fig.texts]
    plt.close(fig)
    return out


def test_a_table_continuing_onto_a_page_carries_no_heading():
    """A widget is titled where it starts. The pages it runs onto get nothing —
    not the title again, and not a '(continued)' variant of it."""
    dash, results = _table_dash(2.0), _long_results(200)
    assert any("Affected orders" in t for t in _page_texts(dash, results, 1))

    later = _page_texts(dash, results, 2)
    assert not any("Affected orders" in t for t in later)
    assert not any("continued" in t for t in later)


def test_a_widget_that_starts_on_a_later_page_keeps_its_title():
    """Only continuations lose the heading — a row that begins on page 2 is not
    a continuation of anything."""
    dash = _dash([Row(height_in=6.0, widgets=[
        Widget(type="bar", dataset="by_market", title=f"Chart {i}",
               spec={"x": "market", "y": "pct"})]) for i in range(2)])
    assert any("Chart 1" in t for t in _page_texts(dash, _results(), 2))


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


def test_the_footer_is_only_a_page_number():
    import matplotlib.pyplot as plt
    from kdbmonitor.core.dashpdf import _footer

    fig = plt.figure()
    _footer(fig, AS_OF, page_no=2, total=3)
    texts = [t.get_text() for t in fig.texts]
    plt.close(fig)

    assert texts == ["2 / 3"]


def test_no_generated_stamp_survives_anywhere_on_the_page():
    import matplotlib.pyplot as plt
    from kdbmonitor.core.dashpdf import _footer, _header

    fig = plt.figure()
    _header(fig, _dash([]), RT, AS_OF, first=True)
    _footer(fig, AS_OF, page_no=1, total=1)
    page_text = " ".join(t.get_text() for t in fig.texts)
    plt.close(fig)

    for gone in ("Generated", "KdbMonitor", "by market"):
        assert gone not in page_text


# --- orientation: a table has to fit across the page, not only down it -------

WIDE_COLS = ["sym", "side", "orderId", "qty", "filledQty", "avgPrice",
             "limitPrice", "venue", "trader", "status", "startTime", "endTime"]
WIDE_VALS = ["RELIANCE.IN", "BUY", "ORD-00012345", "125,000", "118,400",
             "1,284.55", "1,290.00", "NSE-MAIN", "jdoe", "PARTIAL",
             "09:15:03.221", "15:29:58.004"]


def _wide_results(n_cols: int, n_rows: int = 6) -> dict:
    df = pd.DataFrame([dict(zip(WIDE_COLS[:n_cols], WIDE_VALS[:n_cols]))
                       for _ in range(n_rows)])
    return {"orders": DatasetResult("orders", df, "q", None, row_count=n_rows)}


def _wide_dash(n_cols: int, orientation: str = "auto") -> Dashboard:
    d = _dash([Row(widgets=[Widget(type="table", dataset="orders")],
                   height_in=2.5)])
    d.orientation = orientation
    return d


def test_a_table_that_fits_across_the_page_leaves_it_upright():
    assert choose_page(_wide_dash(4), _wide_results(4)) is PORTRAIT


def test_a_table_too_wide_for_the_page_turns_it():
    """Twelve columns down an A4 portrait page printed on top of each other."""
    assert choose_page(_wide_dash(12), _wide_results(12)) is LANDSCAPE


def test_the_turn_is_decided_for_the_whole_report_not_page_by_page():
    """One wide table turns every page — a reader should not have to rotate the
    document back and forth through a single report."""
    wide = Row(widgets=[Widget(type="table", dataset="orders")], height_in=2.5)
    narrow = Row(widgets=[Widget(type="kpi", dataset="orders",
                                 spec={"column": "qty", "agg": "count"})],
                 height_in=2.0)
    d = _dash([narrow, wide, narrow])
    assert choose_page(d, _wide_results(12)) is LANDSCAPE


def test_an_explicit_orientation_is_not_second_guessed():
    assert choose_page(_wide_dash(12, "portrait"), _wide_results(12)) is PORTRAIT
    assert choose_page(_wide_dash(4, "landscape"), _wide_results(4)) is LANDSCAPE


def test_auto_stays_upright_when_there_is_no_data_to_measure():
    """The editor has no results, so it plans against the page it prints on
    today rather than guessing at one."""
    assert choose_page(_wide_dash(12)) is PORTRAIT


def test_a_dashboard_with_no_orientation_saved_still_prints():
    """Dashboards stored before the setting existed carry no 'orientation'."""
    d = dashboard_from_dict({"name": "Old", "rows": []})
    assert d.orientation == "auto"
    assert choose_page(d, _wide_results(12)) is PORTRAIT


def test_orientation_survives_a_round_trip():
    d = _wide_dash(4, "landscape")
    assert dashboard_from_dict(dashboard_to_dict(d)).orientation == "landscape"


def test_a_turned_page_is_shorter_and_so_holds_fewer_rows():
    rows = [Row(height_in=2.0) for _ in range(6)]
    assert len(paginate(rows, sheet=LANDSCAPE)) > len(paginate(rows))
    assert page_limit(2, LANDSCAPE) < page_limit(2, PORTRAIT)


def test_the_editor_plans_against_the_page_it_was_pinned_to():
    rows = [Row(height_in=2.0) for _ in range(6)]
    upright = max(p.page for p in plan_rows(rows, PORTRAIT))
    turned = max(p.page for p in plan_rows(rows, LANDSCAPE))
    assert turned > upright


def test_a_turned_report_renders_on_turned_paper():
    d = _wide_dash(12)
    png = dashboard_page_png_bytes(d, _wide_results(12), RT, AS_OF)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    assert width > height


def test_an_upright_report_still_renders_upright():
    png = dashboard_page_png_bytes(_wide_dash(4), _wide_results(4), RT, AS_OF)
    assert int.from_bytes(png[16:20], "big") < int.from_bytes(png[20:24], "big")


def test_the_pdf_of_a_wide_dashboard_is_produced_at_all():
    pdf = dashboard_to_pdf_bytes(_wide_dash(12), _wide_results(12), RT, AS_OF)
    assert pdf.startswith(b"%PDF")


def test_the_page_count_is_taken_on_the_page_it_will_print_on():
    """Counted portrait, a turned report is counted on a page it never uses."""
    d = _wide_dash(12)
    results = _wide_results(12)
    assert page_count(d, results) == len(
        paginate(d.rows, results, {}, LANDSCAPE))
