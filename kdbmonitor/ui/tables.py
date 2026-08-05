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
from kdbmonitor.core import tablegroup as tg
from kdbmonitor.core.plotmodel import format_time_of_day

ROW_PX = 35          # Streamlit's data-editor row height, header included


def fitted_height(n_rows: int, allotted_px: int) -> int:
    """Height in pixels for st.dataframe: what the rows need, capped by the slot.

    Forcing the row's printed height on a short table pads it with blank filler
    rows, which reads as missing data. So a table that fits gets exactly the
    height its rows come to, and only one taller than its slot is constrained —
    there the cap is what makes it scroll rather than lose rows.

    A pixel count rather than st.dataframe's "content", which Streamlit only
    learned in 1.46: this is the same number, arrived at here instead of there,
    and every version takes it. An empty table keeps a row's worth of room, so
    its empty state has somewhere to print.

    Lives here rather than beside the dashboard page because a group inside a
    tree is a table too, and the two have to be sized by the same rule.
    """
    return min(ROW_PX * (max(n_rows, 1) + 1) + 3, allotted_px)

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


def gen_key(widget_key: str) -> str:
    return f"tbl_gen_{widget_key}"


def generation(widget_key: str) -> int:
    """How many times this table's controls have been cleared.

    It rides on every control's key, so clearing gives them all new keys. That
    is the only thing a browser accepts as "start again": a widget keeps what
    it holds under an id it has seen before, and re-sends it on the next rerun
    — see :func:`_clear`.
    """
    return int(st.session_state.get(gen_key(widget_key), 0))


def _generational(name: str, gen: int) -> str:
    """A key that is unchanged until the first clear, so nothing stored under
    the original name is orphaned by adding this."""
    return name if not gen else f"{name}_g{gen}"


def search_key(widget_key: str, gen: int = 0) -> str:
    return _generational(f"tbl_q_{widget_key}", gen)


# What the group-by picker says for a flat table. A word rather than an empty
# option, because "" in a selectbox reads as a rendering fault.
NO_GROUP = "(none)"


def group_key(widget_key: str) -> str:
    return f"tbl_g_{widget_key}"


def group_frame_key(widget_key: str, label: str) -> str:
    """One heading's own table, named for the heading rather than its place.

    Hashed and named like :func:`filter_key`, and for the same reason: a
    refresh can bring the headings back in another order, and a key tied to
    position would hand one group's scroll and column sort to whichever group
    landed in its slot.
    """
    token = hashlib.md5(str(label).encode("utf-8")).hexdigest()[:10]
    return f"tbl_grp_{token}_{widget_key}"


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


def filter_key(widget_key: str, column: str, part: str, gen: int = 0) -> str:
    """A filter control's key, named for its column rather than its position.

    Hashed because a header can hold spaces, dots and anything else a query
    returned. Named for the column and not, as it once was, for where the
    column sits: a refresh is free to bring its columns back in another order
    or to bring back a column that a parameter had taken away, and a positional
    key would hand one column's condition to whichever column landed in its
    place.

    ``gen`` is this table's clear count — see :func:`generation`.
    """
    token = hashlib.md5(str(column).encode("utf-8")).hexdigest()[:10]
    return _generational(f"tbl_f{part}_{token}_{widget_key}", gen)


def _stored(widget_key: str) -> dict:
    """This table's remembered controls: what was typed, not what it meant.

    ``group`` is three-valued on purpose. ``None`` is nobody has chosen yet, so
    the author's grouping stands; ``""`` is a reader who chose a flat table, and
    it has to outlast a refresh or the author's choice would spring back every
    few seconds under somebody who had just turned it off.
    """
    held = st.session_state.setdefault(
        state_key(widget_key), {"vals": {}, "query": "", "group": None})
    held.setdefault("group", None)
    return held


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
    for i in range(len(out.columns)):
        column = out.iloc[:, i]
        shown = as_shown(column, _format_at(formats, i))
        if shown is not column:
            out.isetitem(i, shown)
    return out


def _format_at(formats: list[str], i: int) -> str:
    return formats[i] if i < len(formats) else ""


def as_shown(column: pd.Series, spec: str) -> pd.Series:
    """One column the way it prints — a clock as its time, not as its anchor.

    Everything else is handed back untouched, so a caller can put a whole frame
    or a single column through it and get the same answer either way. That
    matters where a grouping heading is taken from one column of a frame the
    reader is looking at: the heading has to say 09:15, which is what the cell
    under it says, rather than the 1970 timestamp holding it.
    """
    if not is_clock(column):
        return column
    return column.dt.strftime(_strftime_for(spec))


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
    gen = generation(key)
    left, right = st.columns(2)
    if dates:
        with left:
            st.date_input("From", value=raw.get("lo"),
                          key=filter_key(key, name, "lo", gen))
        with right:
            st.date_input("To", value=raw.get("hi"),
                          key=filter_key(key, name, "hi", gen))
        return
    step = 1 if pd.api.types.is_integer_dtype(column) else None
    with left:
        st.number_input("At least", value=raw.get("lo"), step=step,
                        placeholder=f"{low}",
                        key=filter_key(key, name, "lo", gen),
                        label_visibility="collapsed")
    with right:
        st.number_input("At most", value=raw.get("hi"), step=step,
                        placeholder=f"{high}",
                        key=filter_key(key, name, "hi", gen),
                        label_visibility="collapsed")


