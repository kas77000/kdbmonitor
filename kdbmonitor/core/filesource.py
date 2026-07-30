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
import math
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

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


_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}


def null_set(shape: FileShape) -> set[str]:
    """The file's markers for "missing", folded for comparison."""
    return {str(m).strip().lower() for m in shape.null_markers}


def is_blank(value: Any, markers: set[str]) -> bool:
    """Whether this cell says nothing.

    Whitespace-only counts: a cell holding two spaces is a cell somebody left
    empty, and treating it as text would put "  " in a number column.
    """
    return str(value).strip().lower() in markers


def _to_number(text: str) -> float:
    """Commas and spaces are how a person writes a number, not part of it.

    ``float()`` turns an overflow like "1e400", or a literal "inf", into
    ``inf`` rather than raising — so that has to be caught here. Left alone it
    would sit in a number column formatting and aggregating like any other
    value, which is the same silent-infinity failure ``transform.
    _no_infinities`` exists to strip out of a derived column; a file should
    not be able to smuggle the same thing in from the other end.
    """
    value = float(text.replace(",", "").replace(" ", ""))
    if not math.isfinite(value):
        raise ValueError(f"{text} is not a finite number")
    return value


def _to_integer(text: str) -> int:
    value = _to_number(text)
    if value != int(value):
        raise ValueError(f"{text} is not a whole number")
    return int(value)


def _to_boolean(text: str) -> bool:
    folded = text.strip().lower()
    if folded in _TRUE:
        return True
    if folded in _FALSE:
        return False
    raise ValueError(f"{text} is not true or false")


def _to_date(text: str):
    value = pd.to_datetime(text, errors="raise")
    if pd.isna(value):
        raise ValueError(f"{text} is not a date")
    return value


_READERS = {"number": _to_number, "integer": _to_integer,
            "boolean": _to_boolean, "date": _to_date, "text": lambda t: t}


def read_values(cells: list[str], type_name: str,
                markers: set[str]) -> tuple[pd.Series, list[tuple[int, str]]]:
    """A column read as ``type_name``, plus every value that would not read.

    Checking by *reading* rather than by inferring a type and comparing labels
    is what makes integers-where-numbers-were-expected work without a special
    case, and it is what lets a refusal quote the value that broke.

    An unrecognised type name reads as text rather than raising: it can only
    come from a hand-edited bundle, and refusing every row of a column because
    its declared type is misspelt helps nobody.
    """
    reader = _READERS.get(type_name, _READERS["text"])
    out: list[Any] = []
    failures: list[tuple[int, str]] = []
    for i, cell in enumerate(cells):
        text = str(cell).strip()
        if is_blank(text, markers):
            out.append(None)
            continue
        try:
            out.append(reader(text))
        except (ValueError, TypeError, OverflowError, pd.errors.ParserError):
            out.append(None)
            failures.append((i, text))

    if type_name == "date":
        return (pd.to_datetime(pd.Series(out, dtype="object"), errors="coerce"),
                failures)
    dtype = {"number": "float64", "integer": "Int64",
             "boolean": "boolean"}.get(type_name, "object")
    return pd.Series(out, dtype=dtype), failures


@dataclass
class FileLoad:
    """What came of reading one file: a frame, or the reasons there is none."""
    df: Optional[pd.DataFrame] = None       # None when refused
    cells: dict[str, Any] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.df is not None and not self.problems


def read_cells(grid: list[list[str]], shape: FileShape) -> dict[str, Any]:
    """Every named cell, read from the raw grid by its own coordinates.

    Called before :func:`orient` and before the table is cut, because a named
    cell addresses the file as it sits on disk — which is the grid the designer
    was looking at when they pointed at it. Were orientation to apply here too,
    switching a dataset to vertical headers would silently move every cell
    already named.

    A cell that is missing, out of range or unreadable as its type is null
    rather than a refusal. These describe the report — a date in its title bar —
    and a report whose caption did not parse is still a report. Refusing the
    upload over one would be refusing the data because of the label on it.

    A negative address is treated as out of range, not as Python's
    count-from-the-end: nobody points at "one row up from the top", and reading
    the last line of the file instead would be a confident wrong answer.
    """
    markers = null_set(shape)
    out: dict[str, Any] = {}
    for cell in shape.cells:
        raw = ""
        if 0 <= cell.row < len(grid) and 0 <= cell.col < len(grid[cell.row]):
            raw = grid[cell.row][cell.col]
        values, _ = read_values([raw], cell.type, markers)
        value = values.iloc[0]
        out[cell.name] = None if pd.isna(value) else value
    return out


