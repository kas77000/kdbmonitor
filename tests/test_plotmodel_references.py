import pandas as pd
import pytest

from kdbmonitor.core.dashboard_models import Widget
from kdbmonitor.core.dataset import DatasetResult
from kdbmonitor.core.plotmodel import build_plot_model


def _results(df: pd.DataFrame | None = None) -> dict:
    frame = df if df is not None else pd.DataFrame({
        "t": ["09:15", "09:20", "09:25", "09:30"],
        "share": [0.10, 0.20, 0.30, 0.40],
        "pace": [0.25, 0.50, 0.75, 1.00]})
    return {"d": DatasetResult("d", frame, "q", None, row_count=len(frame))}


def _line(**spec) -> Widget:
    base = {"x": "t", "y": "share"}
    base.update(spec)
    return Widget(type="line", dataset="d", title="T", spec=base)


# --- constants ---------------------------------------------------------------

def test_a_constant_reference_is_resolved_with_its_label():
    pm = build_plot_model(_line(references=[
        {"kind": "constant", "value": 0.25, "label": "target"}]), _results())
    assert len(pm.references) == 1
    assert pm.references[0].value == pytest.approx(0.25)
    assert pm.references[0].label == "target"


def test_a_constant_given_as_text_is_still_a_number():
    """Specs are stored as JSON and edited in a text box."""
    pm = build_plot_model(_line(references=[
        {"kind": "constant", "value": "0.25"}]), _results())
    assert pm.references[0].value == pytest.approx(0.25)


def test_a_constant_that_is_not_a_number_is_dropped_not_raised():
    pm = build_plot_model(_line(references=[
        {"kind": "constant", "value": "banana"}]), _results())
    assert pm.references == []
    assert pm.error is None            # the chart still draws


# --- statistics of the plotted series ----------------------------------------

def test_a_mean_reference_is_the_mean_of_what_is_plotted():
    """Drawing the average is the commonest thing anyone asks of a chart, so it
    must not require deriving a column first."""
    pm = build_plot_model(_line(references=[{"kind": "mean"}]), _results())
    assert pm.references[0].value == pytest.approx(0.25)


def test_a_median_reference_is_the_median():
    pm = build_plot_model(_line(references=[{"kind": "median"}]), _results())
    assert pm.references[0].value == pytest.approx(0.25)


def test_a_quantile_reference_uses_the_value_given():
    pm = build_plot_model(_line(references=[
        {"kind": "quantile", "value": 0.5}]), _results())
    assert pm.references[0].value == pytest.approx(0.25)


def test_a_statistic_reference_gets_a_default_label_naming_itself():
    pm = build_plot_model(_line(references=[{"kind": "mean"}]), _results())
    assert "mean" in pm.references[0].label.lower()


def test_a_statistic_over_an_empty_frame_is_dropped_rather_than_nan():
    empty = pd.DataFrame({"t": [], "share": []})
    pm = build_plot_model(_line(references=[{"kind": "mean"}]), _results(empty))
    assert pm.references == []


# --- another column ----------------------------------------------------------

def test_a_column_reference_carries_that_column_s_values():
    pm = build_plot_model(_line(references=[
        {"kind": "column", "column": "pace", "label": "even pace"}]),
        _results())
    assert pm.references[0].values == pytest.approx([0.25, 0.50, 0.75, 1.00])
    assert pm.references[0].value is None


def test_a_column_reference_naming_an_absent_column_is_dropped():
    pm = build_plot_model(_line(references=[
        {"kind": "column", "column": "ghost"}]), _results())
    assert pm.references == []
    assert pm.error is None


# --- bands -------------------------------------------------------------------

def test_a_band_is_resolved_with_its_ends_and_label():
    pm = build_plot_model(_line(bands=[
        {"from": "09:15", "to": "09:25", "label": "pre-open"}]), _results())
    assert len(pm.bands) == 1
    assert (pm.bands[0].start, pm.bands[0].end) == ("09:15", "09:25")
    assert pm.bands[0].label == "pre-open"


def test_a_band_whose_ends_are_not_in_the_data_is_dropped():
    """Drawn anyway it would land at the origin and mean something false."""
    pm = build_plot_model(_line(bands=[{"from": "03:00", "to": "04:00"}]),
                          _results())
    assert pm.bands == []


def test_a_band_given_backwards_is_still_drawn_the_right_way_round():
    pm = build_plot_model(_line(bands=[{"from": "09:25", "to": "09:15"}]),
                          _results())
    assert (pm.bands[0].start, pm.bands[0].end) == ("09:15", "09:25")


# --- where they apply --------------------------------------------------------

def test_a_bar_chart_takes_references_too():
    w = Widget(type="bar", dataset="d", title="T",
               spec={"x": "t", "y": "share",
                     "references": [{"kind": "mean"}]})
    assert len(build_plot_model(w, _results()).references) == 1


def test_a_scatter_takes_references_too():
    w = Widget(type="scatter", dataset="d", title="T",
               spec={"x": "share", "y": "pace",
                     "references": [{"kind": "mean"}]})
    assert len(build_plot_model(w, _results()).references) == 1


def test_a_pie_ignores_references_rather_than_drawing_nonsense():
    """A line across a pie means nothing."""
    w = Widget(type="pie", dataset="d", title="T",
               spec={"by": "t", "value": "share",
                     "references": [{"kind": "mean"}]})
    assert build_plot_model(w, _results()).references == []


def test_a_table_ignores_references():
    w = Widget(type="table", dataset="d", title="T",
               spec={"references": [{"kind": "mean"}]})
    assert build_plot_model(w, _results()).references == []


# --- nothing changes for a chart that asks for neither -----------------------

def test_a_plain_chart_carries_no_references_or_bands():
    pm = build_plot_model(_line(), _results())
    assert pm.references == [] and pm.bands == []


def test_references_that_are_not_a_list_are_ignored():
    """A hand-edited bundle can carry anything."""
    pm = build_plot_model(_line(references="oops", bands=7), _results())
    assert pm.references == [] and pm.bands == []
