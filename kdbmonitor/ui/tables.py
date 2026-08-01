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

import pandas as pd
import streamlit as st

# ",.0f" and friends: an optional thousands comma, then optional decimals.
_NUMERIC = re.compile(r"^(,)?\.?(\d+)?f$")


def search_key(widget_key: str) -> str:
    return f"tbl_q_{widget_key}"


def column_config(headers: list[str], formats: list[str],
                  frame: pd.DataFrame) -> dict:
    """How Streamlit should print each column, from the format it was given.

    Only the specs with a real counterpart are translated. A column whose
    format cannot be expressed is left alone rather than approximated, because
    a number shown plainly is honest and one shown in the wrong units is not.
    """
    config: dict = {}
    for header, spec in zip(headers, list(formats) + [""] * len(headers)):
        if not spec or header not in frame.columns:
            continue
        if not pd.api.types.is_numeric_dtype(frame[header]):
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
    return config


def matching(frame: pd.DataFrame, query: str) -> pd.DataFrame:
    """The rows mentioning ``query``, anywhere in them.

    One box across every column rather than one per column: a reader looking
    for an order is looking for a number they have in front of them, and does
    not yet know which column it sits in. Matched against each value as it
    prints, so searching "9:15" finds the bucket a reader can see rather than
    the timestamp underneath it.
    """
    text = (query or "").strip().casefold()
    if not text or frame is None or frame.empty:
        return frame
    hit = frame.apply(
        lambda column: column.astype(str).str.casefold().str.contains(
            text, regex=False, na=False))
    return frame[hit.any(axis=1)]


def render(pm, height_px: int, key: str) -> None:
    """One table, with its search box above it.

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

    # Worth a box once there is enough to hunt through. Below that the search
    # would cost more room on the page than the scrolling it saves.
    if len(frame) > 8:
        query = st.text_input(
            "Filter", key=search_key(key), placeholder="filter these rows…",
            label_visibility="collapsed")
        shown = matching(frame, query)
        if query and len(shown) != len(frame):
            st.caption(f":gray[{len(shown):,} of {len(frame):,} rows]")
    else:
        shown = frame

    st.dataframe(shown, use_container_width=True, hide_index=True,
                 height=height_px,
                 column_config=column_config(list(pm.columns),
                                             pm.column_formats, shown))