def _controls(frame: pd.DataFrame, key: str, stored: dict) -> None:
    """One control per column, chosen by what the column holds and seeded by
    what was last typed into it."""
    gen = generation(key)
    for name in frame.columns:
        column = frame[name]
        raw = stored["vals"].get(name) or tf.blank()
        kind = tf.control_kind(column, raw)
        st.caption(f"**{name}**")
        if kind == "pick":
            st.multiselect(
                name, tf.options_with(column, raw.get("in")),
                default=list(raw.get("in") or []),
                key=filter_key(key, name, "in", gen),
                placeholder="all", label_visibility="collapsed")
        elif kind == "contains":
            st.text_input(
                name, value=raw.get("txt") or "",
                key=filter_key(key, name, "txt", gen),
                placeholder="contains…", label_visibility="collapsed")
        else:
            _range_control(column, name, key, raw)


def _group_choice(stored: dict, pm) -> str:
    """The column this table is meant to be gathered under, asked for or not.

    The author's ``group_by`` is a starting point: it applies until somebody
    reading picks their own, and after that theirs is the answer — including
    when theirs is "no grouping at all", which is why the store holds ``""``
    and ``None`` as different things.
    """
    chosen = stored.get("group")
    return (getattr(pm, "group_by", "") or "") if chosen is None else chosen


def _grouped_on(stored: dict, pm, frame: pd.DataFrame) -> str:
    """The same choice, once this snapshot has been asked whether it can honour
    it — '' where it cannot, which is a flat table.

    A choice naming a column that is not here is remembered rather than
    honoured, so a column a parameter took away brings its grouping back with
    it, exactly as a tick survives a snapshot with no BUYs in it.
    """
    chosen = _group_choice(stored, pm)
    # Exactly one, never merely present: two columns sharing a display header
    # cannot be told apart by name, and picking one of them at random is worse
    # than not grouping.
    return chosen if list(frame.columns).count(chosen) == 1 else ""


def _group_control(container, offered: list[str], key: str,
                   current: str) -> None:
    """The picker that says which column the rows are gathered under.

    Seeded rather than read: like every other control over a table, what it
    holds is put back from the durable store, because Streamlit deletes the
    value of a widget it did not draw and a refresh can take this one off the
    page for a run.
    """
    options = [NO_GROUP] + list(offered)
    if current and current not in options:
        options.append(current)
    container.selectbox(
        "Group by", options, key=group_key(key),
        index=options.index(current) if current in options else 0,
        help="Gather the rows under the values of one column — every order on "
             "a venue, every fill in a basket — and fold the rest away. Yours "
             "to change whenever the question changes; it narrows nothing, so "
             "every row is still here. The printed page stays a flat list.")


def _draw_groups(parts: list, config: dict, height_px: int, key: str,
                 open_all: bool) -> None:
    """The tree: one heading per group, its rows underneath.

    Headings start open only while there are few enough of them to read at
    once, or while something is narrowing the table. That second case is the
    one that matters: a reader who has just searched is being told "3 of 900
    rows", and three rows behind three closed doors is that line telling the
    truth and showing nothing.
    """
    for label, part in parts:
        with st.expander(f"{label}  ·  {len(part):,}", expanded=open_all):
            if not len(part.columns):
                # Grouped by the only column it has. The heading and its count
                # are the whole answer; an empty frame under it would just be
                # a box of nothing.
                continue
            st.dataframe(part, use_container_width=True, hide_index=True,
                         height=fitted_height(len(part), height_px),
                         column_config={c: config[c] for c in part.columns
                                        if c in config},
                         key=group_frame_key(key, label))


# Above this many headings a tree opened all at once is a wall rather than a
# summary, so they start folded and the reader opens what they came for.
OPEN_UP_TO = 6


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
    gen = generation(key)
    if search_key(key, gen) in st.session_state:
        stored["query"] = st.session_state[search_key(key, gen)] or ""
    if group_key(key) in st.session_state:
        chosen = st.session_state[group_key(key)]
        stored["group"] = "" if chosen == NO_GROUP else (chosen or "")
    for name in frame.columns:
        raw = dict(stored["vals"].get(name) or tf.blank())
        for part in tf.PARTS:
            widget = filter_key(key, name, part, gen)
            if widget not in st.session_state:
                continue
            value = st.session_state[widget]
            raw[part] = list(value or []) if part == "in" else value
        stored["vals"][name] = raw


