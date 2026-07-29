# File-Backed Dashboards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a dashboard be built against an uploaded CSV instead of a KDB query, and let other users run that same dashboard against their own file of the same shape.

**Architecture:** A `Dataset` gains `source = "kdb" | "file"`. A file dataset carries a `FileShape` — where the table sits in the file and what columns it must hold. A new Streamlit-free module `core/filesource.py` turns bytes into a validated DataFrame or a refusal; `run_datasets` takes those frames through `uploads` and applies the existing transform tail, so by the time a widget sees a `DatasetResult` it cannot tell which source produced it. Nothing below the dataset changes.

**Tech Stack:** Python 3.11, Streamlit, pandas, SQLite, pytest. Standard-library `csv` for parsing.

**Spec:** `docs/superpowers/specs/2026-07-30-file-dashboards-design.md`

---

## File Structure

**Created — core (Streamlit-free, unit-tested):**

| File | Responsibility |
| --- | --- |
| `kdbmonitor/core/filesource.py` | bytes → grid → oriented → header → data region → nulls → types → `FileLoad`. Also `profile_columns` for design time. |

**Created — UI (thin, not unit-tested):**

| File | Responsibility |
| --- | --- |
| `kdbmonitor/ui/fileshape.py` | the design-time shape editor: uploader, grid preview, header/orientation/type controls, named cells |

**Created — tests:**

| File | Covers |
| --- | --- |
| `tests/test_filesource.py` | every rule in spec §5 and §6 |
| `tests/test_file_datasets.py` | spec §8: uploads through `run_datasets`, waiting state, PDF |

**Modified:**

| File | Change |
| --- | --- |
| `kdbmonitor/core/dashboard_models.py` | `ColumnSpec`, `NamedCell`, `FileShape`; `Dataset.source/shape/file_label`; `Dashboard.source`; `dashboard_from_dict` |
| `kdbmonitor/core/dataset.py` | `DatasetResult.waiting`; `uploads` through `_fetch` / `run_dataset` / `run_datasets` / `run_dataset_steps` / `trace_datasets` |
| `kdbmonitor/ui/dashboard_editor.py` | split `validate`; `dataset_columns` file branch; source selector; delegate to `fileshape` |
| `kdbmonitor/ui/dashboards.py` | upload panel; `uploads` through `refresh`; no timer for file dashboards |

**Conventions this codebase uses — follow them:**

- Run tests with `PYTHONPATH=. python -m pytest ...` from the repo root (Git Bash).
- All logic lives in `core/` and is unit-tested; `ui/` is thin Streamlit and is not.
- Failures inside a dataset are *captured and returned*, never raised — a broken source degrades one panel, not the page.
- Commits: lowercase `feat:`/`fix:`/`refactor:`/`test:` prefix, title a declarative sentence about the outcome, body in prose. Commit straight to `master`.

---

## Task 1: The shape dataclasses

**Files:**
- Modify: `kdbmonitor/core/dashboard_models.py`
- Test: `tests/test_schema.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schema.py`:

```python
from kdbmonitor.core.dashboard_models import (
    ColumnSpec, Dashboard, Dataset, FileShape, NamedCell,
    dashboard_from_dict, dashboard_to_dict,
)


def test_a_file_dataset_survives_a_round_trip():
    shape = FileShape(
        header_axis="column", header_row=2, first_col=1, data_start=3,
        null_markers=["", "n/a"],
        columns=[ColumnSpec(name="qty", type="number", allow_null=False)],
        cells=[NamedCell(name="Report date", row=0, col=1, type="date")])
    d = Dashboard(id=1, name="Orders", source="file", datasets=[
        Dataset(name="orders", env="", source="file", shape=shape,
                file_label="your orders export")])

    back = dashboard_from_dict(dashboard_to_dict(d))

    assert back.source == "file"
    ds = back.datasets[0]
    assert ds.source == "file" and ds.file_label == "your orders export"
    assert ds.shape.header_axis == "column"
    assert (ds.shape.header_row, ds.shape.first_col, ds.shape.data_start) == (2, 1, 3)
    assert ds.shape.null_markers == ["", "n/a"]
    assert ds.shape.columns[0] == ColumnSpec(name="qty", type="number",
                                             allow_null=False)
    assert ds.shape.cells[0] == NamedCell(name="Report date", row=0, col=1,
                                          type="date")


def test_a_dashboard_saved_before_file_sources_reads_back_as_kdb():
    back = dashboard_from_dict({"name": "Old", "rows": [],
                                "datasets": [{"name": "d", "env": "prod"}]})
    assert back.source == "kdb"
    assert back.datasets[0].source == "kdb"
    assert back.datasets[0].shape is None


def test_a_shape_left_off_a_file_dataset_reads_back_as_none():
    back = dashboard_from_dict({"name": "F", "source": "file", "rows": [],
                                "datasets": [{"name": "d", "source": "file"}]})
    assert back.datasets[0].shape is None
    assert back.datasets[0].file_label == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_schema.py -k file -v`
Expected: FAIL with `ImportError: cannot import name 'ColumnSpec'`

- [ ] **Step 3: Add the dataclasses**

In `kdbmonitor/core/dashboard_models.py`, after the `Transform` dataclass (line ~25), insert:

```python
# What a cell has to say to be read as missing. Matched case-insensitively after
# trimming. Editable per dataset because a column in which "-" is a real
# category would otherwise be silently blanked — quiet data loss, worth a
# control to prevent.
DEFAULT_NULL_MARKERS = ["", "NA", "N/A", "NaN", "NULL", "NONE", "-", "--", "#N/A"]

COLUMN_TYPES = ("date", "number", "integer", "text", "boolean")


@dataclass
class ColumnSpec:
    """One column an uploaded file has to provide."""
    name: str                    # the header text the file must carry
    type: str = "text"           # one of COLUMN_TYPES
    required: bool = True        # a column no widget references need not arrive
    allow_null: bool = True      # false: a blank in this column is a refusal


@dataclass
class NamedCell:
    """A single cell picked out of the file, outside the table.

    Addressed against the raw grid as it sits on disk — the grid the designer
    clicked to create it. Orientation applies to the table region only; if it
    applied here too, turning on vertical headers would move every cell already
    named.
    """
    name: str                    # "Report date"
    row: int = 0                 # 0-based
    col: int = 0                 # 0-based
    type: str = "text"
    allow_null: bool = True


@dataclass
class FileShape:
    """Where the table sits in an uploaded file, and what it must contain.

    Nothing here is ever guessed at run time. The header line is declared, and a
    file whose header is elsewhere is refused rather than searched.
    """
    header_axis: str = "row"     # row = headers across | column = headers down
    header_row: int = 0          # 0-based line carrying the headers
    first_col: int = 0           # 0-based column the table starts at
    data_start: int = 1          # 0-based first data line
    null_markers: list[str] = field(default_factory=lambda:
                                    list(DEFAULT_NULL_MARKERS))
    columns: list[ColumnSpec] = field(default_factory=list)
    cells: list[NamedCell] = field(default_factory=list)
```

- [ ] **Step 4: Add the fields to `Dataset` and `Dashboard`**

In `Dataset`, after `max_rows: int = 5000`, add:

```python
    # --- file-backed datasets -------------------------------------------
    # A file dataset ignores env/time_mode/mode/table/filters/raw_qsql above:
    # they describe a server, and there is no server. transforms and max_rows
    # are NOT ignored — they apply to an uploaded frame identically, which is
    # why a file dataset needs no shaping vocabulary of its own.
    source: str = "kdb"          # kdb | file
    shape: Optional[FileShape] = None       # file only
    file_label: str = ""         # the prompt on the upload box
```

In `Dashboard`, after `periods: str = "both"` and the `orientation` field, add:

```python
    # Where this dashboard's data comes from. A file dashboard has no
    # environment, no period and no refresh interval — see the spec, §8.1.
    source: str = "kdb"          # kdb | file
```

- [ ] **Step 5: Reconstruct the nested shape on load**

In `kdbmonitor/core/dashboard_models.py`, add above `_dataset_from_dict`:

```python
def _column_from_dict(d: dict) -> ColumnSpec:
    return ColumnSpec(name=d.get("name", ""), type=d.get("type", "text"),
                      required=bool(d.get("required", True)),
                      allow_null=bool(d.get("allow_null", True)))


def _cell_from_dict(d: dict) -> NamedCell:
    return NamedCell(name=d.get("name", ""), row=int(d.get("row", 0)),
                     col=int(d.get("col", 0)), type=d.get("type", "text"),
                     allow_null=bool(d.get("allow_null", True)))


def _shape_from_dict(d: Optional[dict]) -> Optional[FileShape]:
    """A stored shape, field by field — never ``FileShape(**d)``.

    Splatting a stored dict would make an old dashboard carrying a field this
    version has since dropped raise on load, which is the one thing reading
    stored data must never do.
    """
    if not d:
        return None
    return FileShape(
        header_axis=d.get("header_axis", "row"),
        header_row=int(d.get("header_row", 0)),
        first_col=int(d.get("first_col", 0)),
        data_start=int(d.get("data_start", 1)),
        null_markers=list(d.get("null_markers") or DEFAULT_NULL_MARKERS),
        columns=[_column_from_dict(c) for c in d.get("columns", [])],
        cells=[_cell_from_dict(c) for c in d.get("cells", [])])
```

In `_dataset_from_dict`, add to the `Dataset(...)` call:

```python
        source=d.get("source", "kdb"),
        shape=_shape_from_dict(d.get("shape")),
        file_label=d.get("file_label", ""),
```

In `dashboard_from_dict`, add to the `Dashboard(...)` call:

