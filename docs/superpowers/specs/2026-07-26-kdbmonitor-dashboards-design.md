# KdbMonitor Dashboards — Design

**Date:** 2026-07-26
**Status:** approved (pending spec review)

## 1. Goal

Let a user build, save, and share **dashboards**: pages assembled from one or more
KDB queries, laid out as KPIs, tables and charts, refreshing on a timer while the
page is open, and exportable to a PDF that shows exactly the state on screen.

The motivating example is `short_sell_report.py` at the repo root — a standalone
script that queries the order RDB, shapes the result in pandas, and draws an A4
one-pager. That script should be reproducible entirely inside the app, without
writing Python, and kept live rather than run by hand.

Dashboards must work against both environments: the **real-time** servers and the
**historical** servers, where the tables are identical except that historical
tables carry a `date` column.

### Non-goals

- No background evaluation of dashboards. Queries run only while a dashboard is
  open, preserving the locked "no daemon" decision for this app.
- No scheduled or emailed PDFs, and no PDF archive in the DB. Download only.
- No drag-and-drop free grid. Layout is rows of widgets.
- No changes to alert evaluation, `engine.run_tick`, or the `Alert` model.

## 2. Concepts

```
Dashboard
 ├─ name, description, refresh_secs
 ├─ time_context                  (dashboard-level default)
 ├─ datasets: list[Dataset]       (the data)
 └─ rows: list[Row]               (the layout)
                └─ widgets: list[Widget]
```

A **Dataset** produces one DataFrame. A **Widget** renders one dataset. A **Row**
places 1–4 widgets side by side. Widgets never query; datasets never draw.

## 3. Environments (real-time vs historical)

### 3.1 Connection pairing

Real-time and historical live on different hosts/ports and expose the same table
names. `Connection` gains two fields:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `kind` | `"realtime" \| "historical"` | `"realtime"` | which environment this server is |
| `env` | `str` | `""` (falls back to `name`) | the logical group both servers share |

Connections sharing an `env` form a pair:

| Name | env | kind |
| --- | --- | --- |
| `order-rdb` | `orders` | realtime |
| `order-hdb` | `orders` | historical |
| `kdp-rdb` | `marketdata` | realtime |
| `kdp-hdb` | `marketdata` | historical |

Admin lists connections grouped by `env` and warns when an env has only one side,
so a dataset can never offer "historical" against a server that has no HDB.

Alerts are unaffected: a `Step` still names a single connection.

### 3.2 Time context

A dashboard stores a **spec**, not resolved dates, so a saved "last 30 days"
dashboard always means the last 30 days:

```python
{"mode": "realtime"}
{"mode": "historical", "range": {"kind": "relative", "n": 30, "unit": "days"}}
{"mode": "historical", "range": {"kind": "absolute", "from": "2026-06-01", "to": "2026-06-30"}}
{"mode": "historical", "range": {"kind": "preset", "name": "last_month"}}
```

Presets: `today`, `yesterday`, `last_7d`, `last_30d`, `mtd`, `last_month`,
`ytd`. Resolution to concrete dates happens once per refresh; the resolved range
is stamped on screen and on the PDF.

The control is a **header bar on the dashboard**, not a placed widget: it governs
every dataset, so it is not a layout element, and a date picker cannot be printed.
In the PDF it renders as the subtitle band — `Historical · 2026-06-01 → 2026-06-30`
or `Real-time · as of 2026-07-26 09:15` — occupying the slot that
`short_sell_report.py:217` already uses for `as_of`.

### 3.3 Per-dataset override

Each dataset carries `time_mode`:

- `inherit` (default) — use the dashboard's time context
- `realtime` — always hit the RDB, whatever the dashboard is set to
- `custom` — its own time-context spec

This is what allows "last 30 days by market" and "today's live fills" on one page.

### 3.4 Date injection

The date constraint is **never stored in the dataset's filters**. It is injected
at run time from the resolved time context, which makes flipping a dataset between
environments lossless in both directions.

- **Guided datasets:** the resolved clause is prepended as the *first* where-clause:
  `select from target where date within (2026.06.01;2026.06.30), side=\`sellshort`.
  First position matters — that is what lets kdb+ prune partitions instead of
  scanning the whole HDB.
- **Raw-q datasets:** `{{date_from}}`, `{{date_to}}` and `{{date_list}}`
  placeholders, substituted by extending the existing `_REF` mechanism in
  `core/chain.py`. `short_sell_report.py`'s `ORDER_FN` becomes historical with one
  edit to its where-clause.

