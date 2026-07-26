import pandas as pd

from kdbmonitor.core import theme
from kdbmonitor.core.dashboard_models import Widget
from kdbmonitor.core.dataset import DatasetResult
from kdbmonitor.core.plotmodel import build_plot_model


def _ok(df: pd.DataFrame, name="by_market") -> dict:
    return {name: DatasetResult(name, df, "q", None, row_count=len(df))}


def _summary() -> pd.DataFrame:
    return pd.DataFrame([
        {"market": "Hong Kong", "n_orders": 12, "completion_pct": 61.4, "n_rejections": 3},
        {"market": "Japan",     "n_orders": 30, "completion_pct": 88.2, "n_rejections": 0},
        {"market": "Korea",     "n_orders": 5,  "completion_pct": 12.0, "n_rejections": 1},
    ])


# --- errors ----------------------------------------------------------------

def test_missing_dataset_becomes_an_error_model():
    pm = build_plot_model(Widget(type="kpi", dataset="nope", title="X"), {})
    assert pm.kind == "error"
    assert "nope" in pm.error
    assert pm.title == "X"


def test_failed_dataset_carries_its_message():
    results = {"by_market": DatasetResult("by_market", None, "q", "connection refused")}
    pm = build_plot_model(Widget(type="kpi", dataset="by_market"), results)
    assert pm.kind == "error"
    assert "connection refused" in pm.error


def test_missing_column_becomes_an_error_model():
    w = Widget(type="kpi", dataset="by_market", spec={"column": "nope", "agg": "sum"})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.kind == "error"
    assert "nope" in pm.error


# --- kpi -------------------------------------------------------------------

def test_kpi_aggregates_and_formats():
    w = Widget(type="kpi", dataset="by_market", title="Orders",
               spec={"column": "n_orders", "agg": "sum", "fmt": ",.0f"})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.kind == "kpi"
    assert pm.value == "47"
    assert pm.title == "Orders"


def test_kpi_thousands_separator_and_suffix():
    df = pd.DataFrame({"qty": [1234567]})
    w = Widget(type="kpi", dataset="by_market",
               spec={"column": "qty", "agg": "sum", "fmt": ",.0f", "suffix": " sh"})
    assert build_plot_model(w, _ok(df)).value == "1,234,567 sh"


def test_kpi_threshold_colours_the_value():
    w = Widget(type="kpi", dataset="by_market",
               spec={"column": "n_rejections", "agg": "sum", "fmt": ",.0f",
                     "thresholds": [{"op": ">", "value": 0, "color": "critical"}]})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.value == "4"
    assert pm.value_color == theme.CRITICAL


def test_kpi_without_a_matching_threshold_is_ink():
    w = Widget(type="kpi", dataset="by_market",
               spec={"column": "n_rejections", "agg": "sum", "fmt": ",.0f",
                     "thresholds": [{"op": ">", "value": 99, "color": "critical"}]})
    assert build_plot_model(w, _ok(_summary())).value_color == theme.INK


def test_kpi_on_an_empty_frame_shows_a_dash():
    w = Widget(type="kpi", dataset="by_market",
               spec={"column": "n_orders", "agg": "sum"})
    assert build_plot_model(w, _ok(pd.DataFrame(columns=["n_orders"]))).value == "—"


# --- table -----------------------------------------------------------------

def test_table_selects_and_formats_columns():
    w = Widget(type="table", dataset="by_market",
               spec={"columns": ["market", "completion_pct"],
                     "formats": {"completion_pct": ".1f"}})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.kind == "table"
    assert pm.columns == ["market", "completion_pct"]
    assert pm.rows[0] == ["Hong Kong", "61.4"]


def test_table_defaults_to_every_column():
    pm = build_plot_model(Widget(type="table", dataset="by_market"), _ok(_summary()))
    assert pm.columns == ["market", "n_orders", "completion_pct", "n_rejections"]


def test_table_highlight_marks_matching_cells():
    w = Widget(type="table", dataset="by_market",
               spec={"columns": ["market", "n_rejections"],
                     "highlight": [{"column": "n_rejections", "op": ">",
                                    "value": 0, "color": "critical"}]})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.cell_colors[(0, 1)] == theme.CRITICAL     # Hong Kong, 3
    assert (1, 1) not in pm.cell_colors                 # Japan, 0


# --- bar / line ------------------------------------------------------------

def test_bar_builds_one_series_with_a_colour():
    w = Widget(type="bar", dataset="by_market",
               spec={"x": "market", "y": "completion_pct"})
    pm = build_plot_model(w, _ok(_summary()))
    assert pm.kind == "bar"
    assert len(pm.series) == 1
    assert pm.series[0].x == ["Hong Kong", "Japan", "Korea"]
    assert pm.series[0].y == [61.4, 88.2, 12.0]
    assert pm.series[0].color == theme.color_for(0)
    assert pm.x_label == "market"
    assert pm.y_label == "completion_pct"


