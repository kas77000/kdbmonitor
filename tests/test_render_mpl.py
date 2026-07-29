import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from kdbmonitor.core import theme
from kdbmonitor.core.plotmodel import PlotModel, Series
from kdbmonitor.core.render_mpl import (
    LINE_SPACING, TABLE_FONT, TABLE_MIN_FONT, _column_widths, draw,
    table_fit_font, table_layout,
)


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
    assert ax.tables[0].get_celld()[(0, 0)].get_text().get_text() == "market"


def test_table_highlight_colours_the_right_cell(ax):
    draw(ax, PlotModel(kind="table", columns=["market", "rejects"],
                       rows=[["Hong Kong", "3"], ["Japan", "0"]],
                       cell_colors={(0, 1): theme.CRITICAL}))
    cells = ax.tables[0].get_celld()
    assert cells[(1, 1)].get_text().get_color() == theme.CRITICAL   # +1 header row
    assert cells[(2, 1)].get_text().get_color() != theme.CRITICAL


# --- a table has to fit the height it was given ----------------------------

def _rows(n: int) -> list[list[str]]:
    return [[f"SYM{i}.HK", f"{i * 1000:,}", "up"] for i in range(n)]


def _table_model(n: int) -> PlotModel:
    return PlotModel(kind="table", title="Affected orders",
                     columns=["sym", "size", "state"], rows=_rows(n))


def test_a_table_that_fits_prints_every_row_at_full_size():
    shown, font, noted = table_layout(3.1, 12)
    assert (shown, noted) == (12, False)
    assert font == TABLE_FONT


def test_a_table_too_tall_for_its_slot_drops_rows_rather_than_legibility():
    """40 rows in 3.1 inches used to leave 5pt of space for 10.5pt type, which
    printed as one unreadable smudge."""
    shown, font, noted = table_layout(3.1, 40)
    assert shown < 40 and noted
    assert font >= TABLE_MIN_FONT
    assert shown * font * LINE_SPACING / 72 <= 3.1      # the rows really fit


def test_type_shrinks_before_rows_are_dropped():
    """A few rows over the comfortable count should tighten the type, not lose
    data: only once it hits the floor do rows go."""
    _, roomy, _ = table_layout(3.1, 8)
    _, tight, dropped_none = table_layout(3.1, 18)
    assert roomy == TABLE_FONT
    assert TABLE_MIN_FONT <= tight < roomy
    assert not dropped_none


def test_the_type_never_goes_below_the_legible_floor():
    for n in (50, 500, 5000):
        _, font, noted = table_layout(2.0, n)
        assert font >= TABLE_MIN_FONT and noted


def test_a_table_with_no_known_geometry_prints_everything():
    """An axes with no figure behind it: guessing a cap would silently hide
    rows, so nothing is dropped."""
    assert table_layout(0.0, 40) == (40, TABLE_FONT, False)


def test_the_dropped_rows_are_declared_on_the_page():
    fig = plt.figure(figsize=(8.27, 11.69))
    ax = fig.add_axes([0.1, 0.1, 0.8, 3.1 / 11.69])
    draw(ax, _table_model(40))
    note = " ".join(t.get_text() for t in ax.texts)
    assert "of 40 rows" in note
    # The header plus the rows it kept — never all 41.
    printed = max(r for r, _ in ax.tables[0].get_celld()) + 1
    assert printed < 41 and f"showing {printed - 1:,} of 40 rows" in note
    plt.close(fig)


def test_a_short_table_says_nothing_about_row_counts():
    fig = plt.figure(figsize=(8.27, 11.69))
    ax = fig.add_axes([0.1, 0.1, 0.8, 3.1 / 11.69])
    draw(ax, _table_model(6))
    assert "rows" not in " ".join(t.get_text() for t in ax.texts)
    assert max(r for r, _ in ax.tables[0].get_celld()) == 6      # 6 + header
    plt.close(fig)