The guided column picker reads the *resolved* connection's introspected schema, so
`date` appears as a filterable column in historical mode and is absent in real-time
mode. No special-casing.

### 3.5 Safety rail

**In historical mode a dataset must constrain `date`.** Guided mode does it
automatically; raw mode is validated at save time and refuses to run without a
date reference. An unconstrained `select from target` against a partitioned HDB
does not error — it reads years of data and hangs the app on a refresh timer.

## 4. Datasets

```python
@dataclass
class Dataset:
    name: str                      # referenced by widgets and by {{name.column}}
    env: str                       # logical environment, not a connection name
    time_mode: str                 # inherit | realtime | custom
    time_context: dict | None      # only when time_mode == "custom"
    mode: str                      # guided | raw
    table: str                     # guided only
    filters: list[Filter]          # guided only, reuses core.models.Filter
    raw_qsql: str | None           # raw only
    transforms: list[Transform]    # applied to the returned frame, in order
    max_rows: int                  # hard cap, default from settings
```

`mode: guided | raw` mirrors `Step.mode: form | raw`, so the editor reuses the
Builder's interaction vocabulary.

Datasets may reference each other with `{{other_ds.column}}`, reusing
`substitute_refs` from `core/chain.py`. Datasets are executed in declaration
order; a reference to a later dataset is a validation error.

### 4.1 Transforms

Guided, ordered, applied in pandas after the query returns:

| Transform | Parameters |
| --- | --- |
| `derive` | new column name; expression kind — `arithmetic` (`100*executed/size`), `suffix_map` (`sym` → market via a suffix table), `bucket`, `concat` |
| `filter` | column, op, value (post-query filtering, e.g. on a derived column) |
| `groupby` | keys; aggregations `count / nunique / sum / mean / min / max` per output column |
| `sort` | columns, ascending/descending |
| `limit` | n |
| `rename` | old → new |

`derive` with `suffix_map` plus `groupby` reproduces `short_sell_report.py`'s
`market_of()` / `summarise_by_market()` without Python.

## 5. Rendering

### 5.1 The split

Interactivity on screen and a faithful PDF are served by two backends over one
shared resolution step:

```
(widget spec, DataFrame)
        │
        ▼
core/plotmodel.py          ← all logic, resolved once
   PlotModel: series, labels, values, colors,
              number formats, thresholds, titles, axis labels
        │
        ├──────────────────────┬──────────────────
        ▼                      ▼
core/render_mpl.py       core/render_plotly.py
   PDF (A4 axes)            screen (interactive)
```

Every decision that could disagree between the two — row selection, aggregation,
sort order, colour assignment, decimal places, threshold colouring — is made once
in `plotmodel.py` and unit-tested once. The backends only draw. Drift is limited
to visual styling; never to numbers.

`core/theme.py` holds the palette lifted out of `short_sell_report.py`
(`SURFACE`, `INK`, `INK2`, `MUTED`, `GRID`, `BASELINE`, `BLUE`, `CRITICAL`,
`GOOD`) plus the seaborn theme config, and feeds both backends, so a given market
is the same blue on screen and in print.

### 5.2 Screen

- charts → `st.plotly_chart`, with `hovermode="x unified"` on line charts so
  hovering shows every series' value at that x; zoom, pan and legend toggling come
  for free
- tables → `st.dataframe` (sortable, scrollable, selectable text)
- KPIs → `st.metric`
- text → `st.markdown`

Plotly uses a dark template, so dashboards match the app's dark theme.

### 5.3 PDF

`core/dashpdf.py` assembles rows onto A4 portrait pages via `PdfPages`, using the
light paper surface from `short_sell_report.py`. Each widget is drawn by
`render_mpl` onto axes placed with `fig.add_axes([...])`, exactly as
`short_sell_report.py:235,292,295` does today. Seaborn draws `heatmap`, `boxplot`,
`histplot`/`kdeplot`. Rows that overflow a page continue on the next, with the
title band and footer repeated.

**The PDF renders from the frames already on screen** — no re-query. The
downloaded page shows the numbers the user is looking at, not a fresh fetch taken
a moment later.

The dashboard view also offers **Preview PDF**, rendering page 1 to a PNG inline
so the printed output can be checked before downloading.

Filename: `<dashboard_slug>_<YYYY-MM-DD_HHMM>.pdf`, delivered by
`st.download_button` — the same shape as the Excel download in `ui/reports.py`.

