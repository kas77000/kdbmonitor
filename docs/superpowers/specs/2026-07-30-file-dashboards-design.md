# File-Backed Dashboards — Design

**Date:** 2026-07-30
**Status:** designed, not built.

## 1. Goal

Let a user build a dashboard whose data comes from a **file they upload** rather
than from a KDB query, and let *other* users open that dashboard, upload their
own file of the same shape, and see their own numbers.

The dashboard is a **template**. Its author profiles one sample file at design
time — where the headers are, which way they run, what type each column holds —
and stores that shape. At run time a viewer drops in their own file; it is
checked against the stored shape and either accepted or refused with a specific
reason.

The motivating case: a CSV export of working orders, rendered as a line chart
and a table, reproducible by a colleague against their own export of the same
report.

### Non-goals

- No background evaluation. A file dashboard is live only while its page is
  open, preserving the locked "no daemon" decision.
- No stored data. The uploaded file is never written to the database, and the
  sample used at design time is discarded once profiled.
- No new rendering. Widgets, transforms, layout and PDF export are unchanged;
  a file dataset produces the same `DatasetResult` a KDB dataset does.
- No joining two files, and no mixing a file dataset with a KDB dataset in one
  dashboard (see §10).

## 2. Decisions taken

Settled with the user before design, recorded here because each one closed off
alternatives that would otherwise look reasonable later.

| Question | Decision | Rejected |
| --- | --- | --- |
| Relationship to KDB dashboards | One `Dashboard` kind with a `source` field; file dashboards hide what is meaningless to them | A separate type (duplicates the layout editor, widget config and PDF export) |
| Where run-time data comes from | Each viewer uploads their own file; nothing is stored | One stored file per dashboard; a watched path on disk |
| Content outside the table | Skipped, except cells deliberately named at design time | Skipping entirely; full spreadsheet addressing |
| Shape matching | By column name, anchored, with a fallback search for the header line | Strict anchoring; positional matching |
| Automation | Profile only — widgets are built by hand, as for KDB datasets | Suggested widgets; a generated starter dashboard |
| File formats | CSV, strictly comma-separated, UTF-8 | Delimiter sniffing; Excel |
| Files per dashboard | One per dataset — each dataset is its own named upload slot | One file carved into many datasets |
| What is stored of the sample | The shape and the per-column type contract. No rows. | Storing the whole sample; storing a preview |
| Type checking | Coerce the viewer's column to the expected type; refuse only if values genuinely will not read | Comparing an inferred type label against the stored one |

Two of these carry a known risk the user accepted explicitly:

- **Strictly comma-separated CSV.** A semicolon-delimited export from a European
  locale is the most likely first real-world stumble. Mitigated by isolating all
  parsing behind `filesource.read_grid()`, so tolerance is a later change to one
  function rather than a change to the design.
- **Positional matching was rejected in favour of name matching**, which means a
  file with translated headers is refused rather than misread. That is the
  intended trade: a refusal is recoverable, confidently wrong numbers are not.

## 3. Concepts

```
Dashboard (source = "file")
 ├─ name, description
 ├─ datasets: list[Dataset]        each one an upload slot
 │    └─ shape: FileShape          where the table is, and what it must hold
 │         ├─ columns: list[ColumnSpec]
 │         └─ cells:   list[NamedCell]
 └─ rows: list[Row]                unchanged
      └─ widgets: list[Widget]     unchanged
```

A file dashboard has no environment, no real-time/historical period, and no
refresh interval — those describe a server, and there is no server. Everything
below the dataset is identical to a KDB dashboard.

## 4. What is stored

New dataclasses in `core/dashboard_models.py`, beside `Transform`, so they
serialise with the dashboard:

```python
@dataclass
class ColumnSpec:
    name: str                  # the header text the file must carry
    type: str                  # date | number | integer | text | boolean
    required: bool = True      # a column no widget references need not arrive
    allow_null: bool = True    # false: a blank in this column is a refusal


@dataclass
class NamedCell:
    name: str                  # "Report date"
    row: int                   # 0-based, from the top-left of the raw file
    col: int                   # 0-based
    type: str = "text"
    allow_null: bool = True


@dataclass
class FileShape:
    header_axis: str = "row"   # row = headers across | column = headers down
    header_row: int = 0        # 0-based line carrying the headers
    first_col: int = 0         # 0-based column the table starts at
    data_start: int = 1        # 0-based first data line
    search_rows: int = 20      # how far to hunt if the header is not where expected
    null_markers: list[str] = field(default_factory=lambda: [
        "", "NA", "N/A", "NaN", "NULL", "NONE", "-", "--", "#N/A"])
    columns: list[ColumnSpec] = field(default_factory=list)
    cells: list[NamedCell] = field(default_factory=list)
```

Existing dataclasses gain:

| Class | Field | Default | Meaning |
| --- | --- | --- | --- |
| `Dashboard` | `source` | `"kdb"` | `kdb` \| `file` |
| `Dataset` | `source` | `"kdb"` | `kdb` \| `file` |
| `Dataset` | `shape` | `None` | the `FileShape`, when `source == "file"` |
| `Dataset` | `file_label` | `""` | the prompt on the upload box |

`Dataset.env`, `time_mode`, `time_context`, `mode`, `table`, `filters` and
`raw_qsql` are unused by a file dataset. They stay on the dataclass, are hidden
by the editor, and are ignored by validation.

`transforms` and `max_rows` are **not** unused — they apply to a file frame
exactly as they apply to a query result, and are the reason a file dataset needs
no shaping vocabulary of its own.

### 4.1 Persistence, export and import

None of these need new code. Dashboards are stored as whole-object JSON
(`storage.add_dashboard` / `update_dashboard` via `dashboard_to_json`) and
exported the same way (`portability.export_dashboards_json`), so a new dataclass
field is picked up by `dashboard_to_dict` automatically. Reading back needs one
`d.get(...)` line per field in `dashboard_from_dict`, plus `_shape_from_dict`
for the nested structure — the same shape of change `orientation` took.

Dashboards saved before this feature read back as `source="kdb"`, `shape=None`.

Because no sample rows are stored, an exported bundle carries **no data** —
only the shape and the column contract.

## 5. Reading a file — `core/filesource.py`

One new module owns everything between bytes and a frame. It imports no
Streamlit and knows nothing about dashboards, so it is testable directly, in
keeping with the project's rule that logic lives in `core/`.

```python
@dataclass
class Problem:
    """One reason a file was refused, in the file's own terms."""
    column: str            # "" for problems about the file as a whole
    message: str           # "expects a number; 12 of 500 values could not be read"
    line: Optional[int]    # 1-based line in the file, where there is one
    sample: str = ""       # the offending value


@dataclass
class FileLoad:
    df: Optional[pd.DataFrame]     # None when refused
    cells: dict[str, Any]          # named cells, resolved
    problems: list[Problem]        # non-empty means refused
    notes: list[str]               # accepted, but worth saying
```

`load(data: bytes, shape: FileShape) -> FileLoad` runs six stages:

1. **`read_grid`** — decode UTF-8, parse as comma-separated, return a raw
   rectangular grid of strings. Every parsing assumption in the design lives
   here and nowhere else.
2. **Orient** — if `header_axis == "column"`, transpose the grid. Every index
   in `FileShape` refers to the grid *after* this step, so vertical headers cost
   one line rather than a second code path.
3. **Locate the header** — read line `header_row` from `first_col`. If the
   expected names are not there, scan the first `search_rows` lines for a line
   that carries them; on success, record `"headers found on line 4, not 3"` in
   `notes`. Drop blank headers (a trailing comma produces a column nothing can
   reference). Refuse duplicate header names — neither can be referenced
   unambiguously, and first-wins would be a silent choice.
4. **Cut the data region** — take lines from `data_start` down. Drop rows blank
   across the whole region, and note how many: trailing blank lines are
   near-universal in exports, and a row of nulls is not the same as no row.
5. **Normalise nulls and coerce types** — see §6.
6. **Resolve named cells** — read each `NamedCell` by `(row, col)` from the
   **raw grid, before orientation and before cutting**. A named cell addresses
   the file as it sits on disk, which is also the grid the designer clicked to
   create it (§7). Orientation applies to the table region only; if it applied
   to named cells too, turning on vertical headers would silently move every
   cell already named.

Any `Problem` means the whole load is refused: `df` is `None`. A partially
loaded frame would be worse than none, because it looks like data.

## 6. Missing values and types

The renderers are already null-safe — `plotmodel._fmt` turns `None`, `NaN`,
`NaT` and infinities into `—`, and `transform._no_infinities` handles division
by zero. So the requirement here is narrow and specific: **produce genuine
pandas nulls at read time**, never leave the string `"N/A"` sitting in a number
column where it will silently turn that column into text.

### 6.1 What counts as missing

Any cell whose trimmed value matches one of `shape.null_markers`, compared
case-insensitively. The list is editable per dataset because a `side` column in
which `-` is a real category would otherwise be silently blanked — quiet data
loss, worth one control to prevent.

### 6.2 The four rules

- **Inference skips blanks.** A column's type is decided from its non-blank
  values only. A column blank throughout the sample cannot be typed, so it
  profiles as `text` and says so: *"'notes' is empty in your sample — typed as
  text."*
- **Validation skips blanks.** Only non-blank values are coerced, so a blank
  never fails a `number` column. A wholly empty column satisfies any type.
- **`allow_null = False` overrides both.** A blank in such a column is a refusal
  naming the count and the first line. Intended for the column a chart cannot
  survive gaps in — the date on a line chart's x-axis.