```python
        source=d.get("source", "kdb"),
```

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_schema.py -v`
Expected: PASS, including the three new tests.

- [ ] **Step 7: Run the whole suite for regressions**

Run: `PYTHONPATH=. python -m pytest tests -q`
Expected: all pass — `asdict` picks the new fields up, and every `.get` has a default.

- [ ] **Step 8: Commit**

```bash
git add kdbmonitor/core/dashboard_models.py tests/test_schema.py
git commit -m "feat: a dataset can say its data arrives as a file"
```

---

## Task 2: Reading a file into a grid

**Files:**
- Create: `kdbmonitor/core/filesource.py`
- Test: `tests/test_filesource.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_filesource.py`:

```python
import pytest

from kdbmonitor.core.filesource import read_grid


def test_a_plain_csv_becomes_a_grid():
    assert read_grid(b"a,b\n1,2\n") == [["a", "b"], ["1", "2"]]


def test_short_rows_are_padded_so_the_grid_is_rectangular():
    """A cell is addressed by (row, col); a ragged grid makes that a lie."""
    assert read_grid(b"a,b,c\n1\n") == [["a", "b", "c"], ["1", "", ""]]


def test_quoted_fields_keep_their_commas():
    assert read_grid(b'a,b\n"125,000",x\n') == [["a", "b"], ["125,000", "x"]]


def test_a_byte_order_mark_is_not_part_of_the_first_header():
    """Excel writes one, and it made the first column unmatchable by name."""
    assert read_grid("﻿sym,qty\n0005.HK,10\n".encode("utf-8")) == [
        ["sym", "qty"], ["0005.HK", "10"]]


def test_an_empty_file_is_an_empty_grid():
    assert read_grid(b"") == []


def test_a_file_that_is_not_utf8_text_is_refused_by_name():
    with pytest.raises(ValueError, match="UTF-8"):
        read_grid(b"\xff\xfe\x00s\x00y")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kdbmonitor.core.filesource'`

- [ ] **Step 3: Create the module with `read_grid`**

Create `kdbmonitor/core/filesource.py`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/filesource.py tests/test_filesource.py
git commit -m "feat: an uploaded file is read as a grid of cells"
```

---

## Task 3: Orientation and the declared header line

**Files:**
- Modify: `kdbmonitor/core/filesource.py`
- Test: `tests/test_filesource.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filesource.py`:

```python
from kdbmonitor.core.dashboard_models import ColumnSpec, FileShape
from kdbmonitor.core.filesource import Problem, header_columns, orient


def _shape(**kw) -> FileShape:
    kw.setdefault("columns", [ColumnSpec(name="sym"), ColumnSpec(name="qty")])
    return FileShape(**kw)


def test_row_headers_are_left_alone():
    grid = [["a", "b"], ["1", "2"]]
    assert orient(grid, "row") == grid


def test_column_headers_are_transposed_into_row_headers():
    """Headers running down the first column, one record per column."""
    assert orient([["sym", "0005.HK", "7203.JP"],
                   ["qty", "10", "20"]], "column") == [
        ["sym", "qty"], ["0005.HK", "10"], ["7203.JP", "20"]]


def test_transposing_an_empty_grid_is_not_an_error():
    assert orient([], "column") == []


def test_headers_are_read_from_the_declared_line():
    grid = [["report", "", ""], ["", "", ""], ["sym", "qty", "note"]]
    found, problems = header_columns(grid, _shape(header_row=2, data_start=3))
    assert problems == []
    assert found == [("sym", 0), ("qty", 1), ("note", 2)]


def test_headers_are_read_from_the_declared_column_onwards():
    grid = [["#", "sym", "qty"]]
    found, problems = header_columns(grid, _shape(first_col=1, data_start=1))
    assert problems == []
    assert found == [("sym", 1), ("qty", 2)]


def test_a_header_somewhere_else_is_refused_and_the_line_is_quoted():
    """No searching. The declared line is the contract."""
    grid = [["sym", "qty"], ["0005.HK", "10"]]
    _, problems = header_columns(grid, _shape(header_row=1, data_start=2))
    assert len(problems) == 1
    assert "line 2" in problems[0].message
    assert "0005.HK" in problems[0].message      # what was actually there


def test_a_header_line_past_the_end_of_the_file_is_refused_not_an_index_error():
    _, problems = header_columns([["sym", "qty"]], _shape(header_row=9,
                                                          data_start=10))
    assert len(problems) == 1
    assert "9 line(s)" in problems[0].message or "line 10" in problems[0].message


def test_a_trailing_comma_makes_a_blank_header_which_is_dropped():
    """Nothing can reference an unnamed column, so it is not offered."""
    found, problems = header_columns([["sym", "qty", ""]], _shape(data_start=1))
    assert problems == []
    assert found == [("sym", 0), ("qty", 1)]


def test_two_columns_with_the_same_name_are_refused():
    _, problems = header_columns([["qty", "qty"]], _shape(data_start=1))
    assert len(problems) == 1
    assert "qty" in problems[0].message


