"""Reading one table as a tree: the rows sharing a value, gathered under it.

A table is a flat list, and a flat list is the wrong shape for a question people
ask of one constantly — what did this basket do, which orders are on this venue,
what is under this trader. The rows are all there, scattered down a column
somebody has to scan for. Grouping gathers them: one heading per distinct value,
its rows underneath it, and every other heading folded away until it is asked
for.

Which column that is belongs to whoever is reading, not to whoever built the
dashboard. The same table gets read by venue at nine, by symbol at ten and by
basket when something goes wrong at half past, and having to open the editor to
ask the next question is what makes people export to Excel and stop looking at
the dashboard. So the author's choice is where a reader starts, never where they
are stuck — see :func:`kdbmonitor.ui.tables.render`, which keeps the reader's
choice across a refresh the same way it keeps their filters.

Nothing here imports Streamlit. What a tree looks like is the UI's business;
which rows sit under which heading is decided here, where it can be tested.
"""
from __future__ import annotations

import pandas as pd

# Above this many headings a tree stops being one. Well under
# ``tablefilter.MAX_PICKABLE`` on purpose: 200 values is a long tick-list but
# still a list you scan, whereas 200 headings holding one row each is the table
# you started with, plus a fold between every row and twice the scrolling.
MAX_GROUPS = 50

# What a heading says for the rows whose value is missing. The em dash
# ``plotmodel._fmt`` prints in a cell, so a null reads the same in both places.
MISSING = "—"


def label_of(value) -> str:
    """One heading's text: the value as it reads, not as it is stored."""
    try:
        if value is None or pd.isna(value):
            return MISSING
    except (TypeError, ValueError):        # arrays and the like are never null
        pass
    if isinstance(value, pd.Timestamp):
        # Midnight on the dot is a date, not an instant. A trade-date column
        # headed "2026-08-05 00:00:00" says the same thing at twice the width,
        # and the extra half is always zeroes.
        if value == value.normalize():
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def labels(column: pd.Series) -> pd.Series:
    """Every row's heading, as text, in the order the rows are in."""
    return column.map(label_of)


def group_count(column: pd.Series) -> int:
    """How many headings this column would make, counting the missing one."""
    return int(column.nunique(dropna=True)) + int(bool(column.isna().any()))


def groupable(frame: pd.DataFrame) -> list[str]:
    """The columns worth offering as headings, in the order they print.

    Two columns are left off. One with more distinct values than
    :data:`MAX_GROUPS` would make a tree nobody can read, and one with a
    distinct value in every row would put a fold between every row and call it
    a grouping — an order-id column is not a heading, it is the table.

    Only what the data can bear right now, which is a shifting list: a basket
    column with eleven baskets in it this minute may have four hundred by the
    close. That is why the reader's own choice is offered whether or not it is
    on this list — losing a grouping to a busy minute would be the same quiet
    rewrite that :func:`kdbmonitor.core.tablefilter.options_with` exists to
    prevent.
    """
    if frame is None or getattr(frame, "empty", True):
        return []
    rows = len(frame)
    out: list[str] = []
    seen: set[str] = set()
    for i, header in enumerate(frame.columns):
        name = str(header)
        if name in seen:            # two columns under one header: neither is
            continue                # addressable by name, so offer neither
        seen.add(name)
        count = group_count(frame.iloc[:, i])
        if count < 1 or count > MAX_GROUPS:
            continue
        if rows > 1 and count == rows:
            continue
        out.append(name)
    return out


def split(frame: pd.DataFrame, keys) -> list[tuple[str, pd.DataFrame]]:
    """``frame``'s rows under their headings, in the order the headings appear.

    ``keys`` is one heading per row, positionally — row *i* of the frame belongs
    under ``keys[i]``. Positional rather than matched on the index because a
    frame that has been through a transform can carry a repeated index, and
    aligning on one of those either raises or, worse, multiplies the rows.

    Appearance order rather than alphabetical: the dataset arrived sorted by
    somebody's ``xdesc`` and the tree has no business overruling it — the
    busiest venue stays where the query put it, at the top. The missing heading
    is the one exception and always comes last, because rows with no value are
    not an answer to the question the tree is being asked.
    """
    if frame is None or keys is None or len(frame) == 0:
        return []
    marks = [str(k) for k in keys]
    if len(marks) != len(frame):
        return []
    where: dict[str, list[int]] = {}
    for i, mark in enumerate(marks):
        where.setdefault(mark, []).append(i)
    order = [k for k in where if k != MISSING]
    if MISSING in where:
        order.append(MISSING)
    return [(k, frame.iloc[where[k]]) for k in order]
