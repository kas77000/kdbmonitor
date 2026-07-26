"""(Widget, dataset results) -> PlotModel: the resolved, backend-agnostic plot.

Every decision the two renderers could disagree about — which rows, what
aggregation, sort order, colour assignment, decimal places, threshold colouring —
is made here exactly once. The renderers only draw.
"""
from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd

from kdbmonitor.core import theme
from kdbmonitor.core.dashboard_models import Widget

AGGS: dict[str, Callable[[pd.Series], Any]] = {
    "count": lambda s: s.count(),
    "nunique": lambda s: s.nunique(),
    "sum": lambda s: s.sum(),
    "mean": lambda s: s.mean(),
    "min": lambda s: s.min(),
    "max": lambda s: s.max(),
}

_OPS = {"=": operator.eq, "==": operator.eq, "!=": operator.ne,
        "<>": operator.ne, "<": operator.lt, "<=": operator.le,
        ">": operator.gt, ">=": operator.ge}

# Spec fields a widget cannot render without, and how to name them to a human.
# Lives here rather than in the editor so the editor's save-time check and the
# renderer's runtime check can never disagree about what a widget needs.
REQUIRED_SPEC: dict[str, tuple[str, ...]] = {
    "kpi": ("column", "agg"),
    "table": (),
    "text": ("markdown",),
    "bar": ("x", "y"),
    "line": ("x", "y"),
    "scatter": ("x", "y"),
    "hist": ("x",),
    "box": ("y",),
    "heatmap": ("rows", "cols", "value"),
    "pie": ("by", "value"),
}

FIELD_LABELS = {
    "x": "X axis", "y": "Y axis", "column": "column", "agg": "aggregate",
    "markdown": "text", "rows": "row grouping", "cols": "column grouping",
    "value": "value column", "by": "slice-by column",
}

# Which spec fields actually name a column. Distinct from REQUIRED_SPEC: a text
# widget requires 'markdown', but that is prose, not a column — checking it
# against the dataset's columns would flag every sentence as a missing column.
COLUMN_SPEC_FIELDS: dict[str, tuple[str, ...]] = {
    "kpi": ("column",),
    "table": (),          # its 'columns' list is checked separately
    "text": (),
    "bar": ("x", "y", "hue"),
    "line": ("x", "y", "hue"),
    "scatter": ("x", "y", "hue"),
    "hist": ("x",),
    "box": ("x", "y"),
    "heatmap": ("rows", "cols", "value"),
    "pie": ("by", "value"),
}


def referenced_columns(widget: Widget) -> list[str]:
    """Column names this widget binds to, for checking they still exist."""
    named = [widget.spec.get(f) for f in COLUMN_SPEC_FIELDS.get(widget.type, ())]
    if widget.type == "table":
        named += list(widget.spec.get("columns") or [])
    return [c for c in named if isinstance(c, str) and c.strip()]