- **Blank rows are dropped and counted**, never kept as rows of nulls.

### 6.3 Type checking by coercion

The stored `ColumnSpec.type` is checked by **attempting to read the viewer's
column as that type**, not by inferring a type from their file and comparing
labels. Inference-then-compare gets the common cases wrong in both directions;
coercion gets them right for free:

| Expected | Given | Result |
| --- | --- | --- |
| `number` | integers | accepted — integers read as numbers |
| `number` | `"125,000"` | accepted — commas and whitespace are stripped |
| `number` | `"N/A"` | null, if it is a null marker; otherwise refused |
| `number` | `"hello"` | refused, naming the column, the count and the line |
| `integer` | `1.5` | refused — narrowing loses information silently |
| `text` | anything | accepted — a text column holds anything |
| `date` | an unparseable string | refused |

Coercion to `number` strips commas and surrounding whitespace. Currency symbols
and percent signs are out of scope for v1.

### 6.4 Inference order at design time

`date` → `integer` → `number` → `boolean` → `text`, taking the first that reads
every non-blank value. `text` always succeeds, so inference terminates.

The designer can correct any of it. A column of integer-looking order IDs is
`text`, and only a human knows that.

## 7. Building a file dashboard

1. Choose **File** as the dashboard's source. The editor hides environment,
   period and refresh interval.
2. Add a dataset and drop in a sample file.
3. The app reads the first 50 lines as a grid and guesses the header line: the
   first row that has no blank cells, no duplicate values, and fewer than half
   of its cells parsing as numbers. Data starts at the next non-blank line;
   types come from §6.4. If no row qualifies, row 0 is offered and the designer
   corrects it.
4. **The raw file is shown as a grid, exactly as it sits on disk**, with the
   guess drawn on it — header line highlighted, data region tinted — and every
   field overridable. Clicking a cell outside the table offers *name this cell*,
   which records its raw `(row, col)` as a `NamedCell`.
5. On confirm the profile is stored and **the sample is discarded from the
   database**. It stays in session state for the rest of the editing session
   (§7.1).
6. Widgets are built exactly as against a KDB dataset.

The guess is a convenience; what the designer confirms is the contract. A wrong
guess costs a click, not a failure.

### 7.1 The sample during editing

Storing no rows has one consequence worth designing for rather than discovering:
the transform preview and the widget column pickers have nothing to work
against.

The sample frame therefore **lives in session state for the duration of the
editing session** — it is already in memory from profiling — and is never
written to the database. While it is there, `trace_datasets` powers the same
step-by-step transform preview a KDB dataset gets, so a file dataset's pipeline
is built with real values in front of the designer rather than blind.

Reopening the dashboard later, with no sample in hand, still works: the widget
column pickers read `shape.columns`, which is the contract and is stored. Only
the *preview* of values is unavailable until a sample is dropped in again — and
dropping one in is exactly what the shape editor already does.

## 8. Running a file dashboard

Each file dataset renders its own upload box above the dashboard, labelled with
`file_label`. Dropping a file in calls `filesource.load` **immediately**, at the
upload box, before anything renders — so a refusal appears next to the thing the
viewer just dropped in, rather than later as a red panel some distance away, and
the file is not re-parsed on every refresh.

Accepted frames are held in session state per `(dashboard, dataset)`. A refusal
leaves the previous frame, if any, standing.

`run_datasets` gains `uploads: dict[str, pd.DataFrame] | None`, keyed by dataset
name. For a file dataset, `_fetch` returns the frame from `uploads` instead of
querying; everything after that — `apply_transforms`, `max_rows`, truncation —
is the existing shared tail. By the time a widget sees a `DatasetResult` it
cannot tell which source produced it.

**No upload yet is a state, not an error.** The panel reads *"waiting for your
orders export"*, consistent with the existing rule that a broken source degrades
one panel rather than blanking the page.

### 8.1 A file dashboard never comes due

`refresh()` re-runs a dashboard's datasets once `refresh_secs` has elapsed. A
file dashboard has nothing to re-run: the frame changes only when a different
file is uploaded. So `is_due` is skipped entirely for `source == "file"` and the
frames are stamped at upload; `refresh_secs` is ignored and hidden by the
editor.

This is not merely an optimisation. Re-stamping frames on an interval would
throw away the cached PDF pages on every tick — the exact bug the existing
comment in `refresh()` records having fixed for KDB dashboards.

### 8.2 Refusal messages

Each `Problem` names the column, what was expected, what arrived, and where:

- *"missing required column `filledQty` — the file has: sym, side, qty, avgPrice, venue"*
- *"column `qty` expects a number; 12 of 500 values could not be read as one (line 14: `N/A`)"*
- *"column `date` expects a value in every row; 4 rows are blank (first at line 27)"*
- *"two columns are called `qty` — rename one, or neither can be referenced"*

