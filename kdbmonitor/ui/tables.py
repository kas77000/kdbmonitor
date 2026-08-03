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

import hashlib
import re

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


def state_key(widget_key: str) -> str:
    """Where this table's filters are kept between runs.

    Streamlit's own widget state cannot be that place. It owns it: a widget
    that is not drawn on a run has its value deleted, and a tick-list quietly
    drops a tick for a value its current options no longer offer. Both happen
    on an ordinary refresh — a snapshot with too few rows to be worth filtering
    takes the controls off the page for one run and every filter with them, and
    a snapshot without a BUY in it unticks BUY. So what somebody typed is kept
    here, where nothing but this table's own Clear button removes it, and the
    controls are drawn from it rather than being it.
    """
    return f"tbl_state_{widget_key}"


def filter_key(widget_key: str, column: str, part: str) -> str:
    """A filter control's key, named for its column rather than its position.

    Hashed because a header can hold spaces, dots and anything else a query
    returned. Named for the column and not, as it once was, for where the
    column sits: a refresh is free to bring its columns back in another order
    or to bring back a column that a parameter had taken away, and a positional
    key would hand one column's condition to whichever column landed in its
    place.
    """
    token = hashlib.md5(str(column).encode("utf-8")).hexdigest()[:10]
    return f"tbl_f{part}_{token}_{widget_key}"


def _stored(widget_key: str) -> dict:
    """This table's remembered controls: what was typed, not what it meant."""
    return st.session_state.setdefault(
        state_key(widget_key), {"vals": {}, "query": ""})


def forget(key_prefix: str) -> None:
    """Drop every table filter belonging to widgets under ``key_prefix``.

    For a closed dashboard tab. The remembered filters outlive the widgets on
    purpose, which means that unlike Streamlit's own state they do not go when
    the table does — so closing a tab has to say so.
    """
    dead = [k for k in list(st.session_state)
            if k.startswith("tbl_") and f"_{key_prefix}_" in k]
    for k in dead:
        st.session_state.pop(k, None)


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


# The widths Streamlit's column config understands. A dashboard is stored data
# and can be hand-edited, so anything else is left to fall back to the automatic
# width rather than handed to Streamlit, which refuses the whole table over one
# bad value.
WIDTHS = ("small", "medium", "large")