def is_blank(value) -> bool:
    """A field the user never actually filled in."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def missing_spec_fields(widget: Widget) -> list[str]:
    """Required spec fields this widget has not been given."""
    return [f for f in REQUIRED_SPEC.get(widget.type, ())
            if is_blank(widget.spec.get(f))]


@dataclass
class Series:
    label: str
    x: list
    y: list
    color: str


@dataclass
class PlotModel:
    kind: str                       # widget type, or "error"
    title: str = ""
    error: Optional[str] = None

    # kpi
    value: Optional[str] = None
    value_color: Optional[str] = None
    caption: str = ""

    # table
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    cell_colors: dict = field(default_factory=dict)      # (row, col) -> hex

    # charts
    series: list[Series] = field(default_factory=list)
    x_label: str = ""
    y_label: str = ""
    orientation: str = "v"          # bar only: v | h
    bins: int = 20                  # hist only
    donut: bool = False             # pie only
    regression: bool = False        # scatter only

    # heatmap
    matrix: list[list[float]] = field(default_factory=list)
    row_labels: list[str] = field(default_factory=list)
    col_labels: list[str] = field(default_factory=list)
    annotate: bool = True

    # text
    text: str = ""


def _err(title: str, message: str) -> PlotModel:
    return PlotModel(kind="error", title=title, error=message)


def _need(df: pd.DataFrame, *columns: str) -> None:
    missing = [c for c in columns if c and c not in df.columns]
    if missing:
        raise KeyError(f"no column {', '.join(repr(m) for m in missing)} "
                       f"(have: {', '.join(map(str, df.columns))})")


def format_time_of_day(td: pd.Timedelta) -> str:
    """Render a q time/timespan as HH:MM:SS(.mmm).

    q ``time`` columns arrive from pykx as timedeltas, which stringify as
    "0 days 09:30:00" — never what anyone wants to read in a report.
    """
    total = td.total_seconds()
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours, rem = divmod(int(total), 3600)
    minutes, seconds = divmod(rem, 60)
    millis = int(round((total - int(total)) * 1000))
    stamp = f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{stamp}.{millis:03d}" if millis else stamp


def _fmt(value: Any, spec: str = "") -> str:
    # pd.NaT is not a float and not a Timedelta, so check nullness generically
    # before anything else.
    if value is None:
        return "—"
    try:
        if pd.isna(value):
            return "—"
    except (TypeError, ValueError):        # arrays and the like are never null
        pass
    if isinstance(value, pd.Timedelta):
        return format_time_of_day(value)
    try:
        return format(value, spec) if spec else str(value)
    except (TypeError, ValueError):
        return str(value)


def _threshold_color(value: Any, thresholds: list[dict]) -> str:
    for t in thresholds or []:
        op = _OPS.get(t.get("op", ">="))
        try:
            if op and op(value, t["value"]):
                return theme.resolve_color(t.get("color"))
        except TypeError:
            continue
    return theme.INK


# --- per-type resolvers ----------------------------------------------------

def _kpi(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    column, agg = spec.get("column", ""), spec.get("agg", "sum")
    _need(df, column)
    if agg not in AGGS:
        raise KeyError(f"unknown agg '{agg}'")
    if df.empty:
        return PlotModel(kind="kpi", title=title, value="—",
                         value_color=theme.INK, caption=spec.get("caption", ""))
    raw = AGGS[agg](df[column])
    text = _fmt(raw, spec.get("fmt", "")) + spec.get("suffix", "")
    return PlotModel(kind="kpi", title=title, value=text,
                     value_color=_threshold_color(raw, spec.get("thresholds", [])),
                     caption=spec.get("caption", ""))


def _table(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    columns = spec.get("columns") or list(df.columns)
    _need(df, *columns)
    formats = spec.get("formats", {})
    # Display labels are presentation only — formats and highlight rules keep
    # keying off the real column names, so renaming a header breaks nothing.
    labels = spec.get("labels", {})
    headers = [str(labels.get(c) or c) for c in columns]
    rows = [[_fmt(r[c], formats.get(c, "")) for c in columns]
            for _, r in df.iterrows()]

    cell_colors: dict = {}
    for rule in spec.get("highlight", []):
        col = rule["column"]
        if col not in columns:
            continue
        ci = columns.index(col)
        op = _OPS.get(rule.get("op", ">"))
        colour = theme.resolve_color(rule.get("color"))
        for ri, (_, r) in enumerate(df.iterrows()):
            try:
                if op and op(r[col], rule["value"]):
                    cell_colors[(ri, ci)] = colour
            except TypeError:
                continue

    return PlotModel(kind="table", title=title, columns=headers,
                     rows=rows, cell_colors=cell_colors)


def _y_label(spec: dict) -> str:
    y = spec.get("y")
    return ", ".join(y) if isinstance(y, list) else str(y)


def _xy_series(df: pd.DataFrame, spec: dict) -> list[Series]:
    """One series per y column, or one per hue value."""
    x = spec["x"]
    hue = spec.get("hue")
    ys = spec["y"] if isinstance(spec.get("y"), list) else [spec["y"]]
    _need(df, x, hue, *ys)

    if hue:
        return [Series(str(label), grp[x].tolist(), grp[ys[0]].tolist(),
                       theme.color_for(i))
                for i, (label, grp) in enumerate(df.groupby(hue, sort=True))]
    return [Series(y, df[x].tolist(), df[y].tolist(), theme.color_for(i))
            for i, y in enumerate(ys)]


def _sorted(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    order = spec.get("sort")
    if order not in ("asc", "desc"):
        return df
    y = spec["y"] if not isinstance(spec.get("y"), list) else spec["y"][0]
    _need(df, y)
    return df.sort_values(y, ascending=order == "asc").reset_index(drop=True)


def _bar(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    d = _sorted(df, spec)
    return PlotModel(kind="bar", title=title, series=_xy_series(d, spec),
                     x_label=spec["x"], y_label=_y_label(spec),
                     orientation=spec.get("orientation", "v"))


def _line(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    return PlotModel(kind="line", title=title, series=_xy_series(df, spec),
                     x_label=spec["x"], y_label=_y_label(spec))


def _scatter(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    return PlotModel(kind="scatter", title=title, series=_xy_series(df, spec),
                     x_label=spec["x"], y_label=_y_label(spec),
                     regression=bool(spec.get("regression")))


def _hist(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    x = spec["x"]
    _need(df, x)
    return PlotModel(kind="hist", title=title, x_label=x, y_label="count",
                     bins=int(spec.get("bins", 20)),
                     series=[Series(x, [], df[x].tolist(), theme.color_for(0))])


def _box(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    x, y = spec.get("x"), spec["y"]
    _need(df, x, y)
    if not x:
        return PlotModel(kind="box", title=title, y_label=y,
                         series=[Series(y, [], df[y].tolist(),
                                        theme.color_for(0))])
    series = [Series(str(label), [], grp[y].tolist(), theme.color_for(i))
              for i, (label, grp) in enumerate(df.groupby(x, sort=True))]
    return PlotModel(kind="box", title=title, x_label=x, y_label=y, series=series)


def _heatmap(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    rows, cols, value = spec["rows"], spec["cols"], spec["value"]
    _need(df, rows, cols, value)
    pivot = df.pivot_table(index=rows, columns=cols, values=value,
                           aggfunc=spec.get("agg", "sum"), fill_value=0,
                           sort=False)
    return PlotModel(
        kind="heatmap", title=title, x_label=cols, y_label=rows,
        row_labels=[str(i) for i in pivot.index],
        col_labels=[str(c) for c in pivot.columns],
        matrix=[[float(v) for v in row] for row in pivot.values],
        annotate=bool(spec.get("annotate", True)))


def _pie(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    by, value = spec["by"], spec["value"]
    _need(df, by, value)
    return PlotModel(kind="pie", title=title, donut=bool(spec.get("donut")),
                     series=[Series(value, df[by].astype(str).tolist(),
                                    df[value].tolist(), theme.color_for(0))])


_PLACEHOLDER = re.compile(r"\{\{(\w+)\.(\w+)\.(\w+)\}\}")


def _text(df: pd.DataFrame, spec: dict, title: str, name: str) -> PlotModel:
    """Markdown with {{dataset.agg.column}} placeholders resolved."""
    def repl(m: re.Match) -> str:
        ds, agg, column = m.groups()
        if ds != name or agg not in AGGS or column not in df.columns:
            return m.group(0)          # leave it visible rather than lying
        default = ".1f" if agg == "mean" else ",.0f"
        return _fmt(AGGS[agg](df[column]), spec.get("fmt", default))

    return PlotModel(kind="text", title=title,
                     text=_PLACEHOLDER.sub(repl, spec.get("markdown", "")))


_RESOLVERS: dict[str, Callable] = {
    "kpi": _kpi, "table": _table, "bar": _bar, "line": _line,
    "scatter": _scatter, "hist": _hist, "box": _box, "heatmap": _heatmap,
    "pie": _pie,
}


def build_plot_model(widget: Widget, results: dict) -> PlotModel:
    """Resolve a widget against its dataset result. Never raises."""
    title = widget.title
    result = results.get(widget.dataset)
    if result is None:
        return _err(title, f"unknown dataset '{widget.dataset}'")
    if result.error:
        return _err(title, result.error)
    if result.df is None:
        return _err(title, f"dataset '{widget.dataset}' produced no rows")

    # Say what is unset before trying to draw — otherwise a missing 'x' surfaces
    # as a bare KeyError reading just "x", which tells the reader nothing.
    missing = missing_spec_fields(widget)
    if missing:
        named = ", ".join(FIELD_LABELS.get(f, f) for f in missing)
        return _err(title, f"this {widget.type} widget has no {named} set — "
                           f"choose one in the editor")

    try:
        if widget.type == "text":
            return _text(result.df, widget.spec, title, widget.dataset)
        resolver = _RESOLVERS.get(widget.type)
        if resolver is None:
            return _err(title, f"unknown widget type '{widget.type}'")
        return resolver(result.df, widget.spec, title)
    except (KeyError, ValueError, TypeError) as exc:
        return _err(title, str(exc).strip("'"))
