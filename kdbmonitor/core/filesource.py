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
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any, Optional

import pandas as pd

from kdbmonitor.core.dashboard_models import ColumnSpec, FileShape

# Tried in this order. utf-8-sig first because that is what everything
# downstream assumes text to be; a plain, well-formed export never leaves it.
# cp1252 next because it is what a European-locale Excel spells a
# semicolon-delimited export in when it is not UTF-8 — an apostrophe or an
# accented name is exactly the byte utf-8 refuses and cp1252 reads correctly.
# latin-1 last because every byte value is a valid code point in it: it cannot
# fail, so it is the backstop once cp1252 also refuses a byte, not a second
# guess at which encoding is "really" right.
_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")

# Candidates tried when FileShape.delimiter is "auto". Order only matters as
# the last tiebreaker, below.
_DELIMITERS = (",", ";", "\t", "|")


def _decode(data: bytes) -> tuple[str, str]:
    """The file's text, and which encoding it actually took to read it.

    latin-1 cannot raise — every byte from 0 to 255 is a defined code point in
    it — so this always returns rather than raising. That is deliberate: a
    file is not refused over its encoding any more, only over what is
    afterwards found (or not found) inside it. The encoding name is passed
    back so the caller can say, in a note, when it was not what was assumed.
    """
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise AssertionError("unreachable: latin-1 decodes every byte sequence")


def _sniff_delimiter(text: str) -> str:
    """Which of ``,`` ``;`` tab or ``|`` this file actually uses.

    Decided by *parsing* the first ~20 non-blank lines with each candidate and
    counting the fields that come out — not by counting delimiter characters,
    which a quoted comma sitting inside a semicolon-delimited field would
    inflate into a false positive.

    A candidate that actually splits a line into more than one field always
    beats one that does not, before consistency is even considered: a
    delimiter absent from the text parses every line as a single field, which
    is trivially "consistent" and would otherwise out-score the true
    delimiter the moment one row in the real file is short or ragged. Only
    once that is settled does the steadiest field count across lines decide
    it, and only once that is also tied does comma — the format this module
    has always assumed — break it.
    """
    lines = [line for line in text.splitlines() if line.strip()][:20]
    if not lines:
        return ","
    best_delimiter = ","
    best_score = None
    for delimiter in _DELIMITERS:
        try:
            counts = [len(row) for row in csv.reader(lines, delimiter=delimiter)]
        except csv.Error:
            continue
        if not counts:
            continue
        mode_count, frequency = Counter(counts).most_common(1)[0]
        score = (mode_count > 1, frequency / len(counts), delimiter == ",")
        if best_score is None or score > best_score:
            best_score, best_delimiter = score, delimiter
    return best_delimiter


def _split(text: str, delimiter: str) -> list[list[str]]:
    """Decoded text as a rectangular grid, using exactly the delimiter given.

    Padded to the widest row, because everything downstream addresses a cell by
    ``(row, col)`` and a ragged grid makes that address a lie.
    """
    rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def _read_grid_and_encoding(data: bytes,
                            delimiter: str) -> tuple[list[list[str]], str, str]:
    """The grid, the encoding it took to read it, and the delimiter used.

    The one place both decisions are made, so :func:`load` can report the
    encoding and pass the resolved delimiter on to the number readers, which
    have to know it and cannot sniff it again themselves from a grid that has
    already been split.
    """
    text, encoding = _decode(data)
    used = delimiter if delimiter != "auto" else _sniff_delimiter(text)
    return _split(text, used), encoding, used


def read_grid(data: bytes, delimiter: str = "auto") -> list[list[str]]:
    """The file as a rectangular grid of strings, exactly as written.

    ``delimiter`` defaults to sniffing among comma, semicolon, tab and pipe;
    passing anything else uses it exactly as given and never second-guesses
    it — a file whose real delimiter was declared gets read by that delimiter
    even if it happens to also look plausible as something else.
    """
    grid, _, _ = _read_grid_and_encoding(data, delimiter)
    return grid


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


# A valid thousands-grouped number: 1-3 digits, then any number of groups of
# exactly 3, then an optional decimal part. "125,000" matches; "0,0215" does
# not (its second group is 4 digits) — which is exactly how a comma-file
# number column tells a real thousands separator from a decimal comma that
# does not belong there, see _to_number.
_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})*(\.\d+)?$")