Notes are shown on acceptance: *"headers found on line 4, not 3"*, *"skipped 3
blank rows"*, *"ignored 2 columns not used by this dashboard"*.

## 9. Files changed

**New**

| File | Purpose |
| --- | --- |
| `core/filesource.py` | bytes → grid → frame: orientation, header location, nulls, coercion, named cells |
| `ui/fileshape.py` | the design-time grid editor |

`ui/fileshape.py` is separate because `ui/dashboard_editor.py` is already 1,472
lines; putting the profiler in it would push it past 1,800.

**Changed**

| File | Change |
| --- | --- |
| `core/dashboard_models.py` | three new dataclasses; four new fields; `dashboard_from_dict` lines |
| `core/dataset.py` | `run_datasets(..., uploads=None)` and `trace_datasets(..., uploads=None)`; one `source == "file"` branch in `_fetch`, which both already share |
| `ui/dashboards.py` | an upload panel per file dataset; thread `uploads` through `refresh`; skip `is_due` for file dashboards (§8.1) |
| `ui/dashboard_editor.py` | source selector; hide environment/period/refresh for file dashboards; hold the sample in session state (§7.1) |

### 9.1 One refactor, folded in

`dashboard_editor.validate()` is ~90 lines and nearly all of its per-dataset half
is KDB-specific: environments, periods, the historical-date guard, table
selection. A file dataset has none of those. Wrapping each check in
`if ds.source == "file"` would make an already-long function worse, so the
per-dataset half splits into `_kdb_dataset_problems(ds, envs, dashboard_time)`
and `_file_dataset_problems(ds)`, dispatched on `ds.source`.

This is a change to code the feature has to touch regardless — not general
tidying.

`_file_dataset_problems` checks: the dataset has a shape; the shape has at least
one column; no duplicate column names; every column a widget references is in
the shape and marked `required`; `data_start` is past `header_row`.

## 10. Mixing sources

A dashboard's `source` governs its datasets: a `file` dashboard holds only file
datasets, a `kdb` dashboard only KDB datasets. This is a deliberate restriction,
not a technical one — `run_datasets` could serve both in the same pass.

Mixing would mean the editor showing environment and period controls that apply
to some datasets and not others, which is the confusion the "one kind, two
faces" decision was taken to avoid. If joining an uploaded file against KDB
reference data is wanted later, it is additive: relax the restriction, and show
the KDB controls per dataset rather than per dashboard.

## 11. Testing

`filesource` is pure — raw CSV strings in, frames or refusals out — so it is
tested directly, without Streamlit, matching how the rest of `core/` is tested.

**Reading and shape**
- header on line 1 (the ordinary export)
- header on line 3, preamble above it
- header not where the spec says, found by the fallback search, and noted
- header not found within `search_rows` → refused
- vertical headers (`header_axis="column"`) transposed correctly
- `first_col > 0` — the table does not start at column A
- trailing-comma blank headers dropped
- duplicate headers refused
- blank rows dropped, and the count reported
- an empty file, and a file with headers but no data rows

**Nulls and types**
- every default null marker becomes a real null, case-insensitively
- a custom `null_markers` list — `-` kept as a category
- integer satisfies `number`
- `"125,000"` reads as `125000`
- text in a `number` column refused, naming the column, count and line
- `1.5` in an `integer` column refused
- `allow_null=False` refuses gaps, naming the count and first line
- a wholly empty column passes; and fails when `allow_null=False`
- inference skips blanks; an all-blank column profiles as `text`

**Contract**
- missing required column refused, listing what did arrive
- an absent optional column is fine
- extra columns ignored, and noted
- named cell resolved; a blank named cell becomes null
- `FileShape` survives `dashboard_to_dict` → `dashboard_from_dict`
- a dashboard saved without these fields reads back as `source="kdb"`

**Pipeline**
- a file dataset and a KDB dataset carrying the same frame produce
  indistinguishable `DatasetResult`s
- transforms and `max_rows` apply to a file frame identically
- no upload yet yields a waiting state, not a crash
- `trace_datasets` steps through a file dataset's transforms
- a file dashboard never comes due, whatever `refresh_secs` says (§8.1)
- a file dashboard exports a PDF

The last two in that group are the ones that prove the pipeline is genuinely
shared rather than parallel.

`ui/fileshape.py` and the upload panel are Streamlit and are not unit-tested,
matching how the rest of `ui/` is treated; everything they decide lives in
`filesource` and is tested there.

## 12. Out of scope for v1

Excel; delimiter sniffing; non-UTF-8 encodings; decimal comma; currency and
percent symbols; joining two files; suggested or generated widgets; storing
sample rows; scheduled runs.

All parsing sits behind `filesource.read_grid()`, so format tolerance is a later
change to one function.
