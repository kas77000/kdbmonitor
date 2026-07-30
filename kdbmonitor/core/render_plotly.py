"""PlotModel -> plotly figure, for the interactive on-screen dashboard.

A dumb backend: it draws what the PlotModel already decided. Hovering a line
chart shows every series' value at that x, which is the whole reason the screen
does not simply display the PDF's images.
"""
from __future__ import annotations

import plotly.graph_objects as go

from kdbmonitor.core import theme
from kdbmonitor.core.plotmodel import Band, PlotModel, Reference

CHART_KINDS = {"bar", "line", "scatter", "hist", "box", "heatmap", "pie"}

# bar/line/scatter only — the kinds a reference or band is ever resolved for.
_REFERENCEABLE = {"bar", "line", "scatter"}


def _layout(fig: go.Figure, pm: PlotModel) -> go.Figure:
    fig.update_layout(
        template=theme.PLOTLY_TEMPLATE,
        title=dict(text=pm.title, x=0, font=dict(size=15)),
        margin=dict(l=8, r=8, t=40 if pm.title else 8, b=8),
        paper_bgcolor=theme.SCREEN_SURFACE,
        plot_bgcolor=theme.SCREEN_SURFACE,
        font=dict(color=theme.SCREEN_INK, size=12),
        xaxis_title=pm.x_label or None,
        yaxis_title=pm.y_label or None,
        showlegend=len(pm.series) > 1,
        legend=dict(orientation="h", y=-0.18),
    )
    fig.update_xaxes(gridcolor=theme.SCREEN_GRID, zeroline=False)
    fig.update_yaxes(gridcolor=theme.SCREEN_GRID, zeroline=False)
    return fig


def _value_range(pm: PlotModel) -> tuple[float, float]:
    """The value-axis range the data alone would justify, padded the way
    plotly's own autorange pads.

    Plotly computes its real autorange client-side, which this process cannot
    see without a headless renderer — so rather than trust that a shape drawn
    at an arbitrary data value stays out of that calculation, the range is
    worked out here from the series alone and pinned explicitly. A reference
    or band added afterwards then has no way to widen it, whatever value it
    was given.
    """
    values = [v for s in pm.series for v in s.y if v is not None]
    if not values:
        return (0.0, 1.0)
    lo, hi = min(values), max(values)
    if lo == hi:
        pad = abs(lo) * 0.1 or 1.0
    else:
        pad = (hi - lo) * 0.08
    return lo - pad, hi + pad


def _band_span(xs: list, band: Band):
    """The two x values a band's ends resolve to, or None if either is not on
    this axis after all.

    Matched by text against the series' own x list — the same way the
    resolver matched them against the data — so a category, a date and a
    number all locate the same way. Unlike matplotlib's bar drawing, plotly's
    bar trace already plots at the categories themselves rather than integer
    positions, so no separate bar-chart case is needed here.
    """
    str_xs = [str(v) for v in xs]
    try:
        i0 = str_xs.index(str(band.start))
        i1 = str_xs.index(str(band.end))
    except ValueError:
        return None
    return xs[i0], xs[i1]


def _add_band(fig: go.Figure, band: Band, xs: list, horizontal: bool) -> None:
    span = _band_span(xs, band)
    if span is None:
        return
    v0, v1 = span
    # The label is placed by hand rather than through add_vrect's own
    # annotation_text: plotly's built-in placement averages the two ends to
    # centre the label, and that average is taken with plain arithmetic — which
    # raises on a categorical axis, where a band's ends are strings.
    if horizontal:
        fig.add_hrect(y0=v0, y1=v1, fillcolor=theme.MUTED, opacity=0.15,
                     layer="below", line_width=0)
        if band.label:
            fig.add_annotation(x=0.99, y=v0, xref="paper", yref="y",
                               xanchor="right", yanchor="bottom",
                               text=band.label, showarrow=False,
                               font=dict(size=10, color=theme.MUTED))
    else:
        fig.add_vrect(x0=v0, x1=v1, fillcolor=theme.MUTED, opacity=0.15,
                     layer="below", line_width=0)
        if band.label:
            fig.add_annotation(x=v0, y=0.97, xref="x", yref="paper",
                               xanchor="left", yanchor="top",
                               text=band.label, showarrow=False,
                               font=dict(size=10, color=theme.MUTED))