def test_a_header_line_with_no_names_at_all_is_refused():
    _, problems = header_columns([["", ""]], _shape(data_start=1))
    assert len(problems) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -k "orient or header" -v`
Expected: FAIL with `ImportError: cannot import name 'Problem'`

- [ ] **Step 3: Add `Problem`, `orient` and `header_columns`**

Add to `kdbmonitor/core/filesource.py` — imports first:

```python
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from kdbmonitor.core.dashboard_models import ColumnSpec, FileShape, NamedCell
```

Then, after `read_grid`:

```python
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
    """
    if axis != "column":
        return grid
    return [list(row) for row in zip(*grid)]


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
        shown = ", ".join(c for c in row[:6]) or "(empty)"
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
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -v`
Expected: PASS, 16 tests.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/filesource.py tests/test_filesource.py
git commit -m "feat: the header line is read where it was declared to be"
```

---

## Task 4: The data region

**Files:**
- Modify: `kdbmonitor/core/filesource.py`
- Test: `tests/test_filesource.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filesource.py`:

```python
from kdbmonitor.core.filesource import data_records


def test_data_starts_where_it_was_declared_to():
    grid = [["sym", "qty"], ["0005.HK", "10"], ["7203.JP", "20"]]
    records, skipped = data_records(grid, _shape(data_start=1),
                                    [("sym", 0), ("qty", 1)])
    assert skipped == 0
    assert records == [(2, ["0005.HK", "10"]), (3, ["7203.JP", "20"])]


def test_only_the_named_columns_are_taken():
    """A column dropped for having no header takes its data with it."""
    grid = [["sym", "", "qty"], ["0005.HK", "junk", "10"]]
    records, _ = data_records(grid, _shape(data_start=1),
                              [("sym", 0), ("qty", 2)])
    assert records == [(2, ["0005.HK", "10"])]


def test_a_wholly_blank_row_is_dropped_and_counted():
    """A trailing blank line is not a row of nulls."""
    grid = [["sym", "qty"], ["0005.HK", "10"], ["", ""], ["7203.JP", "20"]]
    records, skipped = data_records(grid, _shape(data_start=1),
                                    [("sym", 0), ("qty", 1)])
    assert skipped == 1
    assert [r[1] for r in records] == [["0005.HK", "10"], ["7203.JP", "20"]]


def test_the_line_number_survives_a_dropped_row():
    """It points into the file the reader has open, not into what we kept."""
    grid = [["sym", "qty"], ["", ""], ["7203.JP", "20"]]
    records, _ = data_records(grid, _shape(data_start=1),
                              [("sym", 0), ("qty", 1)])
    assert records == [(3, ["7203.JP", "20"])]


def test_whitespace_only_counts_as_blank():
    grid = [["sym", "qty"], ["  ", " "]]
    records, skipped = data_records(grid, _shape(data_start=1),
                                    [("sym", 0), ("qty", 1)])
    assert (records, skipped) == ([], 1)


def test_a_file_with_headers_and_no_data_yields_no_records():
    records, skipped = data_records([["sym", "qty"]], _shape(data_start=1),
                                    [("sym", 0), ("qty", 1)])
    assert (records, skipped) == ([], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -k data_ -v`
Expected: FAIL with `ImportError: cannot import name 'data_records'`

- [ ] **Step 3: Implement `data_records`**

Append to `kdbmonitor/core/filesource.py`:

```python
def data_records(grid: list[list[str]], shape: FileShape,
                 columns: list[tuple[str, int]]) -> tuple[list[tuple[int, list[str]]], int]:
    """The table's rows as ``(1-based file position, cells)``, and how many were
    blank.

    The position is carried rather than recomputed because blank rows are
    dropped: a refusal has to point into the file the reader has open, not into
    the rows that happened to survive.
    """
    records: list[tuple[int, list[str]]] = []
    skipped = 0
    for offset in range(shape.data_start, len(grid)):
        row = grid[offset]
        cells = [row[i] if i < len(row) else "" for _, i in columns]
        if not any(str(c).strip() for c in cells):
            skipped += 1
            continue
        records.append((offset + 1, cells))
    return records, skipped
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -v`
Expected: PASS, 22 tests.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/filesource.py tests/test_filesource.py
git commit -m "feat: a blank line is skipped rather than read as a row of nulls"
```

---

## Task 5: Nulls and reading a column as its declared type

**Files:**
- Modify: `kdbmonitor/core/filesource.py`
- Test: `tests/test_filesource.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filesource.py`:

```python
import pandas as pd

from kdbmonitor.core.filesource import is_blank, null_set, read_values


DEFAULTS = null_set(FileShape())


def test_the_default_markers_all_read_as_missing():
    for marker in ("", "NA", "N/A", "NaN", "NULL", "NONE", "-", "--", "#N/A"):
        assert is_blank(marker, DEFAULTS), marker


def test_markers_are_matched_whatever_their_case_or_padding():
    assert is_blank("  n/a  ", DEFAULTS)


def test_a_real_value_is_not_missing():
    assert not is_blank("0", DEFAULTS)
    assert not is_blank("0005.HK", DEFAULTS)


def test_a_marker_can_be_taken_off_the_list():
    """A 'side' column where '-' is a real category must keep it."""
    markers = null_set(FileShape(null_markers=["", "N/A"]))
    assert not is_blank("-", markers)


def test_integers_satisfy_a_number_column():
    values, failures = read_values(["10", "20"], "number", DEFAULTS)
    assert failures == []
    assert list(values) == [10.0, 20.0]


def test_a_thousands_separator_does_not_stop_a_number_being_read():
    values, failures = read_values(["125,000"], "number", DEFAULTS)
    assert failures == [] and list(values) == [125000.0]


def test_a_blank_never_fails_a_number_column():
    values, failures = read_values(["10", "N/A", ""], "number", DEFAULTS)
    assert failures == []
    assert values.isna().tolist() == [False, True, True]


def test_text_where_a_number_was_promised_fails_and_says_which_value():
    _, failures = read_values(["10", "hello"], "number", DEFAULTS)
    assert [f[0] for f in failures] == [1]          # index within the column
    assert failures[0][1] == "hello"


def test_a_fraction_is_refused_by_an_integer_column():
    """Narrowing loses information, and silently."""
    _, failures = read_values(["1.5"], "integer", DEFAULTS)
    assert len(failures) == 1


def test_a_whole_number_written_as_a_float_is_accepted_by_an_integer_column():
    values, failures = read_values(["10.0"], "integer", DEFAULTS)
    assert failures == [] and list(values) == [10]


def test_a_text_column_accepts_anything():
    values, failures = read_values(["10", "hello", "-"], "text", DEFAULTS)
    assert failures == []
    assert values.tolist()[:2] == ["10", "hello"]
    assert pd.isna(values.tolist()[2])              # "-" is still a null marker


def test_a_date_column_reads_dates_and_refuses_prose():
    values, failures = read_values(["2026-07-30"], "date", DEFAULTS)
    assert failures == [] and values.iloc[0] == pd.Timestamp("2026-07-30")
    _, bad = read_values(["not a date"], "date", DEFAULTS)
    assert len(bad) == 1


def test_a_boolean_column_reads_the_usual_spellings():
    values, failures = read_values(["true", "N", "1", "0"], "boolean", DEFAULTS)
    assert failures == []
    assert list(values) == [True, False, True, False]


def test_an_empty_column_reads_as_all_null_whatever_the_type():
    values, failures = read_values(["", ""], "number", DEFAULTS)
    assert failures == [] and values.isna().all()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -k "blank or read_values or marker" -v`
Expected: FAIL with `ImportError: cannot import name 'is_blank'`

- [ ] **Step 3: Implement null handling and coercion**

Append to `kdbmonitor/core/filesource.py`:

```python
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
    """Commas and spaces are how a person writes a number, not part of it."""
    return float(text.replace(",", "").replace(" ", ""))


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
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -v`
Expected: PASS, 36 tests.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/filesource.py tests/test_filesource.py
git commit -m "feat: a column is checked by reading it, not by guessing its type"
```

---

## Task 6: The column contract, and `load`

**Files:**
- Modify: `kdbmonitor/core/filesource.py`
- Test: `tests/test_filesource.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filesource.py`:

```python
from kdbmonitor.core.filesource import FileLoad, load


ORDERS = b"sym,qty,venue\n0005.HK,10,SEHK\n7203.JP,20,TSE\n"


def _orders_shape(**kw) -> FileShape:
    kw.setdefault("columns", [ColumnSpec(name="sym", type="text"),
                              ColumnSpec(name="qty", type="number")])
    return FileShape(**kw)


def test_a_matching_file_is_accepted():
    out = load(ORDERS, _orders_shape())
    assert out.problems == []
    assert list(out.df.columns) == ["sym", "qty"]
    assert out.df["qty"].tolist() == [10.0, 20.0]


def test_a_column_nothing_asked_for_is_ignored_and_noted():
    out = load(ORDERS, _orders_shape())
    assert "venue" not in out.df.columns
    assert any("venue" in n for n in out.notes)


def test_a_missing_required_column_is_refused_and_says_what_did_arrive():
    shape = _orders_shape(columns=[ColumnSpec(name="sym"),
                                   ColumnSpec(name="filledQty")])
    out = load(ORDERS, shape)
    assert out.df is None
    joined = " ".join(p.message for p in out.problems)
    assert "filledQty" in joined and "venue" in joined


def test_a_missing_optional_column_is_fine():
    shape = _orders_shape(columns=[ColumnSpec(name="sym"),
                                   ColumnSpec(name="note", required=False)])
    out = load(ORDERS, shape)
    assert out.problems == []
    assert "note" not in out.df.columns


def test_a_value_that_will_not_read_names_the_column_the_count_and_the_line():
    bad = b"sym,qty\n0005.HK,10\n7203.JP,N\xc2\xa0A\n"
    out = load(bad, _orders_shape())
    assert out.df is None
    problem = out.problems[0]
    assert problem.column == "qty"
    assert problem.line == 3
    assert "1 of 2" in problem.message


def test_a_column_that_may_not_be_null_refuses_a_gap():
    shape = _orders_shape(columns=[ColumnSpec(name="sym", allow_null=False),
                                   ColumnSpec(name="qty", type="number")])
    out = load(b"sym,qty\n0005.HK,10\n,20\n", shape)
    assert out.df is None
    assert out.problems[0].column == "sym"
    assert out.problems[0].line == 3


def test_a_wholly_empty_column_passes_when_nulls_are_allowed():
    shape = _orders_shape(columns=[ColumnSpec(name="sym"),
                                   ColumnSpec(name="qty", type="number")])
    out = load(b"sym,qty\n0005.HK,\n7203.JP,\n", shape)
    assert out.problems == []
    assert out.df["qty"].isna().all()


def test_the_skipped_blank_rows_are_reported():
    out = load(b"sym,qty\n0005.HK,10\n,\n\n", _orders_shape())
    assert out.problems == []
    assert any("blank" in n for n in out.notes)


def test_a_file_that_is_not_utf8_is_refused_rather_than_raising():
    out = load(b"\xff\xfe\x00s", _orders_shape())
    assert out.df is None and "UTF-8" in out.problems[0].message


def test_an_empty_file_is_refused_rather_than_raising():
    out = load(b"", _orders_shape())
    assert out.df is None and out.problems


def test_every_problem_is_reported_not_just_the_first():
    """One upload, one list of everything wrong — not a game of whack-a-mole."""
    shape = _orders_shape(columns=[ColumnSpec(name="sym", type="number"),
                                   ColumnSpec(name="qty", type="number"),
                                   ColumnSpec(name="missing")])
    out = load(ORDERS, shape)
    assert len({p.column for p in out.problems}) >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -k load -v`
Expected: FAIL with `ImportError: cannot import name 'FileLoad'`

- [ ] **Step 3: Implement `FileLoad` and `load`**

Append to `kdbmonitor/core/filesource.py`:

```python
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
```

- [ ] **Step 4: Add the `read_cells` stub the loader calls**

Task 7 fills this in. Append now so `load` runs:

```python
def read_cells(grid: list[list[str]], shape: FileShape) -> dict[str, Any]:
    """Named cells, read from the raw grid. Filled in by Task 7."""
    return {}
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -v`
Expected: PASS, 47 tests.

- [ ] **Step 6: Commit**

```bash
git add kdbmonitor/core/filesource.py tests/test_filesource.py
git commit -m "feat: a file that does not match is refused, with the reasons"
```

---

## Task 7: Named cells

**Files:**
- Modify: `kdbmonitor/core/filesource.py`
- Test: `tests/test_filesource.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filesource.py`:

```python
PREAMBLE = (b"Working orders,2026-07-30\n"
            b"\n"
            b"sym,qty\n"
            b"0005.HK,10\n")


def test_a_named_cell_is_read_from_where_it_was_pointed_at():
    shape = _orders_shape(header_row=2, data_start=3,
                          cells=[NamedCell(name="Report date", row=0, col=1,
                                           type="date")])
    out = load(PREAMBLE, shape)
    assert out.problems == []
    assert out.cells["Report date"] == pd.Timestamp("2026-07-30")


def test_a_named_cell_reads_the_file_as_written_not_as_transposed():
    """Orientation moves the table. A cell was pointed at on the raw grid."""
    down = b"sym,0005.HK,7203.JP\nqty,10,20\n"
    shape = FileShape(header_axis="column", header_row=0, data_start=1,
                      columns=[ColumnSpec(name="sym"),
                               ColumnSpec(name="qty", type="number")],
                      cells=[NamedCell(name="First symbol", row=0, col=1)])
    out = load(down, shape)
    assert out.problems == []
    assert out.cells["First symbol"] == "0005.HK"


def test_a_named_cell_outside_the_file_is_null_rather_than_an_error():
    shape = _orders_shape(cells=[NamedCell(name="Nowhere", row=99, col=99)])
    out = load(ORDERS, shape)
    assert out.problems == []
    assert out.cells["Nowhere"] is None


def test_a_blank_named_cell_is_null():
    shape = _orders_shape(header_row=2, data_start=3,
                          cells=[NamedCell(name="Note", row=1, col=0)])
    out = load(PREAMBLE, shape)
    assert out.cells["Note"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -k cell -v`
Expected: FAIL — `out.cells` is `{}`, so `KeyError: 'Report date'`

- [ ] **Step 3: Replace the `read_cells` stub**

Replace the stub in `kdbmonitor/core/filesource.py` with:

```python
def read_cells(grid: list[list[str]], shape: FileShape) -> dict[str, Any]:
    """Every named cell, read from the raw grid by its own coordinates.

    Called before :func:`orient` and before the table is cut, because a named
    cell addresses the file as it sits on disk — which is the grid the designer
    was looking at when they pointed at it. A cell outside the file is null
    rather than a refusal: it describes the report, and a report missing its
    date is still a report.
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
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -v`
Expected: PASS, 51 tests.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/filesource.py tests/test_filesource.py
git commit -m "feat: a cell outside the table can be given a name"
```

---

## Task 8: Profiling a sample

**Files:**
- Modify: `kdbmonitor/core/filesource.py`
- Test: `tests/test_filesource.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filesource.py`:

```python
from kdbmonitor.core.filesource import profile_columns


def test_a_column_is_typed_from_what_is_in_it():
    grid = [["sym", "qty", "when", "live"],
            ["0005.HK", "10", "2026-07-30", "true"],
            ["7203.JP", "20", "2026-07-29", "false"]]
    specs = profile_columns(grid, FileShape(header_row=0, data_start=1))
    assert [(s.name, s.type) for s in specs] == [
        ("sym", "text"), ("qty", "integer"), ("when", "date"),
        ("live", "boolean")]


def test_a_column_of_decimals_is_a_number_not_an_integer():
    grid = [["px"], ["1284.55"], ["1290.00"]]
    specs = profile_columns(grid, FileShape(header_row=0, data_start=1))
    assert specs[0].type == "number"


def test_a_bare_number_is_not_mistaken_for_a_date():
    """pandas will read '2026' as a year; a column of quantities is not dates."""
    grid = [["qty"], ["2026"], ["1999"]]
    specs = profile_columns(grid, FileShape(header_row=0, data_start=1))
    assert specs[0].type == "integer"


def test_blanks_do_not_drag_a_column_to_text():
    grid = [["qty"], ["10"], [""], ["N/A"], ["20"]]
    specs = profile_columns(grid, FileShape(header_row=0, data_start=1))
    assert specs[0].type == "integer"


def test_a_column_blank_throughout_the_sample_is_typed_as_text():
    grid = [["notes"], [""], [""]]
    specs = profile_columns(grid, FileShape(header_row=0, data_start=1))
    assert specs[0].type == "text"


def test_profiling_reads_headers_that_run_down_a_column():
    """Transposed first, so the types come off the records, not the labels."""
    grid = [["sym", "0005.HK", "7203.JP"], ["qty", "10", "20"]]
    shape = FileShape(header_axis="column", header_row=0, data_start=1)
    specs = profile_columns(grid, shape)
    assert [(s.name, s.type) for s in specs] == [("sym", "text"),
                                                 ("qty", "integer")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -k profile -v`
Expected: FAIL with `ImportError: cannot import name 'profile_columns'`

- [ ] **Step 3: Implement `profile_columns`**

Append to `kdbmonitor/core/filesource.py`:

```python
# Tried in order; the first that reads every non-blank value wins. `text` always
# succeeds, so this terminates.
_INFERENCE_ORDER = ("date", "integer", "number", "boolean", "text")

# A date has to look like one. Without this, pandas reads '2026' as a year and a
# column of quantities profiles as dates — which the designer then has to undo
# on every dashboard.
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

    Only the *types* are read from the file. Where the table is was declared, not
    discovered — see the module docstring. Even the types are a starting value
    the designer corrects: a column of integer-looking order IDs is text, and
    only a human knows that.
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
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_filesource.py -v`
Expected: PASS, 57 tests.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/filesource.py tests/test_filesource.py
git commit -m "feat: a sample file offers a type for each of its columns"
```

---

## Task 9: A file dataset runs through the existing pipeline

**Files:**
- Modify: `kdbmonitor/core/dataset.py`
- Test: `tests/test_file_datasets.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_file_datasets.py`:

```python
from datetime import date

import pandas as pd

from kdbmonitor.core.dashboard_models import (
    ColumnSpec, Dashboard, Dataset, FileShape, Row, Transform, Widget,
)
from kdbmonitor.core.dataset import run_datasets, trace_datasets

TODAY = date(2026, 7, 30)


def _frame() -> pd.DataFrame:
    return pd.DataFrame({"sym": ["0005.HK", "7203.JP", "0005.HK"],
                         "qty": [10.0, 20.0, 30.0]})


def _shape() -> FileShape:
    return FileShape(columns=[ColumnSpec(name="sym"),
                              ColumnSpec(name="qty", type="number")])


def _dash(transforms=None, max_rows=5000) -> Dashboard:
    return Dashboard(id=1, name="Orders", source="file", datasets=[
        Dataset(name="orders", env="", source="file", shape=_shape(),
                file_label="your orders export",
                transforms=transforms or [], max_rows=max_rows)])


def test_an_uploaded_frame_becomes_the_dataset_result():
    results = run_datasets(_dash(), None, None, TODAY,
                           uploads={"orders": _frame()})
    out = results["orders"]
    assert out.error is None
    assert out.df["qty"].tolist() == [10.0, 20.0, 30.0]
    assert out.row_count == 3


def test_transforms_apply_to_a_file_frame_exactly_as_to_a_query():
    dash = _dash(transforms=[Transform(kind="groupby", params={
        "keys": ["sym"], "aggs": [{"column": "qty", "func": "sum",
                                   "as": "total"}]})])
    out = run_datasets(dash, None, None, TODAY, uploads={"orders": _frame()})
    frame = out["orders"].df.set_index("sym")
    assert frame.loc["0005.HK", "total"] == 40.0


def test_max_rows_caps_a_file_frame_and_says_it_did():
    out = run_datasets(_dash(max_rows=2), None, None, TODAY,
                       uploads={"orders": _frame()})["orders"]
    assert len(out.df) == 2 and out.row_count == 3 and out.truncated


def test_no_upload_yet_is_a_waiting_state_not_a_failure():
    out = run_datasets(_dash(), None, None, TODAY, uploads={})["orders"]
    assert out.df is None
    assert out.waiting is True
    assert "your orders export" in out.error


def test_no_uploads_argument_at_all_still_waits_rather_than_crashing():
    out = run_datasets(_dash(), None, None, TODAY)["orders"]
    assert out.waiting is True


def test_a_kdb_result_is_never_marked_waiting():
    """The flag has to mean something, so it must not be on by default."""
    out = run_datasets(_dash(), None, None, TODAY,
                       uploads={"orders": _frame()})["orders"]
    assert out.waiting is False


def test_the_editor_can_step_through_a_file_dataset_transform_by_transform():
    dash = _dash(transforms=[Transform(kind="sort", params={
        "columns": ["qty"], "ascending": False})])
    trace = trace_datasets(dash, None, None, TODAY,
                           uploads={"orders": _frame()})["orders"]
    assert [s.kind for s in trace.steps] == ["query", "sort"]
    assert trace.df["qty"].tolist() == [30.0, 20.0, 10.0]


def test_a_broken_transform_on_a_file_frame_degrades_one_panel():
    dash = _dash(transforms=[Transform(kind="sort",
                                       params={"columns": ["nope"]})])
    out = run_datasets(dash, None, None, TODAY,
                       uploads={"orders": _frame()})["orders"]
    assert out.df is None and "nope" in out.error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_file_datasets.py -v`
Expected: FAIL with `TypeError: run_datasets() got an unexpected keyword argument 'uploads'`

- [ ] **Step 3: Add `waiting` to `DatasetResult`**

In `kdbmonitor/core/dataset.py`, change the `DatasetResult` dataclass:

```python
@dataclass
class DatasetResult:
    name: str
    df: Optional[pd.DataFrame]      # None when the dataset failed
    qsql: str                       # the query that was (or would have been) sent
    error: Optional[str]
    row_count: int = 0              # true size before max_rows capping
    truncated: bool = False
    # A file dataset with nothing uploaded yet has not failed — it is waiting.
    # The distinction is for the reader: "waiting for your export" is an
    # instruction, and a red panel saying the same thing reads as a fault.
    waiting: bool = False
```

- [ ] **Step 4: Branch `_fetch` on the source**

In `kdbmonitor/core/dataset.py`, replace the opening of `_fetch` (currently
`def _fetch(ds, rt, store, mgr, outputs):` and its first statement) with:

```python
def _file_source(ds: Dataset) -> str:
    """What stands in for the query on a file dataset — what it reads, not how."""
    shape = ds.shape
    if shape is None:
        return "file: no shape configured"
    where = "down column" if shape.header_axis == "column" else "on line"
    return (f"file: {len(shape.columns)} column(s), headers {where} "
            f"{shape.header_row + 1}")


def _fetch(ds: Dataset, rt: ResolvedTime, store, mgr, outputs: dict,
           uploads: Optional[dict] = None) -> tuple[str, Optional[pd.DataFrame],
                                                    Optional[str]]:
    """Send the dataset's query — no transforms — as (qsql, frame, error).

    Never raises: every failure comes back as the error, along with whatever the
    query looked like at that point, so a caller can show it. Shared by the plain
    run and the step-by-step trace, so both send exactly the same query.

    A file dataset sends nothing. Its frame was read and checked at the upload
    box (``core.filesource``), so all that happens here is picking it up — which
    is why a file dataset and a KDB dataset are indistinguishable from the next
    line on.
    """
    if ds.source == "file":
        frame = (uploads or {}).get(ds.name)
        if frame is None:
            return (_file_source(ds), None,
                    f"waiting for {ds.file_label or 'a file'}")
        return _file_source(ds), frame, None

    # Resolve first: the date guard must apply to the server actually queried,
    # and a market-data environment is never historical.
    try:
        conn, effective = resolve_target(store, ds.env, rt)
    except Exception as exc:      # noqa: BLE001 - a broken panel, not a page
        return "", None, str(exc)
```

Leave the rest of `_fetch` untouched.

- [ ] **Step 5: Thread `uploads` through the four callers**

In `kdbmonitor/core/dataset.py`, change these signatures and calls:

```python
def run_dataset(ds: Dataset, rt: ResolvedTime, store, mgr,
                outputs: dict, uploads: Optional[dict] = None) -> DatasetResult:
    """Run one dataset, capturing any failure as an error on the result."""
    qsql, df, error = _fetch(ds, rt, store, mgr, outputs, uploads)
    if error is not None:
        return DatasetResult(ds.name, None, qsql, error,
                             waiting=error.startswith("waiting for"))