def _clear(frame: pd.DataFrame, key: str) -> None:
    """Put every row back: forget what was typed, and re-key what typed it.

    Emptying the store is only half of it. The controls on screen keep what
    they are holding — a browser remembers a widget by its id, and re-sends
    that value on the next rerun — so a search somebody had just cleared came
    back on their very next click, and a ticked venue came back with it, the
    table narrowing again under a button that had reported putting every row
    back. Deleting the keys server-side does not help: the browser is not
    asking.

    A closed Filters popover makes it worse rather than better. Its controls
    are gone from the page while their values are still remembered, so there
    is nothing on screen to correct and nothing in session state to find.

    Bumping the generation gives every control a key it has never had, which
    is the one thing a browser reads as "this is a new control, I have nothing
    for it". The grouping is deliberately left alone: it hides no rows, so
    "put every row back" has nothing to say about it, and a reader who had
    turned the author's grouping off would otherwise find it back on for having
    cleared a filter.
    """
    st.session_state[gen_key(key)] = generation(key) + 1
    held = st.session_state.pop(state_key(key), None) or {}
    st.session_state[state_key(key)] = {"vals": {}, "query": "",
                                        "group": held.get("group")}
    # The grouping outlives the clear. It hides no rows, so "put every row
    # back" has nothing to say about it — and a reader who had turned the
    # author's grouping off would otherwise find it back on for having cleared
    # a filter.
    held = st.session_state.pop(state_key(key), None) or {}
    st.session_state[state_key(key)] = {"vals": {}, "query": "",
                                        "group": held.get("group")}


def render(pm, height_px: int, key: str) -> None:
    """One table, with its search box, its grouping and its column filters.

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
    # A grouped table keeps its controls for the same reason: the picker that
    # gathered the rows under a heading is the only one that can flatten them
    # out again, whatever the row count is this second.
    narrowed = (tf.active_count(tf.filters_from(stored["vals"]))
                or stored["query"] or _group_choice(stored, pm))
    if len(frame) <= 8 and not narrowed:
        st.dataframe(frame, use_container_width=True, hide_index=True,
                     height=height_px, column_config=config)
        return

    against = filterable(frame, list(pm.column_formats or []))

    _remember(against, key, stored)
    filters = tf.filters_from(stored["vals"])
    live = tf.active_count(filters)

    asked = _group_choice(stored, pm)
    grouped = _grouped_on(stored, pm, frame)
    # Read off the whole snapshot rather than off the rows a filter left, so
    # narrowing a table never takes away the column you were about to group it
    # by. The picker earns its place on the row only where there is something
    # to gather the rows under, or where somebody has already asked for one.
    offered = tg.groupable(against)
    if offered or asked:
        box, tree, opener = st.columns([3, 1.5, 1], vertical_alignment="bottom")
        _group_control(tree, offered, key, asked)
    else:
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
            "Search", value=stored["query"], key=search_key(key, generation(key)),
            placeholder="search every column…", label_visibility="collapsed")

    kept = tf.apply(against, filters)
    shown = matching(frame.loc[kept.index], query)

    # Grouped against the values as they print, taken from the rows on screen
    # rather than from the whole frame: the heading over 09:15 has to say
    # 09:15, and a tree drawn from rows a filter removed would be a tree of
    # empty headings.
    parts = []
    if grouped:
        spec = _format_at(list(pm.column_formats or []),
                          list(frame.columns).index(grouped))
        parts = tg.split(shown.drop(columns=[grouped]),
                         tg.labels(as_shown(shown[grouped], spec)))
    # More headings than a tree can carry. The rows are all still here, so they
    # are shown as the list they were, and the reason is said out loud — a
    # grouping that quietly did nothing would read as a broken picker.
    crowded = len(parts) > tg.MAX_GROUPS

    narrowing = bool(live or (query and len(shown) != len(frame)))
    if narrowing or grouped:
        told = tf.summary(against, filters)
        said = [f"{len(shown):,} of {len(frame):,} rows" if narrowing
                else f"{len(shown):,} rows"]
        if grouped:
            said.append(f"{len(parts):,} "
                        + ("group" if len(parts) == 1 else "groups")
                        + f" by {grouped}"
                        + (" — too many to fold, so they are listed"
                           if crowded else ""))
        if told:
            said.append(told)
        # The undo sits beside the line that reports the narrowing, because
        # that line is where somebody notices rows are missing. Reaching it
        # through the popover meant opening the filters to stop filtering, and
        # then clearing them one column at a time if you did not spot the
        # button at the bottom.
        note, undo = st.columns([5, 1], vertical_alignment="center")
        note.caption(f":gray[{' · '.join(said)}]")
        if narrowing:
            undo.button("Clear all", key=f"tbl_clearall_{key}", type="tertiary",
                        icon=":material/filter_alt_off:",
                        use_container_width=True,
                        help="Put every row back — clears every column filter "
                             "and the search box",
                        on_click=_clear, args=(against, key))

    if parts and not crowded:
        _draw_groups(parts, config, height_px, key,
                     open_all=narrowing or len(parts) <= OPEN_UP_TO)
        return

    st.dataframe(shown, use_container_width=True, hide_index=True,
                 height=height_px, column_config=config)
