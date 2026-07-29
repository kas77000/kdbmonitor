"""An uploaded file -> a validated DataFrame, or a refusal saying why.

Nothing here knows about Streamlit or about dashboards: it takes bytes and a
``FileShape`` and gives back a ``FileLoad``. Every assumption about the file
format lives in :func:`read_grid` and nowhere else, so widening the format later
is a change to one function.

The file's structure is never guessed. The header line is declared by whoever
built the dashboard, and a file whose header is somewhere else is refused rather
than searched for: an app that quietly decides a file is close enough does not
fail when it is wrong, it reports the wrong thing.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Optional

from kdbmonitor.core.dashboard_models import FileShape


def read_grid(data: bytes) -> list[list[str]]:
    """The file as a rectangular grid of strings, exactly as written.

    Padded to the widest row, because everything downstream addresses a cell by
    ``(row, col)`` and a ragged grid makes that address a lie. ``utf-8-sig``
    strips the byte-order mark Excel writes, which otherwise glues itself to the
    first header and makes that column unmatchable by name.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("this file is not UTF-8 text") from exc
    rows = [row for row in csv.reader(io.StringIO(text))]
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


@dataclass
class Problem:
    """One reason a file was refused, in the file's own terms.

    A refusal has to be actionable by whoever holds the file, so it names the
    column, says what was expected, and points at where to look.
    """
    message: str
    column: str = ""             # "" for problems about the file as a whole
    line: Optional[int] = None   # 1-based, along whichever axis records run


def _axis_word(shape: FileShape) -> str:
    """What a record is called in this file: a line, or a column.

    With headers running down the page each *column* is a record, so calling
    position 14 a "line" would send the reader to the wrong place entirely.
    """
    return "line" if shape.header_axis == "row" else "column"


def orient(grid: list[list[str]], axis: str) -> list[list[str]]:
    """The grid with headers running across it.

    Headers down the first column cost one transpose rather than a second set of
    rules: every index in ``FileShape`` refers to the grid after this.

    Squares the grid up first. ``zip`` stops at the shortest row, so transposing
    a ragged one drops whole records off the end without a word — the exact
    failure this module exists to prevent. :func:`read_grid` already pads, so
    this costs nothing on the ordinary path and makes the function safe to call
    with a grid from anywhere else.
    """
    if axis != "column":
        return grid
    width = max((len(row) for row in grid), default=0)
    return [list(row) for row in
            zip(*[row + [""] * (width - len(row)) for row in grid])]


def header_columns(grid: list[list[str]],
                   shape: FileShape) -> tuple[list[tuple[str, int]], list[Problem]]:
    """``(name, column index)`` for every named column on the declared line.

    The index is kept because dropping blank headers renumbers the columns, and
    the data underneath still lives at the original positions.
    """
    word = _axis_word(shape)
    if shape.header_row >= len(grid):
        return [], [Problem(
            f"this dashboard expects its headers on {word} "
            f"{shape.header_row + 1}, but the file has only {len(grid)} "
            f"{word}(s)")]

    row = grid[shape.header_row]
    found = [(str(name).strip(), i)
             for i, name in enumerate(row)
             if i >= shape.first_col and str(name).strip()]

    if not found:
        shown = ", ".join(c for c in row[:6] if str(c).strip()) or "(empty)"
        return [], [Problem(
            f"this dashboard expects its headers on {word} "
            f"{shape.header_row + 1}; that {word} of your file is blank: {shown}")]

    duplicates = sorted({n for n, _ in found
                         if [x for x, _ in found].count(n) > 1})
    if duplicates:
        return [], [Problem(
            f"two columns are called '{d}' — rename one, or neither can be "
            f"referenced", column=d) for d in duplicates]

    expected = {c.name for c in shape.columns if c.required}
    if expected and not expected & {n for n, _ in found}:
        shown = ", ".join(n for n, _ in found[:6])
        return found, [Problem(
            f"this dashboard expects its headers on {word} "
            f"{shape.header_row + 1}; that {word} of your file reads: {shown}")]

    return found, []


def data_records(grid: list[list[str]], shape: FileShape,
                 columns: list[tuple[str, int]]) -> tuple[list[tuple[int, list[str]]], int]:
    """The table's rows as ``(1-based file position, cells)``, and how many were
    blank.

    The position is carried rather than recomputed because blank rows are
    dropped: a refusal has to point into the file the reader has open, not into
    the rows that happened to survive.

    Blankness is judged only on the columns being taken. A row empty across the
    table but carrying a note in some column this dashboard ignores is still an
    empty row — the note is not data anyone asked for.

    ``data_start`` is clamped to zero before it drives the loop: a negative
    value is not out of range to Python's list indexing, it is a request to
    read backwards from the end of the grid, which would splice the header
    back in as a "data" row under a nonsensical line number instead of failing
    loudly.
    """
    records: list[tuple[int, list[str]]] = []
    skipped = 0
    for offset in range(max(0, shape.data_start), len(grid)):
        row = grid[offset]
        cells = [row[i] if i < len(row) else "" for _, i in columns]
        if not any(str(c).strip() for c in cells):
            skipped += 1
            continue
        records.append((offset + 1, cells))
    return records, skipped