```

Leave the rest of `run_dataset` as it is. Then:

In each of the three below, change only the two lines shown — the signature, and
the one call inside. Every existing docstring and body line stays exactly as it
is.

```python
def run_dataset_steps(ds: Dataset, rt: ResolvedTime, store, mgr,
                      outputs: dict,
                      uploads: Optional[dict] = None) -> DatasetTrace:
    # (docstring unchanged)
    qsql, df, error = _fetch(ds, rt, store, mgr, outputs, uploads)
```

```python
def run_datasets(dashboard: Dashboard, store, mgr, today: date,
                 uploads: Optional[dict] = None) -> dict[str, DatasetResult]:
    # (docstring and the loop above unchanged)
        res = run_dataset(ds, rt, store, mgr, outputs, uploads)
```

```python
def trace_datasets(dashboard: Dashboard, store, mgr, today: date,
                   uploads: Optional[dict] = None) -> dict[str, DatasetTrace]:
    # (docstring and the loop above unchanged)
        trace = run_dataset_steps(ds, rt, store, mgr, outputs, uploads)
```

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_file_datasets.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 7: Run the whole suite**

Run: `PYTHONPATH=. python -m pytest tests -q`
Expected: all pass — every new parameter has a default.

- [ ] **Step 8: Commit**

```bash
git add kdbmonitor/core/dataset.py tests/test_file_datasets.py
git commit -m "feat: an uploaded frame runs the same pipeline a query does"
```

---

## Task 10: Validation splits by source

**Files:**
- Modify: `kdbmonitor/ui/dashboard_editor.py:316-460`
- Test: `tests/test_dashboard_validation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_validation.py`:

```python
from kdbmonitor.core.dashboard_models import (
    ColumnSpec, Dashboard, Dataset, FileShape, Row, Widget,
)
from kdbmonitor.ui.dashboard_editor import _file_dataset_problems


def _ds(**kw) -> Dataset:
    kw.setdefault("name", "orders")
    kw.setdefault("env", "")
    kw.setdefault("source", "file")
    return Dataset(**kw)


def test_a_file_dataset_with_no_shape_is_a_problem():
    problems = _file_dataset_problems(_ds(shape=None))
    assert any("shape" in p for p in problems)


def test_a_shape_with_no_columns_is_a_problem():
    problems = _file_dataset_problems(_ds(shape=FileShape()))
    assert any("column" in p for p in problems)


def test_two_columns_with_one_name_is_a_problem():
    shape = FileShape(columns=[ColumnSpec(name="qty"), ColumnSpec(name="qty")])
    assert any("qty" in p for p in _file_dataset_problems(_ds(shape=shape)))


def test_a_column_with_no_name_is_a_problem():
    shape = FileShape(columns=[ColumnSpec(name="  ")])
    assert _file_dataset_problems(_ds(shape=shape))


def test_data_starting_on_the_header_line_is_a_problem():
    shape = FileShape(header_row=3, data_start=3,
                      columns=[ColumnSpec(name="qty")])
    assert any("header" in p for p in _file_dataset_problems(_ds(shape=shape)))


def test_a_well_formed_file_dataset_has_no_problems():
    shape = FileShape(header_row=0, data_start=1,
                      columns=[ColumnSpec(name="sym"), ColumnSpec(name="qty")])
    assert _file_dataset_problems(_ds(shape=shape)) == []


def test_a_dataset_of_the_wrong_kind_for_its_dashboard_is_a_problem():
    """Spec §10: a dashboard's source governs its datasets. Switching a saved
    dashboard from KDB to file leaves its old datasets behind, and a dataset
    with no shape reading from a server that is no longer consulted would
    otherwise fail silently at run time."""
    from kdbmonitor.ui.dashboard_editor import validate

    class _Store:
        def list_environments(self):
            return {}

    dash = Dashboard(id=1, name="Mixed", source="file",
                     datasets=[_ds(source="kdb", env="prod")],
                     rows=[Row(widgets=[Widget(type="table",
                                               dataset="orders")])])
    assert any("uploaded file" in p for p in validate(dash, _Store()))


def test_a_file_dataset_is_never_asked_for_an_environment():
    """The KDB checks must not run against it: there is no server."""
    from kdbmonitor.ui.dashboard_editor import validate

    class _Store:
        def list_environments(self):
            return {}

    shape = FileShape(columns=[ColumnSpec(name="sym")])
    dash = Dashboard(id=1, name="Orders", source="file",
                     datasets=[_ds(shape=shape)],
                     rows=[Row(widgets=[Widget(type="table",
                                               dataset="orders")])])
    joined = " ".join(validate(dash, _Store()))
    assert "environment" not in joined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_dashboard_validation.py -v`
Expected: FAIL with `ImportError: cannot import name '_file_dataset_problems'`

- [ ] **Step 3: Extract the KDB checks into their own function**

In `kdbmonitor/ui/dashboard_editor.py`, take the body of the `for ds in draft.datasets:` loop in `validate` (lines ~339-460) and split it. Add above `validate`:

```python
def _file_dataset_problems(ds: Dataset) -> list[str]:
    """Everything wrong with a file-backed dataset, in plain English.

    None of the KDB checks apply: a file dataset has no environment, no period
    and no query. What it has instead is a shape, and a shape nothing can be
    read through is the one way it fails before anyone uploads anything.
    """
    out: list[str] = []
    shape = ds.shape
    if shape is None:
        out.append(f"Dataset '{ds.name}' has no shape yet — upload a sample "
                   f"file and confirm where its table sits.")
        return out

    names = [c.name.strip() for c in shape.columns]
    if not names:
        out.append(f"Dataset '{ds.name}' has no columns — confirm its shape "
                   f"against a sample file.")
    if any(not n for n in names):
        out.append(f"Dataset '{ds.name}' has a column with no name.")
    for name in sorted({n for n in names if n and names.count(n) > 1}):
        out.append(f"Dataset '{ds.name}' has two columns called '{name}'.")
    if shape.data_start <= shape.header_row:
        out.append(f"Dataset '{ds.name}': data starts on or above its header "
                   f"line — the header is line {shape.header_row + 1}.")
    return out


def _kdb_dataset_problems(ds: Dataset, envs: dict,
                          dashboard_time) -> list[str]:
    """Everything wrong with a KDB-backed dataset.

    Lifted out of ``validate`` whole when file datasets arrived: nearly all of
    it is about servers, periods and q, and wrapping each check in a source test
    would have made a long function longer without making it clearer.
    """
    problems: list[str] = []
    if _blank(ds.env):
        problems.append(f"Dataset '{ds.name}' has no environment selected.")

    if ds.mode == "raw" and _blank(ds.raw_qsql):
        problems.append(f"Dataset '{ds.name}' is set to raw q but the query is "
                        f"empty.")

    for i, f in enumerate(ds.filters, start=1):
        if _blank(f.column):
            problems.append(f"Dataset '{ds.name}', filter {i}: no column chosen.")
        if _blank(f.value):
            problems.append(f"Dataset '{ds.name}', filter {i} on "
                            f"'{f.column}': no value entered.")

    if ds.time_mode == "realtime":
        rt = resolve({"mode": "realtime"}, date.today())
    elif ds.time_mode == "custom":
        rt = resolve(ds.time_context or {"mode": "realtime"}, date.today())
    else:
        rt = dashboard_time

    # Market-data environments hold reference data: no date partitioning, so
    # the period simply does not apply to them.
    market = ds.env in envs and is_marketdata_env(envs[ds.env])
    if market:
        rt = resolve({"mode": "realtime"}, date.today())

    if ds.env not in envs:
        problems.append(f"Dataset '{ds.name}' uses unknown environment "
                        f"'{ds.env}'.")
    elif not market and envs[ds.env][rt.mode] is None:
        solo = standalone_side(envs[ds.env])
        wanted = "date ranges" if rt.mode == "historical" else "real-time"
        if solo:
            problems.append(
                f"Dataset '{ds.name}': environment '{ds.env}' is "
                f"{KIND_LABELS[solo].lower()} only, so it cannot show "
                f"{wanted}. Give this dataset a period it can answer.")
        else:
            problems.append(f"Dataset '{ds.name}': environment '{ds.env}' has "
                            f"no {KIND_LABELS[rt.mode].lower()} server — add "
                            f"one in Admin.")

    if rt.mode == "historical" and ds.mode == "raw" \
            and not has_date_constraint(ds.raw_qsql or ""):
        problems.append(
            f"Dataset '{ds.name}' is historical but its q never constrains "
            "'date'. Add a date within ({{date_from}};{{date_to}}) clause.")

    if ds.mode == "guided" and not ds.table:
        problems.append(f"Dataset '{ds.name}' has no table selected.")
    return problems
```

- [ ] **Step 4: Rewrite the loop in `validate` to dispatch**

Replace the per-dataset loop body in `validate` with:

```python
    seen: list[str] = []
    for ds in draft.datasets:
        if _blank(ds.name):
            problems.append("A dataset has no name.")
        if ds.name in seen:
            problems.append(f"Duplicate dataset name '{ds.name}'.")
        seen.append(ds.name)

        for i, t in enumerate(ds.transforms, start=1):
            problems += _transform_problems(ds.name, i, t)

        # Spec §10: the dashboard's source governs its datasets. Switching a
        # saved dashboard between sources leaves the old ones behind, and a
        # dataset nobody is going to run is worth saying so about now rather
        # than showing an empty panel later.
        if ds.source != draft.source:
            reads = ("an uploaded file" if draft.source == "file"
                     else "KDB queries")
            problems.append(
                f"Dataset '{ds.name}' does not match this dashboard, which "
                f"reads {reads}. Delete it, or change the dashboard's source.")
        elif ds.source == "file":
            problems += _file_dataset_problems(ds)
        else:
            problems += _kdb_dataset_problems(ds, envs, dashboard_time)
```

Also guard the period checks at the top of `validate`, since a file dashboard
has no period:

```python
    if draft.source != "file":
        draft.time_context = coerce_spec(draft.time_context, draft.periods)
        dashboard_time = resolve(draft.time_context, date.today())
        problems += _periods_problems(draft, envs)
    else:
        dashboard_time = resolve({"mode": "realtime"}, date.today())
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_dashboard_validation.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 6: Run the whole suite**