def test_columns_are_shared_out_by_how_wide_their_content_is():
    """Equal columns made 'RELIANCE.IN' spill into the next cell while 'Side'
    sat half empty."""
    widths = _column_widths(["sym", "side"],
                            [["RELIANCE.IN", "buy"], ["0005.HK", "sellshort"]])
    assert widths[0] > widths[1]
    assert sum(widths) == pytest.approx(1.0)


# --- width: columns have to fit across the page, not just down it -----------

WIDE_COLUMNS = ["sym", "side", "orderId", "qty", "filledQty", "avgPrice",
                "limitPrice", "venue", "trader", "status", "startTime",
                "endTime"]
WIDE_ROW = ["RELIANCE.IN", "BUY", "ORD-00012345", "125,000", "118,400",
            "1,284.55", "1,290.00", "NSE-MAIN", "jdoe", "PARTIAL",
            "09:15:03.221", "15:29:58.004"]


def _wide_model(n_cols: int, n_rows: int = 6) -> PlotModel:
    return PlotModel(kind="table", columns=WIDE_COLUMNS[:n_cols],
                     rows=[list(WIDE_ROW[:n_cols]) for _ in range(n_rows)])


def _drawn(pm: PlotModel, width_in: float, height_in: float = 2.5):
    """The table as it really prints in ``width_in`` of page.

    Under the report's own font: the widths a column is sized against are the
    widths of the type it will actually be set in, so measuring under
    matplotlib's default instead would be measuring a page nobody prints.
    """
    theme.apply_seaborn_theme()
    fig = plt.figure(figsize=(width_in + 1.2, 11.69))
    ax = fig.add_axes([0.6 / (width_in + 1.2), 0.3,
                       width_in / (width_in + 1.2), height_in / 11.69])
    draw(ax, pm)
    fig.canvas.draw()
    return fig, ax


def _worst_overflow(fig, ax, columns, rows, width_in) -> float:
    """Widest cell text as a multiple of the column box it was given.

    Over 1.0 is the failure this is all about: matplotlib neither wraps nor
    clips, so text bigger than its column simply prints over the next one.
    """
    shares = _column_widths(columns, rows)
    renderer = fig.canvas.get_renderer()
    return max(cell.get_text().get_window_extent(renderer).width / fig.dpi
               / (shares[col] * width_in)
               for (_, col), cell in ax.tables[0].get_celld().items())


def test_fit_font_falls_as_columns_are_added():
    wide = 7.07                                     # A4 portrait, less margins
    sizes = [table_fit_font(WIDE_COLUMNS[:n], [WIDE_ROW[:n]], wide)
             for n in (4, 8, 12)]
    assert sizes == sorted(sizes, reverse=True)


def test_a_wider_page_fits_the_same_columns_at_a_bigger_size():
    cols, rows = WIDE_COLUMNS, [WIDE_ROW]
    assert (table_fit_font(cols, rows, 10.49)                # landscape
            > table_fit_font(cols, rows, 7.07))              # portrait


def test_a_wide_table_shrinks_its_type_rather_than_overlapping():
    """Twelve columns printed at 10.5pt ran 1.33x over their boxes."""
    pm = _wide_model(12)
    fig, ax = _drawn(pm, 7.07)
    assert _worst_overflow(fig, ax, pm.columns, pm.rows, 7.07) <= 1.0
    assert ax.tables[0].get_celld()[(0, 0)].get_fontsize() < TABLE_FONT
    plt.close(fig)


def test_a_table_that_fits_keeps_the_preferred_size():
    """Shrinking to fit must not cost every table its type size."""
    pm = _wide_model(4)
    fig, ax = _drawn(pm, 7.07)
    assert ax.tables[0].get_celld()[(0, 0)].get_fontsize() == TABLE_FONT
    plt.close(fig)


def test_type_never_shrinks_below_the_legible_floor():
    pm = _wide_model(12)
    fig, ax = _drawn(pm, 2.0)                        # far too narrow for it
    assert ax.tables[0].get_celld()[(0, 0)].get_fontsize() >= TABLE_MIN_FONT
    plt.close(fig)


