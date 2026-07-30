# Dashboard Parameters and Chart Enrichment — Design

**Date:** 2026-07-31
**Status:** designed, not built.

## 1. Goal

Make a KdbMonitor dashboard able to express the **Volume Profile viewer**
(`C:\Users\user\Desktop\Work\Projects\Work\VolumeProfile`): an intraday volume
profile read from a CSV, charted as a cumulated curve and a per-bucket bar
series, each against a dashed reference, with the instrument chosen by whoever
is looking at it.

That viewer is the target because it is a real tool the desk already uses, and
because everything it does that KdbMonitor cannot is on one seam: **it is driven
by the person reading it, and a dashboard today is not.**

Its file already loads. Against the file-dashboard feature shipped 2026-07-30,
`sample_india_volume_profile.csv` reads first try — 1,560 rows, 20 instruments,
`Time` typed as a date, `CumulatedPercentage` as a number, the `#TimeZone=` line
picked up as a named cell. Nothing in this document is about ingestion.

### Non-goals

- **No synced crosshairs between charts, and no keyboard bucket-stepping.**
  Streamlit would round-trip to the server on every hover. It is the most work
  and the least value here, and it is the one thing the HTML viewer is genuinely
  better at. The viewer keeps that job.
- No new widget types. The charts already exist; they are being given references
  and bands, not replaced.
- No change to alert evaluation or `engine.run_tick`.
- Parameters never reach a **query**. See §3.6.

## 2. The six features, and why in this order

| # | Feature | Buys | Phase |
| --- | --- | --- | --- |
| 1 | Window transforms, partitioned | the per-bucket share, *correctly* | A |
| 2 | Hardened `derive` | closes a stated-but-untrue safety guarantee | A |
| 3 | Viewer parameters | the instrument picker, the scale window, the local/source toggle | B |
| 4 | Chart references and bands | *even pace*, *average bucket*, the pre-open band | C |
| 5 | Timezone transform | the whole timezone section of the viewer | D |
| 6 | Tolerant parsing, and data export | files re-saved through Excel; *Export selection* | E, F |

Phase A comes first because it is a **correctness fix**, not a feature. The
per-bucket share is the difference between consecutive rows of
`CumulatedPercentage`. Today the only way to write that is
`CumulatedPercentage.diff()` in a `derive`, and on this file that is silently
wrong: the frame stacks 20 instruments, so the difference crosses from one
instrument into the next. Measured on the real sample it yields **-1.0 at every
instrument boundary**, and each instrument's shares sum to **0.0** instead of
1.0. A dashboard would print that without complaint.

## 3. Viewer parameters

### 3.1 What a parameter is

A value the **reader** chooses, which the dashboard's transforms and widgets are
written against. The dashboard stores the definition and a default; the choice
itself belongs to whoever is looking.

```python
@dataclass
class Parameter:
    name: str                    # referenced as {{param:name}}
    label: str = ""              # shown on the control; falls back to name
    kind: str = "choice"         # choice | column | number | date | toggle
    choices: list[str] = field(default_factory=list)   # choice only
    dataset: str = ""            # column only: whose values to offer
    column: str = ""             # column only: which column
    default: str = ""
```

`Dashboard` gains `parameters: list[Parameter]`.

### 3.2 How a parameter is used

By substitution, which is how this codebase already resolves everything else —
`{{stepN.column}}` in `chain.substitute_refs`, `{{date_from}}` in
`timectx.substitute_dates`, `{{conn:ENV}}` in `dataset.substitute_connections`.
A fourth idiom would be a fourth thing to learn.

`{{param:name}}` is replaced in:

- every **string** value inside a transform's `params`, at any depth
- every **string** value inside a widget's `spec`, at any depth

So the instrument picker is an ordinary filter:

```json
{"kind": "filter", "params": {"column": "#FidessaCode", "op": "=",
                              "value": "{{param:instrument}}"}}
```

and a parameter can equally choose *which column* a chart plots:

```json
{"type": "line", "spec": {"x": "Time", "y": "{{param:measure}}"}}
```

Substitution is applied to a **deep copy**; the stored dashboard is never
rewritten. A `{{param:...}}` naming a parameter that does not exist is left
untouched rather than blanked, so the failure surfaces as "no column
`{{param:typo}}`" — which names the typo — rather than as an empty filter that
quietly matches everything.

### 3.3 Types, and why substitution is textual

Every value substitutes as text. A `filter` transform already coerces its value
against the column's dtype (`_coerce` in the editor, and pandas comparison at
run time), so a numeric parameter reaching a numeric column works without
parameters needing a type system of their own.

