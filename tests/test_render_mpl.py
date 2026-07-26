import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from kdbmonitor.core import theme
from kdbmonitor.core.plotmodel import PlotModel, Series
from kdbmonitor.core.render_mpl import draw


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