def test_bar_sorts_descending_when_asked():
    w = Widget(type="bar", dataset="by_market",
               spec={"x": "market", "y": "completion_pct", "sort": "desc"})
    assert build_plot_model(w, _ok(_summary())).series[0].x == \
        ["Japan", "Hong Kong", "Korea"]


def test_bar_orientation_defaults_to_vertical():
    w = Widget(type="bar", dataset="by_market",
               spec={"x": "market", "y": "n_orders"})
    assert build_plot_model(w, _ok(_summary())).orientation == "v"


def test_line_supports_several_y_columns():
    df = pd.DataFrame({"date": ["d1", "d2"], "a": [1, 2], "b": [3, 4]})
    w = Widget(type="line", dataset="by_market", spec={"x": "date", "y": ["a", "b"]})
    pm = build_plot_model(w, _ok(df))
    assert [s.label for s in pm.series] == ["a", "b"]
    assert pm.series[1].y == [3, 4]
    assert pm.series[1].color == theme.color_for(1)


def test_line_splits_by_hue():
    df = pd.DataFrame({"date": ["d1", "d2", "d1", "d2"],
                       "market": ["HK", "HK", "JP", "JP"],
                       "qty": [1, 2, 3, 4]})
    w = Widget(type="line", dataset="by_market",
               spec={"x": "date", "y": "qty", "hue": "market"})
    pm = build_plot_model(w, _ok(df))
    assert [s.label for s in pm.series] == ["HK", "JP"]
    assert pm.series[0].y == [1, 2]


# --- scatter / hist / box / heatmap / pie / text ---------------------------

def test_scatter_carries_x_and_y_pairs():
    df = pd.DataFrame({"qty": [10, 20], "pct": [1.5, 2.5]})
    pm = build_plot_model(Widget(type="scatter", dataset="by_market",
                                 spec={"x": "qty", "y": "pct"}), _ok(df))
    assert pm.series[0].x == [10, 20]
    assert pm.series[0].y == [1.5, 2.5]


def test_hist_carries_raw_values_and_bins():
    df = pd.DataFrame({"slip": [1.0, 1.5, 2.0, 9.0]})
    pm = build_plot_model(Widget(type="hist", dataset="by_market",
                                 spec={"x": "slip", "bins": 5}), _ok(df))
    assert pm.series[0].y == [1.0, 1.5, 2.0, 9.0]
    assert pm.bins == 5


def test_box_groups_values_by_category():
    df = pd.DataFrame({"market": ["HK", "HK", "JP"], "pct": [10.0, 20.0, 90.0]})
    pm = build_plot_model(Widget(type="box", dataset="by_market",
                                 spec={"x": "market", "y": "pct"}), _ok(df))
    assert [s.label for s in pm.series] == ["HK", "JP"]
    assert pm.series[0].y == [10.0, 20.0]


def test_heatmap_pivots_into_a_matrix():
    df = pd.DataFrame({"market": ["HK", "HK", "JP"], "hour": [9, 10, 9],
                       "n": [1, 2, 3]})
    w = Widget(type="heatmap", dataset="by_market",
               spec={"rows": "market", "cols": "hour", "value": "n", "agg": "sum"})
    pm = build_plot_model(w, _ok(df))
    assert pm.row_labels == ["HK", "JP"]
    assert pm.col_labels == ["9", "10"]
    assert pm.matrix[0] == [1.0, 2.0]
    assert pm.matrix[1][1] == 0.0            # missing cells fill with zero


def test_pie_builds_labelled_slices():
    pm = build_plot_model(Widget(type="pie", dataset="by_market",
                                 spec={"by": "market", "value": "n_orders"}),
                          _ok(_summary()))
    assert pm.series[0].x == ["Hong Kong", "Japan", "Korea"]
    assert pm.series[0].y == [12, 30, 5]


def test_text_substitutes_dataset_aggregates():
    w = Widget(type="text", dataset="by_market",
               spec={"markdown": "{{by_market.sum.n_orders}} orders, "
                                 "{{by_market.mean.completion_pct}}% done"})
    assert build_plot_model(w, _ok(_summary())).text == "47 orders, 53.9% done"


def test_text_leaves_unknown_placeholders_visible():
    w = Widget(type="text", dataset="by_market",
               spec={"markdown": "{{by_market.sum.nope}}"})
    assert "nope" in build_plot_model(w, _ok(_summary())).text


def test_unknown_widget_type_is_an_error_model():
    pm = build_plot_model(Widget(type="hologram", dataset="by_market"),
                          _ok(_summary()))
    assert pm.kind == "error"
    assert "hologram" in pm.error