def _to_number(text: str, delimiter: str = ",") -> float:
    """A person's number, read the way their file's own delimiter implies.

    Where the delimiter *is* a comma, a comma in the value can only be a
    thousands separator — the file already uses that character to end a
    field, so a stray one inside a field arrived quoted on purpose. It is
    stripped, but only after checking it groups digits in threes; "0,0215"
    groups as 1-then-4, which is not how anyone writes a thousand, so it is
    refused rather than silently read as 215 (or, worse, mistaken for the
    decimal-comma reading below, which belongs to a different file format and
    cannot be told apart from a thousands separator by the digits alone).

    Where the delimiter is anything else, the file cannot mean a thousands
    separator by a comma — that job already belongs to the delimiter — so a
    comma here is Europe's decimal point, exactly as it is in the sample
    export that motivated this: "0,0215" reads as 0.0215.

    ``float()`` turns an overflow like "1e400", or a literal "inf", into
    ``inf`` rather than raising — so that has to be caught here. Left alone it
    would sit in a number column formatting and aggregating like any other
    value, which is the same silent-infinity failure ``transform.
    _no_infinities`` exists to strip out of a derived column; a file should
    not be able to smuggle the same thing in from the other end.
    """
    cleaned = text.replace(" ", "")
    if delimiter == ",":
        if "," in cleaned:
            if not _THOUSANDS.fullmatch(cleaned):
                raise ValueError(f"{text} is not a number")
            cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    value = float(cleaned)
    if not math.isfinite(value):
        raise ValueError(f"{text} is not a finite number")
    return value


def _to_integer(text: str, delimiter: str = ",") -> int:
    value = _to_number(text, delimiter)
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


# Days from this date to a value is how Excel spells a date as a plain number
# — 1899-12-30 rather than the 1900-01-01 the format nominally starts from,
# because Excel's serials carry a phantom 1900-02-29 that never existed, and
# backdating the epoch by a day is how everyone's implementation quietly
# absorbs that bug rather than reproducing it.
_EXCEL_EPOCH = pd.Timestamp("1899-12-30")


def _to_date(text: str):
    """A date, or — only for a column explicitly declared ``date`` — an Excel
    time serial: a plain number spelling a date as days since 1899-12-30, with
    a fractional part as the time of day (0.385416 of a day is 09:15).

    Tried only *after* the ordinary parse, not before: pandas already reads a
    bare "2026" as that calendar year, and trying the serial reading first
    would turn every four-digit year into a date a century early, which is a
    worse guess than the one already being made. The serial reading exists to
    catch what the ordinary parse refuses — a fraction, or a whole number
    outside pandas' notion of a plausible date string — not to compete with it.

    This does mean a small integer in a date column — "5", say — reads as
    1900-01-04 rather than being refused: nothing here can tell a serial from
    a quantity typed in the wrong column, and the rule as specified does not
    ask it to.
    """
    try:
        value = pd.to_datetime(text, errors="raise")
        if not pd.isna(value):
            return value
    except (ValueError, TypeError, pd.errors.ParserError):
        pass
    try:
        serial = float(text)
    except ValueError:
        raise ValueError(f"{text} is not a date") from None
    if not math.isfinite(serial):
        raise ValueError(f"{text} is not a date")
    return _EXCEL_EPOCH + pd.Timedelta(days=serial)


_READERS = {"number": _to_number, "integer": _to_integer,
            "boolean": _to_boolean, "date": _to_date, "text": lambda t: t}


def read_values(cells: list[str], type_name: str, markers: set[str],
                delimiter: str = ",") -> tuple[pd.Series, list[tuple[int, str]]]:
    """A column read as ``type_name``, plus every value that would not read.

    Checking by *reading* rather than by inferring a type and comparing labels
    is what makes integers-where-numbers-were-expected work without a special
    case, and it is what lets a refusal quote the value that broke.

    ``delimiter`` is the file's own field separator, passed in rather than
    read off a global, because a number reader has to know it to tell a
    thousands separator from a decimal comma — the same character means
    opposite things depending on it. Only "number" and "integer" care; every
    other type ignores it.

    An unrecognised type name reads as text rather than raising: it can only
    come from a hand-edited bundle, and refusing every row of a column because
    its declared type is misspelt helps nobody.
    """
    if type_name == "number":
        reader = lambda t: _to_number(t, delimiter)
    elif type_name == "integer":
        reader = lambda t: _to_integer(t, delimiter)
    else:
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


