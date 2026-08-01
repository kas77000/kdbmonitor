"""A dashboard's table on screen: sortable, searchable, formatted.

The printed page and the screen want different things from the same table. The
page wants text, already formatted, laid out to a fixed height. The screen wants
the values, so that clicking a header sorts numbers as numbers and times in
order — which is why ``PlotModel`` carries both and this module draws from the
typed one.

Formatting is then Streamlit's job rather than ours, which means translating the
format specs the dashboard stores. Where a spec has no counterpart the column is
left to Streamlit's own default: showing an unformatted number beats showing a
formatted one that cannot be sorted.
"""
from __future__ import annotations

import re
from datetime import date

import pandas as pd
import streamlit as st

from kdbmonitor.core import tablefilter as tf
from kdbmonitor.core.plotmodel import format_time_of_day

# ",.0f" and friends: an optional thousands comma, then optional decimals.
_NUMERIC = re.compile(r"^(,)?\.?(\d+)?f$")

# Streamlit prints a column of durations in its own words — "15 hours" for
# 15:00:00 — because a timedelta is a length of time and it has no way to know
# this one is a clock reading. A q `time`, `minute`, `second` or `timespan`
# column arrives from pykx as exactly that, so every time-of-day column on
# every dashboard read as a duration until it was anchored to a date here.
_ANCHOR = pd.Timestamp("1970-01-01")
_DAY = pd.Timedelta(days=1)
_ZERO = pd.Timedelta(0)
_ONE_TICK = pd.Timedelta(nanoseconds=1)

# What a clock column prints when the widget names no format of its own.
_DEFAULT_TIME_FORMAT = "HH:mm:ss"

# strftime -> the momentJS tokens Streamlit's column config speaks. Only the
# directives that mean something in both are here; anything else leaves the
# column to Streamlit's default rather than printing a half-translated pattern.
_MOMENT_TOKENS: dict[str, str] = {
    "Y": "YYYY", "y": "YY", "m": "MM", "d": "DD", "H": "HH", "I": "hh",
    "M": "mm", "S": "ss", "f": "SSS", "p": "A", "b": "MMM", "B": "MMMM",
    "a": "ddd", "A": "dddd", "j": "DDDD", "%": "[%]",
}


def search_key(widget_key: str) -> str:
    return f"tbl_q_{widget_key}"


def filter_key(widget_key: str, index: int, part: str) -> str:
    """A filter control's key, named by the column's position rather than its
    name — a header can hold spaces, dots and anything else a query returned."""
    return f"tbl_f{part}{index}_{widget_key}"


def moment_format(spec: str) -> str:
    """A strftime pattern as a momentJS one, or '' if it can't be expressed.

    ``'%Y-%m-%d %H:%M'`` -> ``'YYYY-MM-DD HH:mm'``. Literal letters are wrapped
    in brackets, moment's own escape, because an unescaped 'h' in "9h30" is a
    twelve-hour clock to moment and would print the hour twice.
    """
    out: list[str] = []
    i = 0
    while i < len(spec or ""):
        char = spec[i]
        if char == "%" and i + 1 < len(spec):
            token = _MOMENT_TOKENS.get(spec[i + 1])
            if token is None:
                return ""
            out.append(token)
            i += 2
            continue
        out.append(f"[{char}]" if char.isalpha() else char)
        i += 1
    return "".join(out)


def _as_clock(column: pd.Series):
    """A duration column as a datetime holding the same clock time, or None.

    None where a clock can't hold it: a negative duration, or one past 24
    hours, would come out as a wrapped time of day — 25:00:00 shown as
    01:00:00 is not a formatting nicety, it is the wrong answer.
    """
    finite = column.dropna()
    if not finite.empty and ((finite < _ZERO).any() or (finite >= _DAY).any()):
        return None
    return _ANCHOR + column