`toggle` substitutes as `"true"` / `"false"`. `date` substitutes ISO
(`2026-07-31`). `number` substitutes its literal text.

### 3.4 Where a `column` parameter's choices come from

From the **raw frame of the named dataset, before its transforms run**. That is
the only frame where every instrument is still present — after the filter the
parameter itself drives, exactly one is.

This creates an ordering requirement: **the dataset a parameter reads its
choices from must be declared before any dataset whose transforms use that
parameter.** This is not a new rule; `{{name.column}}` dataset references
already work this way, and validation already enforces declaration order for
them. It is enforced the same way here.

Where the source dataset has not run — or failed — the choices are empty and the
parameter falls back to its default. The control says so rather than rendering
an empty dropdown.

### 3.5 Where the chosen value lives

In session state, per `(dashboard, parameter)`. Not on the `Dashboard`: the
definition and the default are the dashboard's, but the choice is the reader's,
and two people looking at the same dashboard are entitled to different ones.

Not in the URL either, for now. Bookmarkable filtered reports are worth having
and the codebase already reads `?dash=` — but it is not needed for the goal, and
every parameter in the query string is a decision about escaping and about what
happens when a bookmarked value is no longer a valid choice. Out of v1, noted
here so the omission is deliberate.

### 3.6 Parameters never reach a query

A parameter feeds **transforms and widget specs only** — never `raw_qsql`, never
a guided filter, never a `FileShape`. Therefore changing one **never re-queries
KDB and never re-reads an uploaded file**: the frames already in session state
are re-transformed and redrawn.

This is a real limitation and it is chosen. Letting a parameter into the query
would mean every control change hits the server, and on a historical dashboard
that is a partitioned read. It would also make a parameter change indistinct
from a refresh, which the dashboards work of 2026-07-26 went to some trouble to
separate. For the Volume Profile target it costs nothing: the file is 1,560 rows
and the filtering is local either way.

### 3.7 Re-running on a change

Changing a parameter drops the **derived** half of the cached frames and keeps
the fetched half. Concretely: `run_datasets` splits into fetch (unchanged) and
apply (re-run). The PDF page cache is dropped, because the pages are of a report
that has changed.

### 3.8 The controls

Rendered in one row above the dashboard's rows, before the upload panel for a
file dashboard. `choice` and `column` are selectboxes; `number` is a number
input; `date` a date input; `toggle` a checkbox.

The editor shows the same controls, so the author sees the dashboard as its
reader will, and the layout preview and step preview both run against the
current values.

### 3.9 Parameters on the printed page

**The values print.** A report filtered to one instrument that does not say
which is misleading, and a PDF outlives the screen it was taken from. They go
under the period line in the title band, as `instrument: ICICIBC.IN · window:
full session`, in the same muted type.

Only page 1 carries them, matching the existing rule for the title band.

### 3.10 Validation

`validate` gains, for every dashboard:

- a parameter with no name
- two parameters with one name
- a `choice` parameter with no choices
- a `column` parameter naming a dataset or column that does not exist
- a `column` parameter whose source dataset is declared *after* a dataset that
  uses the parameter (§3.4)
- a `{{param:x}}` appearing in any transform or widget where no parameter `x` is
  defined
- a default that is not among a `choice` parameter's choices

## 4. Window transforms

### 4.1 The transform

```python
{"kind": "window", "params": {
    "column": "CumulatedPercentage",
    "op": "diff",                       # diff | cumsum | shift | rolling_mean
                                        # | rolling_sum | row_number
    "periods": 1,                       # diff, shift, rolling_*
    "partition_by": ["#FidessaCode"],   # optional; [] = the whole frame
    "as": "bucket",
}}
```

Implemented with `df.groupby(partition_by)[column].transform(...)`, or the plain
series where `partition_by` is empty. `row_number` takes no source column and
numbers rows from 0 within each partition — which is what makes an *even pace*
reference computable (§5.2).

### 4.2 Rules

- An empty frame yields the new column, empty and correctly typed — matching
  `_groupby`, which already returns an empty frame with the right columns rather
  than raising.
- A missing `column` or `partition_by` entry raises the same
  `no column 'x' (have: ...)` message every other transform raises, from the
  shared `_need` helper.
- An unknown `op` raises naming what it was, like `_derive`'s unknown-kind path.
- `partition_by` **does not reorder the frame.** `groupby(...).transform(...)`
  preserves position, which matters because these files are in session order and
  a volume profile resorted around midnight is wrong.

### 4.3 Why this is not optional

