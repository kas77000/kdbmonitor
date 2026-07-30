import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from kdbmonitor.core import theme
from kdbmonitor.core.plotmodel import Band, PlotModel, Reference, Series
from kdbmonitor.core.render_mpl import draw
from kdbmonitor.core.render_plotly import figure


@pytest.fixture()
def ax():
    fig, ax = plt.subplots()
    yield ax
    plt.close(fig)


def _series(x=None, y=None):
    return Series("share", x or ["a", "b", "c"], y or [0.1, 0.2, 0.3],
                  theme.color_for(0))


def _model(**kw) -> PlotModel:
    base = dict(kind="line", title="T", series=[_series()])
    base.update(kw)
    return PlotModel(**base)


# --- matplotlib --------------------------------------------------------------

def test_a_constant_reference_draws_a_line(ax):
    draw(ax, _model(references=[Reference(label="target", value=0.25)]))
    assert any(line.get_linestyle() in ("--", "dashed")
               for line in ax.get_lines())


def test_a_reference_is_labelled(ax):
    draw(ax, _model(references=[Reference(label="average", value=0.2)]))
    text = " ".join(t.get_text() for t in ax.texts)
    assert "average" in text


def test_a_curve_reference_draws_every_point(ax):
    draw(ax, _model(references=[
        Reference(label="pace", values=[0.15, 0.25, 0.35])]))
    dashed = [ln for ln in ax.get_lines()
              if ln.get_linestyle() in ("--", "dashed")]
    assert dashed and len(dashed[0].get_ydata()) == 3


def test_a_reference_outside_the_data_does_not_rescale_the_axis(ax):
    """A mistyped threshold would otherwise flatten the real series into a
    line along the bottom of the chart."""
    plain = plt.subplots()[1]
    draw(plain, _model())
    without = plain.get_ylim()
    plt.close(plain.figure)

    draw(ax, _model(references=[Reference(label="miles away", value=500.0)]))
    assert ax.get_ylim() == pytest.approx(without, rel=0.05)


def test_a_reference_off_the_scale_says_so_rather_than_vanishing(ax):
    """Quietly dropping a line the author asked for is its own kind of wrong."""
    draw(ax, _model(references=[Reference(label="target", value=500.0)]))
    text = " ".join(t.get_text() for t in ax.texts).lower()
    assert "target" in text and ("off" in text or "scale" in text)


def test_a_band_is_shaded(ax):
    draw(ax, _model(bands=[Band(start="a", end="b", label="pre-open")]))
    assert len(ax.patches) >= 1


def test_a_band_is_labelled(ax):
    draw(ax, _model(bands=[Band(start="a", end="b", label="pre-open")]))
    assert "pre-open" in " ".join(t.get_text() for t in ax.texts)


def test_a_band_sits_behind_the_series(ax):
    """Drawn over the line it hides the data it is there to frame."""
    draw(ax, _model(bands=[Band(start="a", end="c", label="all")]))
    line = ax.get_lines()[0]
    assert ax.patches[0].get_zorder() < line.get_zorder()


def test_a_chart_with_neither_draws_as_it_always_did(ax):
    draw(ax, _model())
    assert ax.patches == [] or all(p.get_alpha() is None for p in ax.patches)


def test_references_work_on_a_bar_chart_too(ax):
    draw(ax, _model(kind="bar", references=[Reference(label="avg", value=0.2)]))
    assert any(ln.get_linestyle() in ("--", "dashed") for ln in ax.get_lines())


# --- plotly ------------------------------------------------------------------

def test_plotly_draws_a_constant_reference():
    fig = figure(_model(references=[Reference(label="target", value=0.25)]))
    assert any(getattr(s, "line", None) is not None
               and getattr(s.line, "dash", None) for s in fig.data) \
        or len(fig.layout.shapes) >= 1


def test_plotly_draws_a_curve_reference():
    fig = figure(_model(references=[
        Reference(label="pace", values=[0.15, 0.25, 0.35])]))
    assert len(fig.data) >= 2


def test_plotly_draws_a_band():
    fig = figure(_model(bands=[Band(start="a", end="b", label="pre-open")]))
    assert len(fig.layout.shapes) >= 1


def test_plotly_does_not_rescale_for_a_far_off_reference():
    fig = figure(_model(references=[Reference(label="far", value=500.0)]))
    top = fig.layout.yaxis.range[1] if fig.layout.yaxis.range else None
    assert top is None or top < 100


def test_a_plotly_chart_with_neither_is_unchanged():
    fig = figure(_model())
    assert len(fig.layout.shapes) == 0