def _add_reference(fig: go.Figure, ref: Reference, xs: list, lim: tuple,
                   horizontal: bool):
    """Draw one reference, pinned inside ``lim``.

    Returns the reference's label if a constant fell outside that range —
    silently skipping it would leave the reader wondering where the line they
    asked for went, so the caller turns this into an annotation instead. A
    curve reference is drawn as an ordinary trace made to look like an
    annotation: dashed, unhoverable and out of the legend, so it cannot be
    mistaken for a second data series.
    """
    lo, hi = lim
    if ref.value is not None:
        if not (lo <= ref.value <= hi):
            return ref.label or "reference"
        if horizontal:
            fig.add_vline(x=ref.value, line_dash="dash",
                         line_color=theme.MUTED,
                         annotation_text=ref.label or None,
                         annotation_position="top")
        else:
            fig.add_hline(y=ref.value, line_dash="dash",
                         line_color=theme.MUTED,
                         annotation_text=ref.label or None,
                         annotation_position="right")
        return None

    n = min(len(xs), len(ref.values))
    if n == 0:
        return None
    cats, values = xs[:n], ref.values[:n]
    plot_x, plot_y = (values, cats) if horizontal else (cats, values)
    fig.add_scatter(x=plot_x, y=plot_y, mode="lines",
                    line=dict(dash="dash", color=theme.MUTED),
                    name=ref.label or "reference", showlegend=False,
                    hoverinfo="skip")
    return None


def _draw_extras(fig: go.Figure, pm: PlotModel) -> None:
    """References and bands, pinned inside the range the series data alone
    would produce — see :func:`_value_range` for why that range has to be
    computed rather than read back off the figure.
    """
    if not pm.references and not pm.bands:
        return
    horizontal = pm.kind == "bar" and pm.orientation == "h"
    xs = list(pm.series[0].x) if pm.series else []
    value_lim = _value_range(pm)

    for band in pm.bands:
        _add_band(fig, band, xs, horizontal)

    off_scale = [note for ref in pm.references
                 if (note := _add_reference(fig, ref, xs, value_lim,
                                            horizontal))]
    for i, label in enumerate(off_scale):
        fig.add_annotation(text=f"{label} — off scale", showarrow=False,
                           xref="paper", yref="paper", x=0.99,
                           y=0.99 - i * 0.08, xanchor="right",
                           yanchor="top", font=dict(size=10, color=theme.MUTED))

    if horizontal:
        fig.update_xaxes(range=list(value_lim))
    else:
        fig.update_yaxes(range=list(value_lim))


def _error(pm: PlotModel) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=f"⚠ {pm.error}", showarrow=False,
                       font=dict(color=theme.CRITICAL, size=13),
                       xref="paper", yref="paper", x=0.5, y=0.5)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return _layout(fig, pm)


def figure(pm: PlotModel) -> go.Figure:
    """Build the interactive figure for a chart PlotModel."""
    if pm.kind == "error":
        return _error(pm)
    if pm.kind not in CHART_KINDS:
        raise ValueError(f"'{pm.kind}' is not a chart — render it natively")

    fig = go.Figure()

    if pm.kind == "bar":
        for s in pm.series:
            if pm.orientation == "h":
                fig.add_bar(y=s.x, x=s.y, name=s.label, orientation="h",
                            marker_color=s.color)
            else:
                fig.add_bar(x=s.x, y=s.y, name=s.label, marker_color=s.color)

    elif pm.kind in ("line", "scatter"):
        mode = "lines+markers" if pm.kind == "line" else "markers"
        for s in pm.series:
            fig.add_scatter(x=s.x, y=s.y, name=s.label, mode=mode,
                            line=dict(color=s.color),
                            marker=dict(color=s.color, size=7))

    elif pm.kind == "hist":
        for s in pm.series:
            fig.add_histogram(x=s.y, name=s.label, nbinsx=pm.bins,
                              marker_color=s.color)

    elif pm.kind == "box":
        for s in pm.series:
            fig.add_box(y=s.y, name=s.label, marker_color=s.color)

    elif pm.kind == "heatmap":
        fig.add_heatmap(z=pm.matrix, x=pm.col_labels, y=pm.row_labels,
                        colorscale=theme.SEQUENTIAL_CMAP,
                        texttemplate="%{z}" if pm.annotate else None)

    elif pm.kind == "pie":
        s = pm.series[0]
        # Fixed 0-decimal percents: plotly's default varies the precision per
        # slice (32.6% next to 13%), which the printed page does not do.
        fig.add_pie(labels=s.x, values=s.y, hole=0.55 if pm.donut else 0.0,
                    texttemplate="%{percent:.0%}",
                    marker=dict(colors=[theme.color_for(i)
                                        for i in range(len(s.x))]))

    if pm.kind in _REFERENCEABLE:
        _draw_extras(fig, pm)

    fig = _layout(fig, pm)
    if pm.kind == "line":
        fig.update_layout(hovermode="x unified")
    return fig
