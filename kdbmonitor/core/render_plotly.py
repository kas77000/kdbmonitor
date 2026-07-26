"""PlotModel -> plotly figure, for the interactive on-screen dashboard.

A dumb backend: it draws what the PlotModel already decided. Hovering a line
chart shows every series' value at that x, which is the whole reason the screen
does not simply display the PDF's images.
"""
from __future__ import annotations

import plotly.graph_objects as go

from kdbmonitor.core import theme
from kdbmonitor.core.plotmodel import PlotModel

CHART_KINDS = {"bar", "line", "scatter", "hist", "box", "heatmap", "pie"}


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

    fig = _layout(fig, pm)
    if pm.kind == "line":
        fig.update_layout(hovermode="x unified")
    return fig