Run: `PYTHONPATH=. python -m pytest tests -q`
Expected: all pass. The KDB checks are unchanged, only relocated.

- [ ] **Step 7: Commit**

```bash
git add kdbmonitor/ui/dashboard_editor.py tests/test_dashboard_validation.py
git commit -m "refactor: a dataset is checked against the source it reads from"
```

---

## Task 11: Widget pickers read the shape

**Files:**
- Modify: `kdbmonitor/ui/dashboard_editor.py:160-190`
- Test: `tests/test_ui_dashboards.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_dashboards.py`:

```python
from kdbmonitor.core.dashboard_models import (
    ColumnSpec, Dataset, FileShape, Transform,
)
from kdbmonitor.ui.dashboard_editor import dataset_columns


def test_a_file_dataset_offers_the_columns_its_shape_declares():
    """No sample in hand after reopening — the contract is what is stored."""
    ds = Dataset(name="orders", env="", source="file",
                 shape=FileShape(columns=[ColumnSpec(name="sym"),
                                          ColumnSpec(name="qty")]))
    assert dataset_columns(ds, None) == ["sym", "qty"]


def test_a_file_dataset_still_accounts_for_its_transforms():
    ds = Dataset(name="orders", env="", source="file",
                 shape=FileShape(columns=[ColumnSpec(name="sym"),
                                          ColumnSpec(name="qty")]),
                 transforms=[Transform(kind="groupby", params={
                     "keys": ["sym"],
                     "aggs": [{"column": "qty", "func": "sum",
                               "as": "total"}]})])
    assert dataset_columns(ds, None) == ["sym", "total"]


def test_a_file_dataset_with_no_shape_offers_nothing_rather_than_raising():
    ds = Dataset(name="orders", env="", source="file", shape=None)
    assert dataset_columns(ds, None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_ui_dashboards.py -k file -v`
Expected: FAIL — `dataset_columns` returns `[]` for the first two tests

- [ ] **Step 3: Add the file branch**

In `kdbmonitor/ui/dashboard_editor.py`, in `dataset_columns`, replace:

```python
    if ds.mode == "raw" or conn is None:
        cols = list(learned or [])
    else:
        cols = list(getattr(conn, "schema", {}).get(ds.table, []))
```

with:

```python
    if ds.source == "file":
        # The shape is the contract and it is stored, so a file dataset's
        # columns are known without a sample, a server or a run — which is what
        # makes a dashboard editable again after it is reopened.
        cols = [c.name for c in (ds.shape.columns if ds.shape else [])]
    elif ds.mode == "raw" or conn is None:
        cols = list(learned or [])
    else:
        cols = list(getattr(conn, "schema", {}).get(ds.table, []))
```