def prepare(headers: list[str], formats: list[str], frame: pd.DataFrame,
            widths: list[str] | None = None
            ) -> tuple[pd.DataFrame, dict]:
    """The frame Streamlit should draw, and the config to draw it with.

    Only the specs with a real counterpart are translated. A column whose
    format cannot be expressed is left alone rather than approximated, because
    a number shown plainly is honest and one shown in the wrong units is not.

    The frame comes back changed in one case only — a duration column becomes
    the clock time it stands for — because that column has no honest default
    to fall back to.

    ``widths`` are the author's per-column widths, positional like ``formats``.
    They are applied last, over whatever config the format needed, so a column
    can be both formatted and narrowed — the two are separate questions and the
    format branches above have no business knowing about the second.
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

    for header, width in zip(headers, list(widths or []) + [""] * len(headers)):
        if width not in WIDTHS or header not in frame.columns:
            continue
        if header in config:
            config[header]["width"] = width
        else:
            config[header] = st.column_config.Column(width=width)
    return out, config


def column_config(headers: list[str], formats: list[str],
                  frame: pd.DataFrame, widths: list[str] | None = None) -> dict:
    """Just the config half of :func:`prepare`."""
    return prepare(headers, formats, frame, widths)[1]


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


def _range_control(column: pd.Series, name: str, key: str, raw: dict) -> None:
    """Two boxes, a floor and a ceiling — Excel's "greater than / less than".

    ``raw`` seeds them. Streamlit ignores a seed for a control it is already
    holding a value for, which is exactly right: it matters only on the run
    after a refresh took the control off the page, where it is what puts the
    filter back.
    """
    low, high = tf.bounds_of(column)
    dates = pd.api.types.is_datetime64_any_dtype(column)
    left, right = st.columns(2)
    if dates:
        with left:
            st.date_input("From", value=raw.get("lo"),
                          key=filter_key(key, name, "lo"))
        with right:
            st.date_input("To", value=raw.get("hi"),
                          key=filter_key(key, name, "hi"))
        return
    step = 1 if pd.api.types.is_integer_dtype(column) else None
    with left:
        st.number_input("At least", value=raw.get("lo"), step=step,
                        placeholder=f"{low}", key=filter_key(key, name, "lo"),
                        label_visibility="collapsed")
    with right:
        st.number_input("At most", value=raw.get("hi"), step=step,
                        placeholder=f"{high}", key=filter_key(key, name, "hi"),
                        label_visibility="collapsed")


def _controls(frame: pd.DataFrame, key: str, stored: dict) -> None:
    """One control per column, chosen by what the column holds and seeded by
    what was last typed into it."""
    for name in frame.columns:
        column = frame[name]
        raw = stored["vals"].get(name) or tf.blank()
        kind = tf.control_kind(column, raw)
        st.caption(f"**{name}**")
        if kind == "pick":
            st.multiselect(
                name, tf.options_with(column, raw.get("in")),
                default=list(raw.get("in") or []),
                key=filter_key(key, name, "in"),
                placeholder="all", label_visibility="collapsed")
        elif kind == "contains":
            st.text_input(
                name, value=raw.get("txt") or "",
                key=filter_key(key, name, "txt"),
                placeholder="contains…", label_visibility="collapsed")
        else:
            _range_control(column, name, key, raw)


def _remember(frame: pd.DataFrame, key: str, stored: dict) -> None:
    """Copy what the controls now say back into the durable store.

    Called before they are drawn again, not after: a widget's new value arrives
    with the rerun it caused, so reading it first is what lets the count on the
    Filters button be this run's rather than the one before it.

    A part is copied only where Streamlit still has that control, so a column
    whose control changed shape — a tick-list that became a contains box, a
    number column that came back as text — keeps the condition somebody set
    rather than having it wiped by the absence of the box that set it. The way
    out of one of those is Clear all, which empties both.
    """
    if search_key(key) in st.session_state:
        stored["query"] = st.session_state[search_key(key)] or ""
    for name in frame.columns:
        raw = dict(stored["vals"].get(name) or tf.blank())
        for part in tf.PARTS:
            widget = filter_key(key, name, part)
            if widget not in st.session_state:
                continue
            value = st.session_state[widget]
            raw[part] = list(value or []) if part == "in" else value
        stored["vals"][name] = raw


def _clear(frame: pd.DataFrame, key: str) -> None:
    for name in frame.columns:
        for part in tf.PARTS:
            st.session_state.pop(filter_key(key, name, part), None)
    st.session_state.pop(search_key(key), None)
    st.session_state.pop(state_key(key), None)


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
    frame, config = prepare(list(pm.columns), pm.column_formats, frame,
                            pm.column_widths)

    stored = _stored(key)

    # Worth the room once there is enough to hunt through. Below that the
    # controls would cost more of the page than the scrolling they save —
    # unless something is already narrowed, and then they have to stay whatever
    # the row count is. A refresh that briefly returns four rows must not take
    # away the only control that can undo the filter which is hiding the rest.
    narrowed = tf.active_count(tf.filters_from(stored["vals"])) or stored["query"]
    if len(frame) <= 8 and not narrowed:
        st.dataframe(frame, use_container_width=True, hide_index=True,
                     height=height_px, column_config=config)
        return

    against = filterable(frame, list(pm.column_formats or []))

    _remember(against, key, stored)
    filters = tf.filters_from(stored["vals"])
    live = tf.active_count(filters)

    box, opener = st.columns([4, 1], vertical_alignment="bottom")
    with opener:
        # The count rides on the button, so a table that is being narrowed says
        # so before anybody opens anything. A popover is a closed door, and a
        # closed door cannot report what is behind it.
        with st.popover(f"Filters ({live})" if live else "Filters",
                        use_container_width=True):
            _controls(against, key, stored)
            if live:
                st.button("Clear all", key=f"tbl_clear_{key}",
                          use_container_width=True,
                          on_click=_clear, args=(against, key))
    with box:
        query = st.text_input(
            "Search", value=stored["query"], key=search_key(key),
            placeholder="search every column…", label_visibility="collapsed")

    kept = tf.apply(against, filters)
    shown = matching(frame.loc[kept.index], query)

    if live or (query and len(shown) != len(frame)):
        told = tf.summary(against, filters)
        # The undo sits beside the line that reports the narrowing, because
        # that line is where somebody notices rows are missing. Reaching it
        # through the popover meant opening the filters to stop filtering, and
        # then clearing them one column at a time if you did not spot the
        # button at the bottom.
        note, undo = st.columns([5, 1], vertical_alignment="center")
        note.caption(f":gray[{len(shown):,} of {len(frame):,} rows"
                     + (f" · {told}" if told else "") + "]")
        undo.button("Clear all", key=f"tbl_clearall_{key}", type="tertiary",
                    icon=":material/filter_alt_off:", use_container_width=True,
                    help="Put every row back — clears every column filter and "
                         "the search box",
                    on_click=_clear, args=(against, key))

    st.dataframe(shown, use_container_width=True, hide_index=True,
                 height=height_px, column_config=config)