def load(data: bytes, shape: FileShape) -> FileLoad:
    """An uploaded file read against ``shape``: a frame, or a refusal."""
    try:
        grid = read_grid(data)
    except ValueError as exc:
        return FileLoad(problems=[Problem(str(exc))])
    return load_grid(grid, shape)


def load_grid(grid: list[list[str]], shape: FileShape) -> FileLoad:
    """A grid read against ``shape``: a frame, or a refusal.

    Split from :func:`load` so the shape editor can check the sample it already
    holds without serialising it back to CSV and parsing it again — and, more to
    the point, so what the designer sees while building is produced by the very
    function a viewer's upload will go through, refusals included.

    Any problem refuses the whole file. A partly loaded frame would be worse
    than none, because it looks like data — and a report drawn from it is wrong
    without ever saying so.

    Every problem is collected rather than the first raised, so one upload
    produces one list of everything to fix.
    """
    if not grid:
        return FileLoad(problems=[Problem("this file is empty")])

    # A data region starting on or above the header line is a broken contract
    # rather than a bad file: read on and the header itself becomes a record,
    # reported as whatever its labels happened to coerce to. Nothing the person
    # holding the file can do about it, so say what is actually wrong.
    if shape.data_start <= shape.header_row:
        word = _axis_word(shape)
        return FileLoad(problems=[Problem(
            f"this dashboard is configured to read data from {word} "
            f"{shape.data_start + 1}, on or above its own header {word} "
            f"{shape.header_row + 1} — its shape needs fixing, not your file")])

    cells = read_cells(grid, shape)
    grid = orient(grid, shape.header_axis)

    columns, problems = header_columns(grid, shape)
    if problems:
        return FileLoad(cells=cells, problems=problems)

    present = {name: index for name, index in columns}
    notes: list[str] = []

    missing = [c.name for c in shape.columns
               if c.required and c.name not in present]
    if missing:
        arrived = ", ".join(present) or "(nothing)"
        problems += [Problem(f"missing required column '{name}' — the file "
                             f"has: {arrived}", column=name)
                     for name in missing]

    wanted = [c for c in shape.columns if c.name in present]
    taken = [(c.name, present[c.name]) for c in wanted]
    extra = [n for n in present if n not in {c.name for c in shape.columns}]
    if extra:
        notes.append(f"ignored {len(extra)} column(s) this dashboard does not "
                     f"use: {', '.join(extra)}")

    records, skipped = data_records(grid, shape, taken)
    if skipped:
        notes.append(f"skipped {skipped} blank row(s)")

    markers = null_set(shape)
    word = _axis_word(shape)
    frame: dict[str, pd.Series] = {}
    for position, spec in enumerate(wanted):
        raw = [cells_of[position] for _, cells_of in records]
        values, failures = read_values(raw, spec.type, markers)
        frame[spec.name] = values

        if failures:
            index, value = failures[0]
            problems.append(Problem(
                f"column '{spec.name}' expects a {spec.type}; {len(failures)} "
                f"of {len(raw)} value(s) could not be read as one "
                f"({word} {records[index][0]}: '{value}')",
                column=spec.name, line=records[index][0]))

        if not spec.allow_null:
            blanks = [records[i][0] for i, v in enumerate(values.isna()) if v]
            if blanks:
                problems.append(Problem(
                    f"column '{spec.name}' expects a value in every row; "
                    f"{len(blanks)} row(s) are blank (first at {word} "
                    f"{blanks[0]})", column=spec.name, line=blanks[0]))

    if problems:
        return FileLoad(cells=cells, problems=problems, notes=notes)
    return FileLoad(df=pd.DataFrame(frame), cells=cells, notes=notes)