## 6. Widgets

```python
@dataclass
class Widget:
    type: str        # kpi | table | bar | line | scatter | hist | box | heatmap | pie | text
    dataset: str
    title: str
    spec: dict       # type-specific, validated by a per-type schema
    width: float     # relative weight within the row, default 1.0

@dataclass
class Row:
    widgets: list[Widget]
    height_in: float          # printed height; screen height derives from it
```

| Type | `spec` fields |
| --- | --- |
| `kpi` | `column`, `agg` (count/nunique/sum/mean/min/max), `fmt`, `suffix`, `thresholds` (value → colour) |
| `table` | `columns`, per-column `fmt`, `sort`, `max_rows`, `highlight` rules (e.g. rejections > 0 → red) |
| `bar` | `x`, `y`, `orientation`, `hue`, `sort`, `value_labels` |
| `line` | `x`, `y` (one or many), `hue`, `markers` |
| `scatter` | `x`, `y`, `hue`, `size`, `regression` |
| `hist` | `x`, `bins`, `kde` |
| `box` | `x`, `y`, `hue` |
| `heatmap` | `rows`, `cols`, `value`, `agg`, `cmap`, `annotate` |
| `pie` | `value`, `by`, `donut` |
| `text` | `markdown`, supporting `{{dataset.agg.column}}` placeholders |

Adding a widget type means one `plotmodel` resolver, one `render_mpl` function,
one `render_plotly` function, and one spec form. Nothing else changes.

## 7. UI

A fifth `st.navigation` entry, **Dashboards**, between Reports and Admin, with
three modes.

### 7.1 Gallery (default)

One card per dashboard: name, description, environments used, widget count, last
refreshed. Actions: `Open`, `Edit`, `Duplicate`, `Delete` (confirmed).
`Duplicate` follows the `_load_clone` pattern in `ui/builder.py` — load as a new
draft with `id=None` and a `(copy)` name. `New dashboard` starts empty.

### 7.2 View

```
 ● Short-sell  ○ Fill quality  ○ Monthly review     [ ⟳ 15s ]  [ Edit ]  [ PDF ]
────────────────────────────────────────────────────────────────────────────────
 Real-time · as of 09:15                                     [ Real-time ▾ ]
 ┌──────────┬──────────┬──────────┐
 │ 128      │ 61.4%    │ 3        │
 │ Orders   │ Complete │ Rejects  │
 └──────────┴──────────┴──────────┘
 ┌───────────────────────────────────────┐
 │ by-market table                       │
 └───────────────────────────────────────┘
 ┌──────────────────┬────────────────────┐
 │ completion bar   │ rejections bar     │
 └──────────────────┴────────────────────┘
```

The tab strip is **`st.pills`**, not `st.tabs`. `st.tabs` executes every tab's
body on every rerun, which under a 15-second refresh would fire every dashboard's
queries at KDB continuously. Pills keep exactly one dashboard live.

The active dashboard is written to the URL as `?dash=<id>`, so a dashboard is
bookmarkable and survives a browser refresh.

### 7.3 Edit

Two sub-sections:

- **Data** — the dataset list. Per dataset: name, env, time mode, guided/raw
  toggle, table, filters, transform steps, plus a live preview of the resulting
  rows and the generated q.
- **Layout** — the row list. Each row is an expander with `↑ ↓ ✕` and
  `+ widget`; each widget is a card with type, dataset, column bindings and
  title. A live render of the real page sits alongside.

Both are session-state-driven, following `ui/builder.py`.

## 8. Refresh

Each dashboard stores `refresh_secs` (off / 5s / 10s / 15s / 30s / 1m / 5m / 15m,
reusing `INTERVAL_PRESETS` from `ui/common.py`). The open dashboard runs inside
`st.fragment(run_every=refresh_secs)`. Navigating away or switching pills destroys
the fragment, so polling stops.

Resolved frames live in `st.session_state` keyed by dashboard id, with their
`as_of` timestamp, and are what both the screen and the PDF read.

`engine.run_tick` and the alert loop are not touched.

## 9. Storage

New table, plus two additive column migrations in the existing `_migrate()` hook
(`core/storage.py`), which keeps every current row valid:

```sql
CREATE TABLE IF NOT EXISTS dashboards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    dashboard_json TEXT NOT NULL
);

ALTER TABLE connections ADD COLUMN kind TEXT NOT NULL DEFAULT 'realtime';
ALTER TABLE connections ADD COLUMN env  TEXT NOT NULL DEFAULT '';
```