def _effective_delimiter(shape: FileShape) -> str:
    """The delimiter to use when a comma might be a decimal point instead.

    "auto" means nobody has resolved it for this shape yet — the shape editor's
    live preview reads a grid it already built, rather than going through
    :func:`load`, which is the only place "auto" gets replaced by whatever the
    sniffer actually found. Treating an unresolved "auto" as comma is the
    conservative reading: it keeps the existing thousands-separator behaviour
    rather than guessing that the file's numbers use a decimal comma, which
    would be exactly the kind of second guess this module does not make.
    """
    return shape.delimiter if shape.delimiter != "auto" else ","


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
    delimiter = _effective_delimiter(shape)
    out: dict[str, Any] = {}
    for cell in shape.cells:
        raw = ""
        if 0 <= cell.row < len(grid) and 0 <= cell.col < len(grid[cell.row]):
            raw = grid[cell.row][cell.col]
        values, _ = read_values([raw], cell.type, markers, delimiter)
        value = values.iloc[0]
        out[cell.name] = None if pd.isna(value) else value
    return out


def load(data: bytes, shape: FileShape) -> FileLoad:
    """An uploaded file read against ``shape``: a frame, or a refusal.

    Resolves "auto" to whichever delimiter the sniffer actually found before
    handing off to :func:`load_grid`, so every reader downstream — including
    the number columns, which have to tell a thousands separator from a
    decimal comma — sees the real delimiter rather than the unresolved word
    "auto". Decoding itself no longer refuses a file: latin-1 always succeeds,
    so what would once have been "this file is not UTF-8 text" is now a note
    on a file that loaded anyway, worth knowing about because a byte utf-8
    rejected can still turn a header into mojibake.
    """
    grid, encoding, used_delimiter = _read_grid_and_encoding(data, shape.delimiter)
    resolved = shape if used_delimiter == shape.delimiter else replace(
        shape, delimiter=used_delimiter)
    out = load_grid(grid, resolved)
    if encoding != "utf-8-sig":
        out.notes = [f"this file was not UTF-8 text — read as {encoding}"] + out.notes
    return out


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
    delimiter = _effective_delimiter(shape)
    word = _axis_word(shape)
    frame: dict[str, pd.Series] = {}
    for position, spec in enumerate(wanted):
        raw = [cells_of[position] for _, cells_of in records]
        values, failures = read_values(raw, spec.type, markers, delimiter)
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


# Tried in order; the first that reads every non-blank value wins. `text` always
# succeeds, so this terminates.
_INFERENCE_ORDER = ("date", "integer", "number", "boolean", "text")

# A date has to look like one. Without this, pandas reads '2026' as a year and a
# column of quantities profiles as dates — which the author would then have to
# undo on every dashboard they build. It makes inference stricter than
# validation, deliberately: a column explicitly declared a date still accepts
# whatever pandas can parse.
_DATE_HINTS = ("-", "/", ":")


def _reads_as(values: list[str], type_name: str) -> bool:
    if type_name == "date" and not all(any(h in v for h in _DATE_HINTS)
                                       for v in values):
        return False
    reader = _READERS[type_name]
    for value in values:
        try:
            reader(value)
        except (ValueError, TypeError, OverflowError, pd.errors.ParserError):
            return False
    return True


def profile_columns(grid: list[list[str]], shape: FileShape) -> list[ColumnSpec]:
    """A first guess at the column contract, read off a sample.

    Only the *types* are read from the file. Where the table sits was declared,
    not discovered — see the module docstring. Even the types are a starting
    value the author corrects: a column of integer-looking order IDs is text,
    and only a human knows that.
    """
    oriented = orient(grid, shape.header_axis)
    columns, problems = header_columns(oriented, shape)
    if problems and not columns:
        return []

    records, _ = data_records(oriented, shape, columns)
    markers = null_set(shape)
    specs: list[ColumnSpec] = []
    for position, (name, _) in enumerate(columns):
        values = [str(cells[position]).strip() for _, cells in records]
        real = [v for v in values if not is_blank(v, markers)]
        # A column with nothing in it cannot be typed. Text accepts whatever
        # turns up later, which is the honest answer to "we do not know".
        kind = next((t for t in _INFERENCE_ORDER if _reads_as(real, t)), "text") \
            if real else "text"
        specs.append(ColumnSpec(name=name, type=kind))
    return specs