Both because of §2's `-1.0`, and because it is the precondition for §4.4: it
supplies, deliberately and per-partition, the one capability that hardening
`derive` takes away by accident.

## 5. Hardening `derive`

### 5.1 The hole

`transform.py` opens by stating why its catalogue is closed:

> Deliberately a small closed catalogue rather than arbitrary code: dashboards
> are stored in the DB and shared between users, so a transform must be data,
> not a Python snippet.

and `_derive` says of its arithmetic expressions:

> pandas' own expression engine: column names only, no attribute access.

That second sentence is false. Measured:

| expression | result |
| --- | --- |
| `cum.diff()` | evaluates |
| `cum.to_numpy().sum()` | evaluates |
| `cum.__class__` | evaluates |
| `cum.__class__.__mro__` | reaches Python's class hierarchy |
| `@__import__("os").getcwd()` | blocked |

Method calls chain, and dunder traversal reaches class internals. `__import__`
is blocked as a bare name, but that is one door of several. `portability.
import_dashboards_json` takes bundles from other people, and the file-dashboard
work has made dashboards markedly more shareable — a template is *meant* to be
handed round.

### 5.2 The fix

Parse the expression to an AST before evaluating, and accept only:

- `Name` (column names), `Constant` (numbers, strings)
- `BinOp` with `+ - * / // % **`
- `UnaryOp` with `+ -`
- `Compare` with `== != < <= > >=`
- `BoolOp` with `and or`, and `Not`

Reject everything else — `Attribute`, `Call`, `Subscript`, `Lambda`,
comprehensions, walrus — with a message naming what was rejected and pointing at
the `window` transform where the rejected thing was a window function:

> `derive` takes arithmetic over columns, not method calls. For `.diff()`,
> `.shift()` or a running total, use a **window** transform, which can also
> partition by another column.

This makes the docstring true, and it is only affordable because §4 exists.

### 5.3 Migration

An existing dashboard carrying `cum.diff()` will start failing validation and
showing an error panel. That is the correct outcome — on any multi-instrument
frame it was already producing wrong numbers — but it must fail *loudly*, with
the message above, not silently.

## 6. Chart references and bands

### 6.1 References

A dashed line across a chart, at a constant or following a column.

```python
spec["references"] = [
    {"kind": "constant", "value": 0.05, "label": "average bucket"},
    {"kind": "column", "column": "even_pace", "label": "even pace"},
]
```

`PlotModel` gains `references: list[Reference]`, resolved in `plotmodel.py` as
every other numeric decision is, and drawn by both renderers — Plotly on screen,
matplotlib on the page — from the same resolved values.

*Even pace* is then an ordinary column: `row_number` over the session (§4.1),
divided by the bucket count. *Average bucket* is a constant the author sets, or a
column derived from the mean.

### 6.2 Bands

A shaded span behind the plot, for the pre-open stretch.

```python
spec["bands"] = [{"from": "09:00:00", "to": "09:15:00", "label": "pre-open"}]
```

`from`/`to` are matched against the x values as text, so this works for times,
dates and categories alike without a second type system. A band whose endpoints
are not found is dropped and noted rather than drawn at the origin.

### 6.3 Applies to

`line`, `bar`, `scatter`. Not `pie`, `heatmap`, `hist`, `box`, `kpi`, `table` —
a reference line across a pie means nothing.

## 7. Timezone transform

```python
{"kind": "timezone", "params": {
    "column": "Time",
    "from_column": "TimeZone",     # or "from_zone": "India Standard Time"
    "to": "local",                 # or an IANA id
    "as": "LocalTime",
    "day_offset_as": "DayShift",   # optional: -1 / 0 / +1
}}
```

The file names Windows zones (`India Standard Time`), not IANA ids, so
`core/zones.py` carries the CLDR windowsZones mapping (~140 names) plus bare
abbreviations (`IST`, `CET`, `JST`) and literal offsets (`UTC+05:30`). IANA ids
pass through. Resolution uses `zoneinfo`, so daylight saving is **computed at
the timestamp**, not assumed.

`day_offset_as` records where a converted bucket lands on another calendar day,
which is what lets a table show `23:45 -1d` without resorting the session.

## 8. Tolerant parsing

Deferred deliberately on 2026-07-30 — strictly comma-separated UTF-8 — with the
note that a semicolon export from a European locale would be the first real
stumble. It is now in scope, and all of it lives behind `filesource.read_grid`
and the `_to_*` readers:

- **Delimiter**: `FileShape.delimiter: str = "auto"`, sniffed among `, ; tab |`
  by counting consistent occurrences across the first lines. An explicit setting
  overrides.