def prepare(headers: list[str], formats: list[str], frame: pd.DataFrame
            ) -> tuple[pd.DataFrame, dict]:
    """The frame Streamlit should draw, and the config to draw it with.

    Only the specs with a real counterpart are translated. A column whose
    format cannot be expressed is left alone rather than approximated, because
    a number shown plainly is honest and one shown in the wrong units is not.

    The frame comes back changed in one case only — a duration column becomes
    the clock time it stands for — because that column has no honest default
    to fall back to.
    """
    config: dict = {}
    out = frame
    for header, spec in zip(headers, list(formats) + [""] * len(headers)):
        if header not in frame.columns:
            continue
        column = frame[header]

        if pd.api.types.is_timedelta64_dtype(column):
            # Handled with or without a format: the default is wrong here, so
            # there is nothing to leave the column to.
            clock = _as_clock(column)
            if clock is not None:
                if out is frame:
                    out = frame.copy()
                out[header] = clock
                config[header] = st.column_config.DatetimeColumn(
                    format=moment_format(spec) or _DEFAULT_TIME_FORMAT)
            else:
                # Longer than a day, so it is a duration after all. Printed as
                # text — which sorts as text; the values it covers are the ones
                # a clock column would have had to lie about anyway.
                if out is frame:
                    out = frame.copy()
                out[header] = column.map(
                    lambda v: "" if pd.isna(v) else format_time_of_day(v))
            continue

        if pd.api.types.is_datetime64_any_dtype(column):
            moment = moment_format(spec) if spec else ""
            if moment:
                config[header] = st.column_config.DatetimeColumn(format=moment)
            continue

        if not spec or not pd.api.types.is_numeric_dtype(column):
            continue
        if spec.endswith("%"):
            config[header] = st.column_config.NumberColumn(format="percent")
            continue
        match = _NUMERIC.match(spec)
        if not match:
            continue
        grouped, decimals = match.group(1), match.group(2)
        if grouped:
            config[header] = st.column_config.NumberColumn(format="localized")
        else:
            config[header] = st.column_config.NumberColumn(
                format=f"%.{decimals or 0}f")
    return out, config


def column_config(headers: list[str], formats: list[str],
                  frame: pd.DataFrame) -> dict:
    """Just the config half of :func:`prepare`."""
    return prepare(headers, formats, frame)[1]


def matching(frame: pd.DataFrame, query: str) -> pd.DataFrame:
    """The rows mentioning ``query``, anywhere in them.

    The quick lookup, and only that: somebody has an order number in front of
    them and does not yet know which column it sits in. Narrowing by named
    columns — side is BUY *and* quantity over a hundred thousand — is what the
    per-column filters beside it are for, and this box cannot express it.

    Matched against each value as it prints, so searching "9:15" finds the
    bucket a reader can see rather than the timestamp underneath it.
    """
    text = (query or "").strip().casefold()
    if not text or frame is None or frame.empty:
        return frame
    hit = frame.apply(
        lambda column: column.astype(str).str.casefold().str.contains(
            text, regex=False, na=False))
    return frame[hit.any(axis=1)]


def is_clock(column: pd.Series) -> bool:
    """A column of times of day, anchored to 1970 by :func:`prepare`."""
    if not pd.api.types.is_datetime64_any_dtype(column):
        return False
    stamps = column.dropna()
    return not stamps.empty and bool((stamps.dt.normalize() == _ANCHOR).all())


def filterable(frame: pd.DataFrame, formats: list[str]) -> pd.DataFrame:
    """The frame the filter controls should work against.

    Times of day are put back into words first. A reader ticking 09:15 off a
    list means the bucket in front of them, and offering them a date picker
    stuck on 1 January 1970 — which is what the anchor underneath a clock
    column really is — would be answering a question nobody asked.
    """
    out = frame.copy()
    for i, name in enumerate(out.columns):
        if is_clock(out[name]):
            spec = formats[i] if i < len(formats) else ""
            out[name] = out[name].dt.strftime(_strftime_for(spec))
    return out


def _strftime_for(spec: str) -> str:
    """The clock pattern a column's format asks for, defaulting to HH:MM:SS."""
    return spec if spec and "%" in spec else "%H:%M:%S"


def _range_control(column: pd.Series, index: int, key: str) -> None:
    """Two boxes, a floor and a ceiling — Excel's "greater than / less than"."""
    low, high = tf.bounds_of(column)
    dates = pd.api.types.is_datetime64_any_dtype(column)
    left, right = st.columns(2)
    if dates:
        with left:
            st.date_input("From", value=None, key=filter_key(key, index, "lo"))
        with right:
            st.date_input("To", value=None, key=filter_key(key, index, "hi"))
        return
    step = 1 if pd.api.types.is_integer_dtype(column) else None
    with left:
        st.number_input("At least", value=None, step=step,
                        placeholder=f"{low}", key=filter_key(key, index, "lo"),
                        label_visibility="collapsed")
    with right:
        st.number_input("At most", value=None, step=step,
                        placeholder=f"{high}", key=filter_key(key, index, "hi"),
                        label_visibility="collapsed")