Also update the docstring's first paragraph to mention the file case.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_ui_dashboards.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/ui/dashboard_editor.py tests/test_ui_dashboards.py
git commit -m "feat: a file dataset's columns are known without a sample"
```

---

## Task 12: The shape editor

**Files:**
- Create: `kdbmonitor/ui/fileshape.py`
- Modify: `kdbmonitor/ui/dashboard_editor.py` (`_dataset_card`, ~787-903)

No unit tests: this is Streamlit, and everything it decides is already tested in
`filesource`. Verify by running the app (Step 5).

- [ ] **Step 1: Create the module**

Create `kdbmonitor/ui/fileshape.py`:

```python
"""The design-time shape editor: point at a sample, say where its table is.

Nothing here decides anything. Every rule about reading a file lives in
``core.filesource``; this module shows a grid, collects the declaration, and
stores it. The sample it reads is held in session state and never written to the
database — see the spec, §7.1.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from kdbmonitor.core.dashboard_models import (
    COLUMN_TYPES, ColumnSpec, Dataset, FileShape, NamedCell,
)
from kdbmonitor.core.filesource import load_grid, profile_columns, read_grid

# How much of a sample is kept for the editing session. Streamlit takes 200MB
# uploads by default, and holding one whole for the sake of a preview is a great
# deal to pay for a glance.
SAMPLE_ROWS = 200
GRID_ROWS = 12                 # how much of the file the grid shows


def sample_key(ds_name: str) -> str:
    return f"fs_sample_{ds_name}"


def stored_sample(ds_name: str):
    """The sample frame kept for this editing session, if there is one."""
    held = st.session_state.get(sample_key(ds_name))
    return held.get("df") if held else None


def _grid_frame(grid: list[list[str]], shape: FileShape) -> pd.DataFrame:
    """The raw file as a table, labelled the way the controls talk about it.

    Rows are numbered from 1 because that is what the header-line control says
    and what a refusal quotes; a grid counting from 0 beside a message saying
    "line 3" is a trap.
    """
    body = grid[:GRID_ROWS]
    width = max((len(r) for r in body), default=0)
    frame = pd.DataFrame([r + [""] * (width - len(r)) for r in body],
                         columns=[f"col {i + 1}" for i in range(width)])
    frame.index = [f"line {i + 1}" for i in range(len(body))]
    return frame


def render(ds: Dataset, key: str) -> None:
    """The shape editor for one file dataset."""
    if ds.shape is None:
        ds.shape = FileShape()
    shape = ds.shape

    ds.file_label = st.text_input(
        "What to ask for", value=ds.file_label, key=f"{key}_label",
        placeholder="your orders export",
        help="The prompt on the upload box when somebody runs this dashboard.")

    upload = st.file_uploader(
        "Sample file", type=["csv"], key=f"{key}_sample",
        help="Read to work out the shape, then discarded. Nothing from it is "
             "saved with the dashboard.")

    if upload is not None:
        try:
            grid = read_grid(upload.getvalue())
        except ValueError as exc:
            st.error(str(exc), icon=":material/error:")
            return
        st.session_state[sample_key(ds.name)] = {"grid": grid[:SAMPLE_ROWS]}

    held = st.session_state.get(sample_key(ds.name))
    if not held:
        st.info("Drop in a sample file to say where its table sits.",
                icon=":material/upload_file:")
        return

    grid = held["grid"]
    st.dataframe(_grid_frame(grid, shape), use_container_width=True)

    c = st.columns(4)
    shape.header_axis = c[0].selectbox(
        "Headers run", ["row", "column"],
        index=0 if shape.header_axis == "row" else 1, key=f"{key}_axis",
        format_func=lambda a: "across a line" if a == "row" else "down a column",
        help="Down a column means each column of the file is one record.")
    shape.header_row = int(c[1].number_input(
        "Header line", 1, 1000, shape.header_row + 1, key=f"{key}_hrow",
        help="Counted as the grid above counts. Whoever runs this dashboard "
             "must have their headers on this line too.")) - 1
    shape.first_col = int(c[2].number_input(
        "Table starts at column", 1, 1000, shape.first_col + 1,
        key=f"{key}_fcol")) - 1
    shape.data_start = int(c[3].number_input(
        "Data starts on line", 1, 1000, shape.data_start + 1,
        key=f"{key}_dstart")) - 1

    shape.null_markers = [m.strip() for m in st.text_input(
        "Read as missing", value=", ".join(shape.null_markers),
        key=f"{key}_nulls",
        help="Comma-separated. Take a marker off the list if it is a real "
             "value in your data — a side of '-' would otherwise be blanked."
    ).split(",")]

    if st.button("Read the columns from this sample", key=f"{key}_profile",
                 icon=":material/refresh:"):
        found = profile_columns(grid, shape)
        by_name = {c.name: c for c in shape.columns}
        # Keep what was already corrected: re-reading a sample must not undo the
        # designer's judgement that an order ID is text.
        shape.columns = [by_name.get(c.name, c) for c in found]
        st.rerun()

    _columns_form(shape, key)
    _cells_form(shape, grid, key)
    _check(grid, shape, ds.name)


def _columns_form(shape: FileShape, key: str) -> None:
    st.caption("**Columns this dashboard needs.** Types are a first reading of "
               "your sample — correct any that are wrong, since a column of "
               "integer-looking order IDs is text.")
    for i, spec in enumerate(list(shape.columns)):
        c = st.columns([3, 2, 1.4, 1.4, 0.7], vertical_alignment="bottom")
        spec.name = c[0].text_input("Name", value=spec.name, key=f"{key}_n{i}")
        spec.type = c[1].selectbox(
            "Type", list(COLUMN_TYPES), key=f"{key}_t{i}",
            index=list(COLUMN_TYPES).index(spec.type)
            if spec.type in COLUMN_TYPES else list(COLUMN_TYPES).index("text"))
        spec.required = c[2].checkbox("Required", value=spec.required,
                                      key=f"{key}_r{i}")
        spec.allow_null = not c[3].checkbox(
            "No gaps", value=not spec.allow_null, key=f"{key}_g{i}",
            help="Refuse a file with blanks here. Worth setting on the column "
                 "a chart is plotted against.")
        if c[4].button("", icon=":material/delete:", key=f"{key}_x{i}"):
            shape.columns.pop(i)
            st.rerun()


def _cells_form(shape: FileShape, grid: list[list[str]], key: str) -> None:
    with st.expander(f"Named cells ({len(shape.cells)})",
                     icon=":material/my_location:"):
        st.caption("A single cell outside the table — a report date in line 1, "
                   "say. Addressed against the grid above, as the file is "
                   "written.")
        for i, cell in enumerate(list(shape.cells)):
            c = st.columns([3, 1.4, 1.4, 1.6, 0.7], vertical_alignment="bottom")
            cell.name = c[0].text_input("Name", value=cell.name,
                                        key=f"{key}_cn{i}")
            cell.row = int(c[1].number_input("Line", 1, 1000, cell.row + 1,
                                             key=f"{key}_cr{i}")) - 1
            cell.col = int(c[2].number_input("Column", 1, 1000, cell.col + 1,
                                             key=f"{key}_cc{i}")) - 1
            cell.type = c[3].selectbox(
                "Type", list(COLUMN_TYPES), key=f"{key}_ct{i}",
                index=list(COLUMN_TYPES).index(cell.type)
                if cell.type in COLUMN_TYPES else list(COLUMN_TYPES).index("text"))
            if c[4].button("", icon=":material/delete:", key=f"{key}_cx{i}"):
                shape.cells.pop(i)
                st.rerun()
        if st.button("Name a cell", key=f"{key}_addcell", icon=":material/add:"):
            shape.cells.append(NamedCell(name=f"cell {len(shape.cells) + 1}"))
            st.rerun()


def _check(grid: list[list[str]], shape: FileShape, ds_name: str) -> None:
    """Read the sample back through the real loader, and keep what it produced.

    ``load_grid`` is the very function a viewer's upload goes through, so what
    the designer sees here — including the refusals — is exactly what their
    colleague will see. The frame it returns is held for the transform preview
    (spec §7.1); it is session state and is never saved.
    """
    if not shape.columns:
        return
    out = load_grid(grid, shape)
    if out.problems:
        for problem in out.problems:
            st.error(problem.message, icon=":material/error:")
        return
    for note in out.notes:
        st.caption(f":gray[{note}]")
    st.success(f"Reads {len(out.df):,} row(s) from this sample.",
               icon=":material/check:")
    st.dataframe(out.df.head(20), use_container_width=True)

    held = st.session_state.get(sample_key(ds_name)) or {}
    st.session_state[sample_key(ds_name)] = {**held,
                                             "df": out.df.head(SAMPLE_ROWS)}
```

Note the import line at the top of the module is
`from kdbmonitor.core.filesource import load_grid, profile_columns, read_grid`
— `load_grid`, not `load`. And the call at the end of `render` is
`_check(grid, shape, ds.name)`.

- [ ] **Step 2: Split the dataset card by source**

In `kdbmonitor/ui/dashboard_editor.py`, `_dataset_card` currently runs
name/environment/period/max-rows (lines ~792-812), then the query controls
(~814-853), then the transforms block (~855-901). The transforms block is shared
verbatim — transforms apply to a file frame identically — so only the middle
changes.

Replace the expander header and the head row (lines ~791-812) with:

```python
    with st.expander(f"**{ds.name}** · "
                     f"{(ds.file_label or 'an uploaded file') if ds.source == 'file' else (ds.env or 'no environment')}",
                     expanded=True):
        if ds.source == "file":
            head = st.columns([3, 1.8, 0.7], vertical_alignment="bottom")
            ds.name = head[0].text_input("Name", value=ds.name, key=f"{key}_n")
            ds.max_rows = int(head[1].number_input(
                "Max rows", 1, 1_000_000, ds.max_rows, step=100,
                key=f"{key}_mr"))
            if head[2].button("", icon=":material/delete:", key=f"{key}_del"):
                draft.datasets.pop(index)
                _forget(r"ds\d+")              # every card below renumbers
                st.rerun()
            from kdbmonitor.ui import fileshape
            fileshape.render(ds, key=key)
        else:
            head = st.columns([2, 2, 1.8, 1.6, 0.7], vertical_alignment="bottom")
            ...the existing name/env/period/max-rows/delete block, unchanged...
```

Then wrap the query controls (the `if ds.time_mode == "custom":` block through
the end of the raw/guided section, ~814-853) in `if ds.source != "file":`.

Leave the transforms block from `st.markdown("**Transforms**")` onwards outside
that guard, so it runs for both.

- [ ] **Step 3: Carry the source when rebuilding the upstream dataset**

Still in `_dataset_card`, the transforms block builds a throwaway `Dataset` to
work out which columns each transform can see (line ~885). It does not copy the
new fields, so a file dataset's transform forms would offer no columns at all:

```python
                upstream = Dataset(name=ds.name, env=ds.env, mode=ds.mode,
                                   table=ds.table, transforms=ds.transforms[:i],
                                   source=ds.source, shape=ds.shape)
```

- [ ] **Step 4: Add the import**

At the top of `kdbmonitor/ui/dashboard_editor.py`, the `dashboard_models` import
needs the new names:

```python
from kdbmonitor.core.dashboard_models import (
    COLUMN_TYPES, ColumnSpec, Component, Dashboard, Dataset, FileShape,
    NamedCell, Row, Transform, Widget, widget_from_dict, widget_to_dict,
)
```

Keep whatever names were already imported; add only those that are missing.

- [ ] **Step 5: Verify by running the app**

Run: `streamlit run app.py`
Then: create a dashboard, set its source to File (Task 13 adds that control — do
this step after Task 13 if the selector is not there yet), add a dataset, upload
a CSV with two preamble lines, set the header line to 3, press *Read the columns
from this sample*, and confirm the preview table shows your rows.

- [ ] **Step 6: Commit**

```bash
git add kdbmonitor/ui/fileshape.py kdbmonitor/ui/dashboard_editor.py
git commit -m "feat: a sample file is where you say the table sits"
```

---

## Task 13: Choosing a file dashboard, and hiding what does not apply

**Files:**
- Modify: `kdbmonitor/ui/dashboard_editor.py` (`render`, ~1388-1420; `_render_data`, ~904)

- [ ] **Step 1: Add the source selector**

`render` currently opens with:

```python
        head = st.columns([3, 3, 1.4, 1.2, 1.2], vertical_alignment="bottom")
        draft.name = head[0].text_input("Dashboard name", value=draft.name)
        draft.description = head[1].text_input("Description",
                                               value=draft.description)
```

and later uses `head[2]` for Save. Widen the row and insert the selector at
index 2, which pushes Save to index 3:

```python
        head = st.columns([2.6, 2.6, 1.8, 1.4, 1.2, 1.2],
                          vertical_alignment="bottom")
        draft.name = head[0].text_input("Dashboard name", value=draft.name)
        draft.description = head[1].text_input("Description",
                                               value=draft.description)
        sources = ["kdb", "file"]
        draft.source = head[2].selectbox(
            "Data from", sources,
            index=sources.index(draft.source if draft.source in sources
                                else "kdb"),
            format_func=lambda s: "KDB queries" if s == "kdb"
            else "An uploaded file",
            help="A file dashboard is a template: whoever opens it uploads "
                 "their own file of the shape you profile here. It has no "
                 "environment, no period and no refresh interval.")
```

Then change the Save button from `head[2].button(...)` to `head[3].button(...)`.
Search `render` for any other `head[` index and shift each one up by one.

- [ ] **Step 2: Hide the period row on a file dashboard**

Wrap the existing periods block:

```python
        if draft.source != "file":
            p = st.columns([2.4, 4.2, 2.4], vertical_alignment="center")
            ...existing periods and orientation controls...
        else:
            p = st.columns([2.4, 7.6], vertical_alignment="center")
            ways = list(ORIENTATIONS)
            draft.orientation = p[0].selectbox(...)   # keep this one
            p[1].caption("This dashboard reads an uploaded file, so it has no "
                         "environment, no period and no refresh interval — "
                         "its numbers change when somebody uploads a "
                         "different file.")
```

Keep the orientation selectbox in both branches: a printed page is a printed
page whatever the data came from.

- [ ] **Step 3: Make Add dataset create the right kind**

In `_render_data`, replace the `Add dataset` handler:

```python
    if st.button("Add dataset", icon=":material/add:", type="primary"):
        envs = sorted(store.list_environments())
        draft.datasets.append(Dataset(
            name=unique_name("dataset", [d.name for d in draft.datasets]),
            env="" if draft.source == "file" else (envs[0] if envs else ""),
            source=draft.source))
        st.rerun()
```

And soften the no-connections warning, which is meaningless here:

```python
    if draft.source != "file" and not store.list_environments():
        st.warning("No connections yet — add one in Admin first.",
                   icon=":material/warning:")
```

- [ ] **Step 4: Feed the held sample to the step preview**

In `run_preview`, pass the samples held by the shape editor:

```python
def run_preview(store, mgr, draft: Dashboard):
    from kdbmonitor.ui.fileshape import stored_sample
    uploads = {ds.name: stored_sample(ds.name) for ds in draft.datasets
               if ds.source == "file" and stored_sample(ds.name) is not None}
    return trace_datasets(draft, store, mgr, date.today(), uploads=uploads)
```

Keep the rest of `run_preview` as it is.

- [ ] **Step 5: Run the suite**

Run: `PYTHONPATH=. python -m pytest tests -q`
Expected: all pass.

- [ ] **Step 6: Verify by running the app**

Run: `streamlit run app.py`
Create a dashboard, set *Data from* to **An uploaded file**, and confirm the
period control is replaced by the explanatory caption and that adding a dataset
gives you the shape editor rather than an environment picker.

- [ ] **Step 7: Commit**

```bash
git add kdbmonitor/ui/dashboard_editor.py
git commit -m "feat: a dashboard can say its data arrives by upload"
```

---

## Task 14: The upload panel, and no timer

**Files:**
- Modify: `kdbmonitor/ui/dashboards.py` (`refresh` ~193, `_render_view` ~409)
- Test: `tests/test_ui_dashboards.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_dashboards.py`:

```python
from datetime import datetime, timedelta

from kdbmonitor.core.dashboard_models import Dashboard
from kdbmonitor.ui.dashboards import comes_due


def test_a_kdb_dashboard_comes_due_on_its_interval():
    dash = Dashboard(id=1, name="K", refresh_secs=15)
    stale = datetime.now() - timedelta(seconds=30)
    assert comes_due(dash, stale)


def test_a_file_dashboard_never_comes_due_however_stale():
    """Nothing to re-fetch, and a tick would throw away the printed pages."""
    dash = Dashboard(id=1, name="F", source="file", refresh_secs=15)
    stale = datetime.now() - timedelta(days=1)
    assert not comes_due(dash, stale)


def test_refresh_off_still_never_comes_due():
    dash = Dashboard(id=1, name="K", refresh_secs=0)
    assert not comes_due(dash, datetime.now() - timedelta(days=1))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_ui_dashboards.py -k due -v`
Expected: FAIL with `ImportError: cannot import name 'comes_due'`

- [ ] **Step 3: Add `comes_due` and use it in `refresh`**

In `kdbmonitor/ui/dashboards.py`, after `is_due`, add:

```python
def comes_due(dashboard: Dashboard, as_of: datetime,
              now: datetime | None = None) -> bool:
    """Whether this dashboard's frames should be taken again.

    A file dashboard never does. Its frame changes when somebody uploads a
    different file and at no other moment, so a tick could only discard the
    cached printed pages to arrive back at the numbers already on screen.
    """
    if dashboard.source == "file":
        return False
    return is_due(as_of, dashboard.refresh_secs, now)
```

In `refresh`, change the signature and the staleness test:

```python
def refresh(store, mgr, dashboard: Dashboard,
            uploads: dict | None = None) -> dict:
    # (docstring unchanged)
    cached = st.session_state.get(frames_key(dashboard.id))
    if cached and not comes_due(dashboard, cached["as_of"]):
        return cached

    if dashboard.source != "file":
        dashboard.time_context = coerce_spec(dashboard.time_context,
                                             dashboard.periods)
    payload = {"results": run_datasets(dashboard, store, mgr, date.today(),
                                       uploads=uploads),
               "as_of": datetime.now(),
               "rt": resolve(dashboard.time_context, date.today())}
```

- [ ] **Step 4: Add the upload panel**

In `kdbmonitor/ui/dashboards.py`, add above `_render_view`:

```python
def uploads_key(dashboard_id: int) -> str:
    return f"dash_uploads_{dashboard_id}"


def _render_uploads(dashboard: Dashboard) -> dict:
    """One upload box per file dataset, and the frames they produced.

    The file is read and checked here, the moment it is dropped in, rather than
    on the way through the dashboard: a refusal belongs beside the thing that
    caused it, and reading it once means it is not re-read on every rerun.
    """
    from kdbmonitor.core.filesource import load

    held = st.session_state.setdefault(uploads_key(dashboard.id), {})
    datasets = [ds for ds in dashboard.datasets if ds.source == "file"]
    if not datasets:
        return {}

    with st.container(border=True):
        for ds in datasets:
            upload = st.file_uploader(
                ds.file_label or f"File for '{ds.name}'", type=["csv"],
                key=f"up_{dashboard.id}_{ds.name}")
            if upload is None:
                continue
            token = (upload.name, upload.size)
            if held.get(ds.name, {}).get("token") == token:
                continue            # already read; a rerun is not a new file
            out = load(upload.getvalue(), ds.shape)
            if out.problems:
                for problem in out.problems:
                    st.error(problem.message, icon=":material/error:")
                continue            # a refusal leaves the previous frame standing
            for note in out.notes:
                st.caption(f":gray[{note}]")
            st.success(f"{ds.name}: {len(out.df):,} row(s) read.",
                       icon=":material/check:")
            held[ds.name] = {"token": token, "df": out.df}
            force_refresh(dashboard.id)

    return {name: kept["df"] for name, kept in held.items()}
```

- [ ] **Step 5: Wire it into the view**

In `_render_view`, replace the auto-refresh selectbox block with a guard, and
call the panel before `_live`:

```python
    if dashboard.source != "file":
        labels = list(REFRESH_OPTIONS)
        current = next((k for k, v in REFRESH_OPTIONS.items()
                        if v == dashboard.refresh_secs), "15s")
        chosen = top[1].selectbox("Auto-refresh", labels,
                                  index=labels.index(current),
                                  key=f"rf_{dashboard.id}")
        if REFRESH_OPTIONS[chosen] != dashboard.refresh_secs:
            dashboard.refresh_secs = REFRESH_OPTIONS[chosen]
            store.update_dashboard(dashboard)
            st.rerun()
```

and:

```python
    if dashboard.source != "file":
        _render_period(store, dashboard,
                       st.session_state.get(frames_key(dashboard.id)))

    uploads = _render_uploads(dashboard)

    @st.fragment(run_every=None if dashboard.source == "file"
                 else (dashboard.refresh_secs or None))
    def _live() -> None:
        data = refresh(store, mgr, dashboard, uploads)
        render_rows(dashboard, data["results"])
```

- [ ] **Step 6: Run the suite**

Run: `PYTHONPATH=. python -m pytest tests -q`
Expected: all pass.

- [ ] **Step 7: Verify by running the app**

Run: `streamlit run app.py`
Open the file dashboard, upload a matching CSV — the widgets fill. Then upload
one missing a required column — a red message names the column and lists what
did arrive, and the previous numbers stay on screen.

- [ ] **Step 8: Commit**

```bash
git add kdbmonitor/ui/dashboards.py tests/test_ui_dashboards.py
git commit -m "feat: a file is checked where it is dropped in"
```

---

## Task 15: A file dashboard prints

**Files:**
- Test: `tests/test_file_datasets.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_file_datasets.py`:

```python
from datetime import datetime

from kdbmonitor.core.dashpdf import dashboard_to_pdf_bytes, page_count
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.timectx import ResolvedTime

AS_OF = datetime(2026, 7, 30, 9, 15)
RT = ResolvedTime("realtime", None, None)


def _printable() -> Dashboard:
    dash = _dash()
    dash.rows = [Row(widgets=[Widget(type="table", dataset="orders",
                                     title="Working orders")]),
                 Row(widgets=[Widget(type="line", dataset="orders",
                                     title="Quantity",
                                     spec={"x": "sym", "y": "qty"})])]
    return dash


def test_a_file_dashboard_prints_a_pdf():
    results = run_datasets(_printable(), None, None, TODAY,
                           uploads={"orders": _frame()})
    out = dashboard_to_pdf_bytes(_printable(), results, RT, AS_OF)
    assert out.startswith(b"%PDF")


def test_a_widget_cannot_tell_a_file_dataset_from_a_query():
    """The proof the pipeline is shared rather than parallel."""
    from kdbmonitor.core.dataset import DatasetResult

    widget = Widget(type="table", dataset="orders", title="Orders")
    from_file = run_datasets(_dash(), None, None, TODAY,
                             uploads={"orders": _frame()})
    from_kdb = {"orders": DatasetResult("orders", _frame(), "select from o",
                                        None, row_count=3)}

    assert (build_plot_model(widget, from_file).rows
            == build_plot_model(widget, from_kdb).rows)


def test_a_dashboard_waiting_for_a_file_still_prints_rather_than_crashing():
    results = run_datasets(_printable(), None, None, TODAY, uploads={})
    assert dashboard_to_pdf_bytes(_printable(), results, RT,
                                  AS_OF).startswith(b"%PDF")
    assert page_count(_printable(), results) >= 1
```

- [ ] **Step 2: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_file_datasets.py -v`
Expected: PASS. If the third test fails, `plotmodel` is raising on a `None`
frame rather than producing an error panel — fix it there, in
`build_plot_model`, so the waiting message prints like any other panel error.

- [ ] **Step 3: Run the whole suite**

Run: `PYTHONPATH=. python -m pytest tests -q`
Expected: all pass.

- [ ] **Step 4: Update the README**

In `README.md`, immediately before the `### PDF` heading, add:

```markdown
### Dashboards from a file

A dashboard can read an uploaded CSV instead of KDB. Set **Data from** to *An
uploaded file* and it becomes a template: you profile one sample here, and
whoever opens the dashboard uploads their own file of the same shape and sees
their own numbers. It has no environment, no period and no refresh interval —
those describe a server, and there is no server.

Profiling is a declaration, not a discovery. You say which line carries the
headers (the first, unless the export has a preamble), whether they run across
the page or down it, and where the data starts. The one thing read from your
sample is each column's type, offered as a list you correct — a column of
integer-looking order IDs is text, and only you know that. You can also name a
single cell outside the table, a report date sitting in line 1, and it becomes a
value the dashboard can show.

**The sample is then discarded.** Only the shape and the column contract are
stored, so a dashboard you export carries no data at all — just the shape of
the data it expects.

A file somebody uploads is checked rather than trusted, and checked by *reading*
each column as the type you declared rather than by guessing a type from their
file. Integers satisfy a number column, `"125,000"` reads as `125000`, and
anything that genuinely will not read is refused with the column, how many
values broke, and the line of the first: *column 'qty' expects a number; 12 of
500 value(s) could not be read as one (line 14: 'N/A')*. A missing column lists
what did arrive. Headers anywhere but the declared line are refused, quoting the
line so you can see what the app saw — nothing is searched for, because an app
that decides a file is close enough does not fail when it is wrong, it reports
the wrong thing.

Blanks become real nulls and print as `—` like any other gap. `NA`, `N/A`,
`NULL`, `-` and a few others count as blank by default, and the list is editable
per dataset — worth taking `-` off it if `-` is a real value in your data. A
column can be marked *No gaps* to refuse a file with blanks in it at all, which
is worth setting on whatever a chart is plotted against.

Everything after that is ordinary: the same transforms, the same widgets, the
same layout, the same printed page.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_file_datasets.py README.md
git commit -m "test: a dashboard prints the same whether its data was queried or uploaded"
```

---

## Verification

Before calling this done:

```bash
PYTHONPATH=. python -m pytest tests -q
```

Expected: every test passes, including the ~90 added here.

Then run the app end to end:

```bash
streamlit run app.py
```

1. New dashboard → *Data from* → **An uploaded file**. The period control is
   gone, replaced by the explanation.
2. Add a dataset, upload a sample CSV with two preamble lines above the header.
   Set the header line to 3. Read the columns. Correct any type that is wrong.
3. Add a line chart and a table against it. Save.
4. Open the dashboard. Upload the same file — the widgets fill.
5. Upload a file missing a required column — the refusal names the column and
   lists what did arrive, and the previous numbers stay on screen.
6. Upload a file with `N/A` in a number column — the refusal quotes the value
   and the line.
7. Generate the PDF. It prints what is on screen.