- **Encoding**: UTF-8, then cp1252, then latin-1 — reported in `notes` when it
  is not UTF-8, because a mojibake column name is worth knowing about.
- **Decimal comma**: `0,0215` reads as a number where the delimiter is not a
  comma. Where it *is* a comma the value is quoted, and the existing
  comma-stripping already handles it — so this is not ambiguous, but the rule
  must be written down.
- **Excel time serials**: a bare fraction in a `date` column (`0.385416`) is
  a time of day. Only applied where the declared type is `date`.

Not in scope: automatic 0..1 versus 0..100 detection. That is a domain judgement
about one column's meaning, and an author who knows which it is can write a
`derive`. Guessing it would be the kind of quiet decision §5 of the file-dashboard
spec argues against.

## 9. Data export from a dashboard

A download control per dataset on the run page, offering CSV and Excel of the
frame **as the widgets see it** — after transforms, after parameters. Reuses
`core/exporting.py`, which already serves the alert Result page.

The filename carries the dashboard, the dataset and the parameter values, so two
exports taken at different instrument selections do not overwrite each other.

## 10. Files

**New**

| File | Responsibility |
| --- | --- |
| `core/parameters.py` | `Parameter` resolution and `{{param:...}}` substitution; Streamlit-free |
| `core/zones.py` | Windows/abbreviation/offset → IANA, and the conversion |
| `ui/parameters.py` | the controls, and the session state behind them |

**Changed**

| File | Change |
| --- | --- |
| `core/dashboard_models.py` | `Parameter`; `Dashboard.parameters`; deserialisation |
| `core/transform.py` | `window`; `timezone`; AST guard on `derive` |
| `core/dataset.py` | split fetch from apply; parameters into `run_datasets` |
| `core/plotmodel.py` | `references`, `bands` resolved into `PlotModel` |
| `core/render_plotly.py`, `core/render_mpl.py` | draw them |
| `core/dashpdf.py` | parameter values in the title band |
| `core/filesource.py` | delimiter, encoding, decimal comma, Excel serials |
| `ui/dashboards.py` | parameter row; export controls; cache invalidation |
| `ui/dashboard_editor.py` | parameter editor; window/timezone forms; reference and band forms; validation |

## 11. Testing

Everything above is `core/` and therefore directly testable. The cases that
matter:

**Window** — diff partitioned by instrument gives no `-1.0` at a boundary and
each partition sums to 1.0; unpartitioned over one instrument matches; empty
frame; missing column; missing partition column; row order preserved; unknown
op.

**Derive hardening** — `cum.diff()` rejected with the message pointing at
`window`; `cum.__class__.__mro__` rejected; `a + b * 2` still accepted;
`(a > 1) and (b < 2)` still accepted; an existing dashboard's arithmetic still
evaluates.

**Parameters** — substitution at depth in transform params and widget specs; a
missing parameter left intact; the stored dashboard unmutated; `column` choices
read from the raw frame not the transformed one; declaration-order rule enforced;
default used when the source dataset failed; every validation rule in §3.10.

**References and bands** — resolved into `PlotModel`; drawn by both renderers; a
band whose endpoints are absent dropped and noted; references ignored on widget
types that do not take them.

**Timezone** — a Windows name resolves; an abbreviation resolves; a literal
offset resolves; an unknown zone errors naming it; DST computed either side of a
transition; `day_offset_as` marks a crossing; order preserved.

**Parsing** — a semicolon file; a tab file; a cp1252 file, noted; a decimal
comma; an Excel time serial in a `date` column; an explicit delimiter overriding
the sniff.

**End to end** — the real `sample_india_volume_profile.csv`, filtered by an
instrument parameter, per-bucket share partitioned, charted with both references,
printed to a PDF whose header names the instrument.

## 12. Phasing

Each phase leaves the app working and is worth having on its own.

| Phase | Contents | Why here |
| --- | --- | --- |
| **A** | window transform, `derive` hardening | correctness first; unblocks the honest fix of the eval hole |
| **B** | parameters, end to end including the PDF caption | the keystone; three viewer features at once |
| **C** | references and bands | the charts read wrong without them |
| **D** | timezone | self-contained |
| **E** | tolerant parsing | self-contained; the deferred item from 2026-07-30 |
| **F** | data export | smallest; reuses existing helpers |

After **C** the Volume Profile dashboard is recognisable: pick an instrument,
cumulated curve against even pace, per-bucket shares against the average, table
below, printed to PDF. **D**–**F** close the remaining distance, except the
crosshair, which stays out by §1.
