import pytest

from kdbmonitor.core import theme
from kdbmonitor.core.plotmodel import PlotModel, Series
from kdbmonitor.core.render_plotly import CHART_KINDS, figure


def _series(label="a", x=None, y=None, color=None):
    return Series(label, x or ["HK", "JP"], y or [1.0, 2.0],
                  color or theme.color_for(0))


def test_bar_makes_a_bar_trace():
    fig = figure(PlotModel(kind="bar", title="T", series=[_series()],
                           x_label="market", y_label="qty"))
    assert fig.data[0].type == "bar"
    assert list(fig.data[0].x) == ["HK", "JP"]
    assert fig.layout.title.text == "T"


def test_horizontal_bar_swaps_the_axes():
    fig = figure(PlotModel(kind="bar", series=[_series()], orientation="h"))
    assert fig.data[0].orientation == "h"
    assert list(fig.data[0].y) == ["HK", "JP"]


def test_line_uses_unified_hover_so_every_value_shows():
    fig = figure(PlotModel(kind="line", series=[_series("a"), _series("b")]))
    assert len(fig.data) == 2
    assert fig.data[0].type == "scatter"
    assert fig.data[0].mode == "lines+markers"
    assert fig.layout.hovermode == "x unified"


def test_series_colour_is_carried_through():
    fig = figure(PlotModel(kind="line", series=[_series(color="#123456")]))
    assert fig.data[0].line.color == "#123456"


def test_scatter_uses_markers_only():
    assert figure(PlotModel(kind="scatter", series=[_series()])).data[0].mode \
        == "markers"


def test_hist_uses_the_series_values_and_bin_count():
    fig = figure(PlotModel(kind="hist", bins=7, series=[_series(y=[1.0, 2.0, 3.0])]))
    assert fig.data[0].type == "histogram"
    assert list(fig.data[0].x) == [1.0, 2.0, 3.0]
    assert fig.data[0].nbinsx == 7


def test_box_makes_one_trace_per_group():
    fig = figure(PlotModel(kind="box",
                           series=[_series("HK", y=[1.0, 2.0]),
                                   _series("JP", y=[3.0])]))
    assert [t.type for t in fig.data] == ["box", "box"]
    assert fig.data[0].name == "HK"


def test_heatmap_carries_the_matrix_and_labels():
    fig = figure(PlotModel(kind="heatmap", matrix=[[1.0, 2.0], [3.0, 4.0]],
                           row_labels=["HK", "JP"], col_labels=["9", "10"]))
    assert fig.data[0].type == "heatmap"
    assert list(fig.data[0].y) == ["HK", "JP"]


def test_pie_becomes_a_donut_when_asked():
    fig = figure(PlotModel(kind="pie", donut=True,
                           series=[Series("n", ["HK", "JP"], [1, 2], theme.BLUE)]))
    assert fig.data[0].type == "pie"
    assert fig.data[0].hole > 0


def test_error_model_renders_a_message_not_an_exception():
    fig = figure(PlotModel(kind="error", title="Broken", error="connection refused"))
    assert "connection refused" in fig.layout.annotations[0].text


def test_unsupported_kind_is_rejected_loudly():
    with pytest.raises(ValueError, match="not a chart"):
        figure(PlotModel(kind="kpi", value="12"))


def test_chart_kinds_matches_the_renderer():
    assert CHART_KINDS == {"bar", "line", "scatter", "hist", "box",
                           "heatmap", "pie"}


def test_pie_percentages_use_a_fixed_precision():
    fig = figure(PlotModel(kind="pie",
                           series=[Series("n", ["A", "B", "C"], [5000, 3000, 1200],
                                          theme.BLUE)]))
    assert fig.data[0].texttemplate == "%{percent:.0%}"