CRUD mirrors the alert CRUD: `add_dashboard`, `get_dashboard`, `list_dashboards`,
`update_dashboard`, `delete_dashboard`, with the whole definition serialised as
JSON exactly as `alert_json` is today.

Export/import extends `core/portability.py` with a dashboards bundle, alongside
the existing alerts and connections exports.

## 10. Modules

All core modules are Streamlit-free and testable against `FakeClient`, per the
existing convention.

| Module | Responsibility |
| --- | --- |
| `core/dashboard_models.py` | `Dashboard`, `Dataset`, `Transform`, `Row`, `Widget`, `TimeContext` + JSON |
| `core/timectx.py` | resolve spec → dates; inject `date within`; substitute date placeholders |
| `core/transform.py` | the transform catalogue |
| `core/dataset.py` | run one dataset: env+mode → connection, build q, query, transform |
| `core/theme.py` | shared palette and seaborn theme |
| `core/plotmodel.py` | (widget spec, DataFrame) → `PlotModel` |
| `core/render_mpl.py` | `PlotModel` → matplotlib/seaborn axes |
| `core/render_plotly.py` | `PlotModel` → plotly figure |
| `core/dashpdf.py` | rows → A4 pages → PDF bytes |
| `core/storage.py` | dashboards table + CRUD (extended) |
| `ui/dashboards.py` | gallery, tab strip, header bar, refresh fragment |
| `ui/dashboard_editor.py` | Data and Layout editors |

`ConnectionManager.get` is extended to pass the `Connection` to the mock factory,
so demo real-time and demo historical can be served by different mocks.

## 11. Error handling

- **Dataset failure** (server down, bad q, bad reference): the dataset resolves to
  an error instead of a frame. Widgets bound to it render an error card naming the
  dataset and the message. **The error card is drawn in the PDF too** — a printed
  report must never omit a broken panel silently, because a missing chart reads as
  "nothing to report".
- **Partial failure:** other datasets and widgets still render. One dead server
  degrades one panel, not the page.
- **Save-time validation:** historical dataset must constrain `date`; env must
  have a connection of the required kind; widget must name an existing dataset and
  existing columns; dataset references must point backwards; row widths positive;
  1–4 widgets per row.
- **Run-time guards:** every dataset carries `max_rows`; the historical date
  constraint is enforced before the query is sent.
- **Refresh overrun:** a refresh slower than the interval simply runs long —
  fragments do not overlap. The header shows the `as_of` stamp and a refreshing
  state, so a stale page is visibly stale.
- **Known limitation:** `PyKxClient` has no query timeout. A hung historical query
  blocks that fragment. The date constraint is the primary mitigation; an explicit
  timeout is noted as future work.

## 12. Testing

Following the existing pattern — core logic against `FakeClient`, plus a demo path
through `MockKdbClient` so the whole feature is exercisable with no real KDB.

- `timectx`: preset/relative/absolute resolution; clause built in first position;
  placeholder substitution; refusal when historical without a date reference
- `transform`: each transform type, and the `derive`+`groupby` combination that
  reproduces `summarise_by_market`
- `dataset`: env+mode → connection resolution; guided q generation with and
  without date injection; raw q substitution; `max_rows` capping; error capture
- `plotmodel`: aggregation, sorting, colour assignment, formatting, thresholds —
  the numbers, asserted once for both backends
- `render_mpl`: smoke tests per widget type (produces axes); `dashpdf` output
  starts with `%PDF`
- `render_plotly`: smoke tests per widget type (expected trace types)
- `storage`: dashboard CRUD; connection `kind`/`env` migration against a DB
  created without those columns
- `ui`: import/smoke test following `tests/test_ui_smoke.py`

`core/mock.py` gains a historical demo server (`orders_hdb_demo`) whose tables
carry a `date` column and which honours `date within`, plus `env`/`kind` on the
demo connection specs, so the environment switch is demoable end to end.

## 13. Dependencies

Added to `requirements.txt`: `matplotlib`, `seaborn`, `plotly`.

## 14. Future work (explicitly out of scope)

- Scheduled or emailed PDFs; a PDF history in the DB
- Background refresh of dashboards not currently open
- Query timeouts in `PyKxClient`
- Drag-and-drop grid layout
- Cross-dashboard shared datasets
