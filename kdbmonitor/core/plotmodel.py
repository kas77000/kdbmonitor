"""(Widget, dataset results) -> PlotModel: the resolved, backend-agnostic plot.

Every decision the two renderers could disagree about — which rows, what
aggregation, sort order, colour assignment, decimal places, threshold colouring —
is made here exactly once. The renderers only draw.
"""
from __future__ import annotations

import math
import operator
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
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
        named.append(widget.spec.get("group_by"))
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
class Reference:
    """A dashed line a chart is meant to be read against.

    A cumulated curve says little without the pace a flat schedule would have
    traced; a bar of shares says little without the average bar. Drawing either
    as an ordinary series makes a reference look like data, so it is carried
    apart and drawn apart.
    """
    label: str = ""
    value: Optional[float] = None                       # a level
    values: list = field(default_factory=list)          # or a curve
    dash: str = "dash"


@dataclass
class Band:
    """A shaded span behind the plot — a pre-open stretch, a lunch break."""
    start: Any = None
    end: Any = None
    label: str = ""


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
    # One part of a table printed across several pages: how many rows a full
    # part holds, so a short last part draws its rows the same height as the
    # rest instead of stretching to fill the slot. 0 = not part of anything.
    row_capacity: int = 0
    # The same rows still typed, under their display headers, for the screen.
    # `rows` above is formatted text and is what the page prints; sorting that
    # sorts text, so a number column ordered by clicking its header came out
    # wrong in a way that looked right. None where a table was sliced for
    # printing, which is a page's worth and never sorted.
    frame: Optional[pd.DataFrame] = None
    column_formats: list[str] = field(default_factory=list)
    # How wide each column should be, positionally: "" leaves it to the width
    # its own text earns, and "small"/"medium"/"large" is the author saying a
    # column is worth less (or more) of the page than its longest value would
    # otherwise claim. A note column with one long entry in it takes the room
    # nine short columns needed, and nothing about the text says it shouldn't.
    column_widths: list[str] = field(default_factory=list)
    # Which column the screen should gather the rows under, as a display
    # header. The author's starting point and nothing more: the reader picks
    # their own and keeps it, because the question a table is being asked
    # changes far faster than the dashboard answering it. Empty means a flat
    # list, which is what a table has always been and what it stays on the
    # printed page — a page has no folds to open.
    group_by: str = ""

    # charts
    series: list[Series] = field(default_factory=list)
    x_label: str = ""
    y_label: str = ""
    orientation: str = "v"          # bar only: v | h
    bins: int = 20                  # hist only
    donut: bool = False             # pie only
    regression: bool = False        # scatter only
    references: list[Reference] = field(default_factory=list)  # bar/line/scatter
    bands: list[Band] = field(default_factory=list)             # bar/line/scatter

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


# Which strftime directives make sense for a duration since midnight — a q
# ``time`` column knows an hour, never a year.
_TIME_DIRECTIVES = set("HIMSfp")
_DIRECTIVE = re.compile(r"%(.)")


def is_time_of_day_format(spec: str) -> bool:
    """Whether ``spec`` asks only for a time of day, e.g. '%H:%M'."""
    found = _DIRECTIVE.findall(spec or "")
    return bool(found) and all(d in _TIME_DIRECTIVES for d in found)


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
    # An infinity is not a null, so it survives the check above — but it is not
    # a figure either, and format() spells it "inf" right next to real numbers.
    # q has 0w, and any ratio taken against an empty total makes one.
    if isinstance(value, float) and math.isinf(value):
        return "—"
    if isinstance(value, pd.Timedelta):
        # A q time is a duration, so strftime cannot touch it directly. Anchor
        # it to a date to honour a time-of-day format; anything else (a year, a
        # month) means nothing here, so the default HH:MM:SS stands.
        if is_time_of_day_format(spec):
            try:
                return format(datetime.min + value.to_pytimedelta(), spec)
            except (TypeError, ValueError, OverflowError):
                pass
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

    # The same table twice over: `rows` already formatted, which is what the
    # printed page needs, and `frame` still typed, which is what the screen
    # needs. Sorting the formatted one sorts text — 1,284.55 lands beside
    # 1,2 and 9:15 lands before 10:00 — so a column of numbers sorted by
    # clicking its header came out in an order that looked deliberate and was
    # not. The screen sorts the values and formats them on the way out.
    frame = df[columns].copy()
    frame.columns = headers
    # Keyed off the real column names like the formats above, not the display
    # headers, so renaming a header keeps the width it was given.
    widths = spec.get("widths", {})
    # Carried as the display header rather than the real name, because the
    # screen groups the frame above and that frame wears the headers. A
    # group-by naming a column this table does not show is dropped: the heading
    # would be the one thing on screen the reader could not see the value of.
    group = spec.get("group_by", "")
    group_header = headers[columns.index(group)] if group in columns else ""
    return PlotModel(kind="table", title=title, columns=headers,
                     rows=rows, cell_colors=cell_colors, frame=frame,
                     column_formats=[formats.get(c, "") for c in columns],
                     column_widths=[str(widths.get(c, "")) for c in columns],
                     group_by=group_header)


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