def _controls(frame: pd.DataFrame, key: str) -> None:
    """One control per column, chosen by what the column holds."""
    for index, name in enumerate(frame.columns):
        column = frame[name]
        kind = tf.kind_of(column)
        st.caption(f"**{name}**")
        if kind == "pick":
            st.multiselect(
                name, tf.options_for(column), key=filter_key(key, index, "in"),
                placeholder="all", label_visibility="collapsed")
        elif kind == "contains":
            st.text_input(
                name, key=filter_key(key, index, "txt"),
                placeholder="contains…", label_visibility="collapsed")
        else:
            _range_control(column, index, key)


def _read_filters(frame: pd.DataFrame, key: str) -> dict:
    """What the controls currently say, as conditions the core can apply."""
    out: dict[str, tf.ColumnFilter] = {}
    for index, name in enumerate(frame.columns):
        low = st.session_state.get(filter_key(key, index, "lo"))
        high = st.session_state.get(filter_key(key, index, "hi"))
        if isinstance(low, date):
            low = pd.Timestamp(low)
        if isinstance(high, date):
            # A day named as a ceiling means all of it, not midnight at its
            # start — "up to the 3rd" that hides the 3rd is a trap.
            high = pd.Timestamp(high) + pd.Timedelta(days=1) - _ONE_TICK
        out[name] = tf.ColumnFilter(
            values=list(st.session_state.get(filter_key(key, index, "in")) or []),
            contains=st.session_state.get(filter_key(key, index, "txt")) or "",
            minimum=low, maximum=high)
    return out


def _clear(frame: pd.DataFrame, key: str) -> None:
    for index in range(len(frame.columns)):
        for part in ("in", "txt", "lo", "hi"):
            st.session_state.pop(filter_key(key, index, part), None)
    st.session_state.pop(search_key(key), None)


def render(pm, height_px: int, key: str) -> None:
    """One table, with its search box and its column filters above it.

    Falls back to the formatted rows where there is no typed frame — a table
    sliced for printing has one page's worth and nothing to sort.
    """
    if pm.title:
        st.markdown(f"**{pm.title}**")

    frame = pm.frame
    if frame is None:
        st.dataframe(
            {c: [r[i] for r in pm.rows] for i, c in enumerate(pm.columns)},
            use_container_width=True, hide_index=True, height=height_px)
        return

    # Prepared before either control, so a reader hunting for "14:30" is
    # matched against the value as it prints rather than the duration
    # underneath it, and the tick-lists offer the same words.
    frame, config = prepare(list(pm.columns), pm.column_formats, frame)

    # Worth the room once there is enough to hunt through. Below that the
    # controls would cost more of the page than the scrolling they save.
    if len(frame) <= 8:
        st.dataframe(frame, use_container_width=True, hide_index=True,
                     height=height_px, column_config=config)
        return

    against = filterable(frame, list(pm.column_formats or []))

    box, opener = st.columns([4, 1], vertical_alignment="bottom")
    with opener:
        # Rendered first so the count on the button is this run's, not the
        # one before it.
        with st.popover("Filters", use_container_width=True):
            _controls(against, key)
            filters = _read_filters(against, key)
            if tf.active_count(filters):
                st.button("Clear all", key=f"tbl_clear_{key}",
                          use_container_width=True,
                          on_click=_clear, args=(against, key))
    with box:
        query = st.text_input(
            "Search", key=search_key(key),
            placeholder="search every column…", label_visibility="collapsed")

    kept = tf.apply(against, filters)
    shown = matching(frame.loc[kept.index], query)

    live = tf.active_count(filters)
    if live or (query and len(shown) != len(frame)):
        told = tf.summary(against, filters)
        st.caption(f":gray[{len(shown):,} of {len(frame):,} rows"
                   + (f" · {told}" if told else "") + "]")

    st.dataframe(shown, use_container_width=True, hide_index=True,
                 height=height_px, column_config=config)