def test_text_too_wide_even_at_the_floor_is_cut_and_says_so():
    """Past the floor there is no size left to give, so the choice is between a
    value cut short — which admits it — and columns printed over each other."""
    pm = _wide_model(12)
    fig, ax = _drawn(pm, 2.0)
    printed = " ".join(c.get_text().get_text()
                       for c in ax.tables[0].get_celld().values())
    assert "…" in printed
    assert "ORD-00012345" not in printed
    assert _worst_overflow(fig, ax, pm.columns, pm.rows, 2.0) <= 1.0
    plt.close(fig)


def test_a_table_with_no_geometry_is_not_trimmed():
    """Off a figure there is no width to fit to, so nothing is cut on a guess."""
    assert table_fit_font(["sym"], [["RELIANCE.IN"]], 0.0) == TABLE_FONT


def test_highlighting_still_lands_on_the_right_row_after_rows_are_dropped():
    fig = plt.figure(figsize=(8.27, 11.69))
    ax = fig.add_axes([0.1, 0.1, 0.8, 3.1 / 11.69])
    pm = _table_model(40)
    pm.cell_colors[(2, 1)] = theme.CRITICAL
    draw(ax, pm)
    cells = ax.tables[0].get_celld()
    assert cells[(3, 1)].get_text().get_color() == theme.CRITICAL   # +1 header
    plt.close(fig)


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
    draw(ax, PlotModel(kind="scatter", series=[_series(x=[1, 2], y=[3.0, 4.0])]))
    assert len(ax.collections) == 1


def test_hist_draws_bars(ax):
    draw(ax, PlotModel(kind="hist", bins=3, series=[_series(y=[1.0, 2.0, 3.0, 4.0])]))
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
    draw(ax, PlotModel(kind="error", title="By market", error="connection refused"))
    assert "connection refused" in _texts(ax)


def test_error_message_is_critical_coloured(ax):
    draw(ax, PlotModel(kind="error", title="X", error="boom"))
    assert any(t.get_color() == theme.CRITICAL for t in ax.texts)


def test_unknown_kind_does_not_raise(ax):
    draw(ax, PlotModel(kind="hologram", title="X"))
    assert "hologram" in _texts(ax)


def test_a_drawer_blowing_up_becomes_an_error_card(ax):
    # pie with mismatched labels/values would raise inside matplotlib
    draw(ax, PlotModel(kind="pie",
                       series=[Series("n", ["HK", "JP", "KR"], [1, 2],
                                      theme.BLUE)]))
    assert any(t.get_color() == theme.CRITICAL for t in ax.texts)


def test_single_series_bars_are_labelled_with_their_values(ax):
    draw(ax, PlotModel(kind="bar", series=[_series(y=[61.4, 88.2])]))
    assert "61.4" in _texts(ax) and "88.2" in _texts(ax)


def test_value_labels_share_one_format_across_the_series(ax):
    # 12.0 must not print as "12" next to "88.2" in the same chart
    draw(ax, PlotModel(kind="bar",
                       series=[_series(x=["A", "B", "C"], y=[88.2, 61.4, 12.0])]))
    assert "12.0" in _texts(ax)


def test_whole_numbers_get_no_decimals(ax):
    draw(ax, PlotModel(kind="bar", series=[_series(y=[12.0, 30.0])]))
    assert "12" in _texts(ax) and "12.0" not in _texts(ax)


def test_large_values_drop_the_decimal(ax):
    """A chart printed '47,833.3' beside a table saying '47,833'."""
    draw(ax, PlotModel(kind="bar",
                       series=[_series(x=["A", "B"], y=[47833.3, 11480.0])]))
    assert "47,833" in _texts(ax)
    assert "47,833.3" not in _texts(ax)


def test_small_values_keep_their_decimal(ax):
    draw(ax, PlotModel(kind="bar", series=[_series(y=[61.4, 88.2])]))
    assert "61.4" in _texts(ax)