def _reference_label(kind: str, spec: dict) -> str:
    given = spec.get("label")
    if isinstance(given, str) and given.strip():
        return given
    if kind == "quantile":
        return f"p{float(spec.get('value', 0.5)) * 100:g}"
    return kind


def _one_reference(df: pd.DataFrame, spec: dict, y_values: pd.Series) -> Optional[Reference]:
    """Resolve a single reference spec, or None if it cannot be drawn.

    Every way a reference can be malformed — a value that is not a number, a
    column that does not exist, a statistic taken over nothing — is a reason to
    drop it, never to raise: a mistyped reference must not cost the chart its
    data.
    """
    kind = spec.get("kind")
    if kind == "column":
        column = spec.get("column")
        if not isinstance(column, str) or column not in df.columns:
            return None
        given = spec.get("label")
        label = given if isinstance(given, str) and given.strip() else column
        return Reference(label=label, values=df[column].tolist())
    label = _reference_label(kind, spec)
    if kind == "constant":
        try:
            value = float(spec.get("value"))
        except (TypeError, ValueError):
            return None
        if math.isnan(value) or math.isinf(value):
            return None
        return Reference(label=label, value=value)
    if kind in ("mean", "median", "quantile"):
        if y_values is None or y_values.empty:
            return None
        if kind == "mean":
            value = y_values.mean()
        elif kind == "median":
            value = y_values.median()
        else:
            try:
                q = float(spec.get("value", 0.5))
            except (TypeError, ValueError):
                return None
            try:
                value = y_values.quantile(q)
            except (TypeError, ValueError):
                return None
        if value is None or (isinstance(value, float) and
                              (math.isnan(value) or math.isinf(value))):
            return None
        return Reference(label=label, value=float(value))
    return None


def _references(df: pd.DataFrame, spec: dict, y_values: pd.Series) -> list[Reference]:
    raw = spec.get("references")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ref = _one_reference(df, item, y_values)
        if ref is not None:
            out.append(ref)
    return out


def _bands(df: pd.DataFrame, spec: dict) -> list[Band]:
    raw = spec.get("bands")
    if not isinstance(raw, list):
        return []
    x = spec.get("x")
    if not x or x not in df.columns:
        return []
    # Matched as text so a time, a date and a category all work without a
    # second type system worrying about what kind of axis this is.
    known = set(df[x].astype(str))
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start, end = item.get("from"), item.get("to")
        if str(start) not in known or str(end) not in known:
            continue
        if str(start) > str(end):
            start, end = end, start
        out.append(Band(start=start, end=end, label=str(item.get("label", ""))))
    return out


def _plotted_y(df: pd.DataFrame, spec: dict) -> pd.Series:
    """The values a statistic reference is taken over.

    A hue column splits the chart into several series, but a reference is one
    line — so the statistic is taken over the whole named y column, before it
    is split, rather than trying to average across series that may not even
    share a unit.
    """
    y = spec.get("y")
    y = y[0] if isinstance(y, list) else y
    if not y or y not in df.columns:
        return pd.Series([], dtype=float)
    return df[y]


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
                     orientation=spec.get("orientation", "v"),
                     references=_references(d, spec, _plotted_y(d, spec)),
                     bands=_bands(d, spec))


def _line(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    return PlotModel(kind="line", title=title, series=_xy_series(df, spec),
                     x_label=spec["x"], y_label=_y_label(spec),
                     references=_references(df, spec, _plotted_y(df, spec)),
                     bands=_bands(df, spec))


def _scatter(df: pd.DataFrame, spec: dict, title: str) -> PlotModel:
    return PlotModel(kind="scatter", title=title, series=_xy_series(df, spec),
                     x_label=spec["x"], y_label=_y_label(spec),
                     regression=bool(spec.get("regression")),
                     references=_references(df, spec, _plotted_y(df, spec)),
                     bands=_bands(df, spec))


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


def slice_table(pm: PlotModel, start: int, count: int, capacity: int) -> PlotModel:
    """The rows ``start..start+count`` of a table, as its own model.

    Used to print a long table over several pages: the headers and the column
    formatting come along unchanged, and the highlight colours are re-indexed
    onto the rows this part actually shows.
    """
    rows = pm.rows[start:start + count]
    colors = {(r - start, c): colour
              for (r, c), colour in pm.cell_colors.items()
              if start <= r < start + len(rows)}
    return replace(pm, rows=rows, cell_colors=colors, row_capacity=capacity)


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
