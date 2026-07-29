# KdbMonitor

A monitoring tool for KDB+/q databases. Build **chains of KDB queries** through a guided UI and get **notified** when a chain's final result meets a condition you define. Aimed at trading / algo teams who need to watch orders and market state without hand-running queries all day.

Built with [Streamlit](https://streamlit.io/) and [PyKX](https://code.kx.com/pykx/).

---

## What it does

- **Connect** to one or more KDB servers (just host + port).
- **Build alerts** as a chain of query steps. Each step runs a query; a later step can reuse an earlier step's result (see [The alert builder](#the-alert-builder)). The final step's result is checked against a **trigger condition**.
- **Clone** an existing alert as a starting point when you need a near-duplicate with a small query change.
- **Monitor** alerts live. Once monitoring is on it **keeps running on every tab and auto-resumes after a restart** (the on/off state is saved), so alerts keep arming and triggering all day without babysitting the Monitor tab. Each alert runs on its own interval. When one triggers you get an in-app banner, an optional sound, a **browser notification that shows even when the window is minimized**, and optionally an email or a Teams/Slack message.
- **Track the day** with durable per-day counters (triggered / armed / errors / notifications) that are derived from the persisted run log, so **they don't reset when you restart the app**.
- **Never miss a trigger**: an alert that has fired keeps a red **NEW** badge until you open it with **View**.
- **Investigate** results: preview an alert's output while building it, and open a full **Result page** for a triggered alert to view, export (Excel/CSV) or copy the data.
- **Share** whole setups with teammates via JSON export/import — export **alerts** (bundled with their connections) or **connections on their own**.
- **Try it with no KDB at all** using the built-in **demo mock**.

> Note on scope: checks run **only while the app is open** in a browser and monitoring is toggled on — but they run on **whichever tab you're on**, not only the Monitor page, and monitoring resumes automatically when you reopen the app. There is no always-on background daemon (by design, for now). The core logic is written to make adding one later straightforward.

---

## Setup

### Requirements

- Python 3.11+
- A KDB server to point at (or use the demo mock, which needs nothing)

### Install

```bash
# from the project root
python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
```

Dependencies: `streamlit`, `pykx`, `pandas`, `requests`, `openpyxl`. (`pykx` is only needed to talk to a real KDB server; the demo mock and the whole UI work without a live connection.)

### Run

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`. State (connections, alerts, settings) is stored in a local `kdbmonitor.db` SQLite file in the project directory.

### Fastest way to see it working

1. Go to **Admin** and click **Load demo servers**. This adds two in-memory mock servers, `kdp_demo` (market data, table `QATT`) and `orders_demo` (order tables `target`, `work_order`, `target_state`), pre-filled with synthetic, time-varying data.
2. Go to **Builder** and create an alert (walkthrough below), or import one.
3. Go to **Monitor**, click **Enable alert notifications**, toggle **Monitoring** on.

---

## The four views (plus a Result page)

| View | Purpose |
|------|---------|
| **Monitor** | The live dashboard. Turn monitoring on/off, set the check granularity, watch statuses, get notifications, open results. |
| **Builder** | Create, edit, **clone**, delete, enable/disable alerts. Import/export alerts. Preview an alert's result before saving. |
| **Dashboards** | Build and watch saved dashboards: KPIs, tables and interactive charts over one or more KDB queries, refreshing on their own interval, exportable as a PDF. |
| **Admin** | Register KDB connections (host + port + environment), introspect their tables/columns, load the demo servers, pair real-time/historical environments, import/export connections, and set SMTP for email alerts. |
| **Result** | Opened from a **View** button in the Monitor. A full-width page to inspect / export / copy a triggered alert's rows. |

---

## The alert builder

This is the heart of the app. An **alert** is:

```
one or more STEPS  →  a TRIGGER condition  →  NOTIFY channels  →  timing & retention
```

Open **Builder → New alert** and fill it in top to bottom.

### 1. Name and check interval

- **Alert name** — free text, shown everywhere. Names must be unique when importing (see [Sharing](#sharing-alerts-and-connections)).
- **Check interval (seconds)** — how often *this* alert runs while monitoring is live (5–3600s; presets 5s / 15s / 30s / 1m / 5m / 15m). Each alert keeps its own cadence.

### 2. Steps (the query chain)

Every step produces a table (a result). Steps run in order. Each step has:

- **Server** — which registered connection to query. Different steps can hit **different servers**.
- **Table** — a table on that server (populated from the server's introspected schema, so you pick from real table names).
- **Mode** — **Guided** or **Raw**:
  - **Guided** builds the query for you from filters. Add filters with the **Add filter** button. Each filter row is:
    - **Not** — tick to negate the whole condition (wraps it in q `not`).
    - **Column** — chosen from the table's real columns.
    - **Op** — one of `=  <>  <  <=  >  >=  in  like`.
    - **Value(s)** — the right-hand side. For `in`, comma-separate values (`AAPL,MSFT`). For `like`, use q patterns such as `A*` or `*USD*`.
    - **Type** — how the value is written into q: **symbol** (`` `AAPL ``), **number** (`101.5`), or **string** (`"buy"`). (`like` is always a string pattern.)
  - **Raw** lets you type qSQL directly, for anything the form can't express. This is also the only mode that can **reuse an earlier step's result** (next section).
- **Query preview** — under each step you see the q query that will run. In Raw mode, references appear unresolved (they are filled in at run time).

Use **Add step** to chain more steps; each step has a **Remove** control.

### 3. Sharing a result between steps (chaining)

**This is the key idea of a chain.** A later step (in **Raw** mode) can inject the values from an earlier step's result column using:

```
{{stepN.column}}
```

- `stepN` is the earlier step's output name: `step1`, `step2`, ... (**not** the table name).
- At run time, `{{stepN.column}}` is replaced by the **distinct values** of that column from step N's result, formatted as a q list literal:
  - symbols → `` `AAPL`MSFT ``
  - numbers → `1 2 3`
  - a single value → `enlist \`AAPL`
- References work **across servers**, so step 1 can read the orders server and step 2 can look those symbols up on the market server.

In Raw mode, use the **Insert reference** dropdown to drop a `{{stepN.col}}` token in, then change `col` to the column you want.

#### Worked example (with the demo servers)

> "For every order we have, alert me if any of those symbols' bid goes above 100."

**Step 1** — Server `orders_demo`, Table `target`, Guided, no filters. Output name `step1`.

```
select from target
```
returns, say, symbols `AAPL, MSFT, GOOG`.

**Step 2** — Server `kdp_demo`, Table `QATT`, **Raw**:

```
select from QATT where sym in {{step1.sym}}
```
At run time `{{step1.sym}}` becomes the distinct `sym` values from step 1, so the query actually run is:

```
select from QATT where sym in `AAPL`MSFT`GOOG
```

**Trigger** — *At least one row matches*, column `bid`, `>`, `100`.

If any of those quotes has `bid > 100`, the alert fires. If a reference points at a column the earlier step didn't return, the preview shows a clear `reference error` instead of a broken query.

### 4. Trigger condition

Checked against the **final step's** result. Pick the shape that fits:

| Condition | Fires when | Extra fields |
|-----------|-----------|--------------|
| **No rows returned** | the final result is empty | — |
| **Has at least one row** | the final result has any rows | — |
| **Row count is at least N** | row count ≥ N | `N` |
| **At least one row matches** | some row satisfies `column op value` | column, op, value, value type |
| **Every row matches** | all rows satisfy `column op value` | column, op, value, value type |
| **Aggregate matches** | `agg(column) op value` (agg = max / min / avg / sum) | aggregate, column, op, value |

For the row/aggregate conditions you also choose a **value type** (number, symbol, string) so comparisons like `sym = \`AAPL` work, not just numeric ones.

A plain-English summary (*"Triggers when at least one row has bid > 100"*) is shown as you build it.

### 5. Notify (chosen per alert)

Tick which channels fire for this alert:

- **In-app banner** — a red banner on the Monitor.
- **Sound** — a short beep on trigger.
- **Browser notification** — an OS-level notification that appears even when the tab is minimized. Requires clicking **Enable alert notifications** once on the Monitor and allowing the browser prompt (see [Notifications](#notifications)).
- **Email recipients** — comma-separated addresses. Needs SMTP configured in Admin.
- **Teams / Slack webhook URLs** — comma-separated incoming-webhook URLs.

**Re-arm** controls how often it re-notifies while it stays triggered:

- **transition** — notify once when it goes from not-triggered to triggered (default).
- **cooldown** — re-notify at most every N seconds.
- **every_tick** — notify on every check while triggered.

### 6. Keep result on trigger (retention)

Controls what the Monitor's **Result** view keeps for this alert. Data is only ever captured on a **triggered** check.

- **Latest** — refresh to the newest rows on every triggered check.
- **Snapshot** — freeze the rows from the moment it triggered (until the alert clears and fires again, or you Clear it).

### 7. Check result (preview) and Save

- **Run now** executes the whole chain immediately against live data (nothing is saved, no notification sent). You see each step's resolved query and rows, and whether it *would* trigger. Use this to validate an alert before saving.
- **Save alert** stores it. Existing alerts are listed under **Your alerts** where you can toggle, edit, **clone**, or delete them.

### Cloning an alert

When you need an alert that's almost identical to an existing one (say, the same chain but a different symbol filter or threshold), click **Clone** on it under **Your alerts**. It loads into the builder as a **new draft** named *"… (copy)"* with all its steps, trigger, channels and timing pre-filled — tweak whatever you need and **Save**. The original is untouched; cloning never overwrites it.

---

## Monitoring

On the **Monitor** view:

- **Monitoring** toggle — checks (and notifications) run **only while this is on**. Turning it off, or just interacting with the page, never fires alerts. Once on, monitoring **keeps running while you're on any other tab** (Builder / Admin / Reports), and the on/off choice is **saved to the database**, so it **automatically resumes the next time you open the app**.
- **Check granularity** — how often the loop wakes (5s–15m). Each alert still only runs when its own interval has elapsed. Set the granularity at or below your fastest alert's interval.
- **Today** — a durable per-day summary (Triggered / Armed / Errors / Notifications, with the number of distinct alerts behind each). These come from the persisted run log, so they **carry across restarts** — restarting the app part-way through the day does not reset the day's tally to zero.
- **Now** — a live KPI row (Alerts / Armed / Triggered / Errors) reflecting the current state of each alert this instant, a banner per currently-triggered alert, and one row per alert with a status badge, row count, and next-check countdown.
- **NEW badge** — when an alert triggers, a red **NEW** flag stays next to it as a reminder until you open it with **View**. It's persisted, so the reminder survives a restart and only clears when you actually look.
- **View** on an alert opens the **Result** page (full table + exports + copy). See below.

The evaluation loop runs in the app shell (not just the Monitor page) using a Streamlit fragment, so it refreshes without reloading the whole page and keeps ticking regardless of which view you're on.

> On errors: the **Errors** counters (both Today and Now) count alerts whose **query chain failed to run** this cycle — an unreachable server, malformed qSQL, or a missing table/column. An alert in the error state isn't evaluating its trigger at all, so a non-zero Errors count is a health signal to go fix the connection or the query.

## The Result page

Reached via **View** on a Monitor row (available once an alert has fired). Opening it also clears that alert's red **NEW** badge. If the in-session result has been lost (for example after a restart), the page falls back to the most recent **stored daily snapshot** so there's still something to look at. It gives you the full table plus flexible ways to get the data out:

- The full result table (searchable, sortable, expandable to fullscreen).
- **Excel** and **CSV** download.
- **Copy** popover:
  - **Column** tab — pick a column and copy it as one-per-line, comma-separated, or a **q list literal** (`` `AAPL`MSFT ``), with a *Distinct only* toggle. Handy for pasting a set of symbols straight into another query.
  - **Whole table** tab — copy everything as TSV.
- **Clear** to forget the captured result.

## Notifications

To get alerts even when the browser is minimized:

1. On the **Monitor**, click **🔔 Enable alert notifications** and allow the browser prompt (one time).
2. Make sure the alert has **In-app banner** (and optionally **Sound**) selected in its notify settings.
3. Turn **Monitoring** on.

Requirements and gotchas:

- Browser notifications need a **secure context**. `http://localhost` counts, so running locally is fine. If you open the app via a network IP over plain HTTP (`http://10.x.x.x:8501`), browsers block notifications; use HTTPS for shared/network deployments.
- If nothing appears, check the tab's site-notification permission in the browser (it may be stuck on "default" or "blocked").
- **Email** needs SMTP host / port / from-address set in **Admin → Email (SMTP)**.
- **Webhooks** just need the incoming-webhook URL from Teams or Slack.

---

## Dashboards

Saved pages built from KDB queries: KPIs, tables and charts that refresh while
the page is open and export to a PDF of exactly what is on screen. Where an alert
answers "tell me when this happens", a dashboard answers "show me the state of
this, continuously".

**Try it:** Admin → *Load demo servers*, then Dashboards → *Import* and pick
`docs/examples/demo_orders_dashboard.json`. It runs against the demo `orders`
environment, so it works with no real KDB — open it, then switch the period to
*Last 7 days* to watch the same dashboard re-query the historical server.

`docs/examples/short_sell_dashboard.json` is the second example: the
`short_sell_report.py` one-pager rebuilt as a dashboard. It expects the **real**
order schema (`target` with `id_target`, `size`, `executed`, `nReject` and
market-suffixed syms), so import it once you have a real `orders` environment
registered — it will show error panels against the demo tables, which do not have
those columns.

### Datasets — query plus shaping

A dataset produces one table of rows. Guided mode builds the where clause from
the table's real columns; raw mode takes q directly. Either way an ordered list
of **transforms** shapes the result, no Python required:

| Transform | What it does |
|---|---|
| `derive` | a new column — an arithmetic expression (`100 * executed / size`) or a suffix map, where you say how many trailing characters make the suffix (3 reads `700.HK` as `.HK` → Hong Kong) |
| `filter` | drop rows, including on a derived column |
| `groupby` | keys + aggregations (count, nunique, sum, mean, min, max) |
| `sort` / `limit` / `rename` | the usual finishing touches |

Datasets run in order and can feed each other with `{{name.column}}`, the same
substitution the alert builder uses for chained steps.

**kdb integer nulls arrive as nulls, not as numbers.** An int vector has nowhere
to put "unknown", so a q null *is* a value — the lowest the width holds. Left
alone, an order that missed a left join reaches pandas holding
`-2,147,483,648`, and summing that column reports rejections in the billions:
wrong, but formatted, coloured and plottable like anything else. Every frame is
scrubbed on arrival, so those three sentinels become blanks and a sum counts
only the rows that have a value. Still write `0^` in your own joins where zero
is the honest answer — a blank cell and a nought say different things to whoever
reads the report.

**A result that is not a number is not printed as one.** Dividing by zero is the
ordinary way to get one — a completion percentage against a total that came back
zero — and an infinity formats, colours against a threshold and plots like any
other figure, besides making every aggregate over that column infinite too. A
derived value that comes out infinite becomes a blank, the same answer a kdb null
gets.

**A negative result is left exactly as it computed.** It *is* a number, and it is
usually the only visible sign that something upstream is wrong: a total that
summed below zero, an unset size, a sign convention the query did not expect.
Blanking it would hide the very thing that needs fixing. Flag it instead — a
table `highlight` or a KPI `threshold` on `< 0` turns it red without changing it,
which is how the short-sell example marks a market whose order quantity is not
positive.

**Run and inspect each step** in the Data section runs the pipeline stage by
stage: the query's own result first, then the frame after every transform, each
with its row count, how many rows it gained or lost, and which columns it added
or dropped. When a transform fails, the run stops there and names it — the frame
shown above it is exactly what it was handed, so you can see *which* step went
wrong rather than only that the dataset did. Running a raw-q dataset also teaches
the editor what columns that query returns, which is what the column pickers
offer from then on.

### Environments — linking your servers

A connection has one of three **kinds**:

| Kind | What it holds |
|---|---|
| **Real-time** | today's data, no `date` column |
| **Historical** | the partitioned HDB — the same tables plus a `date` column |
| **Market data** | reference data (instruments, sectors, lot sizes); not partitioned by date |

Connections are grouped into **environments**. Putting a real-time and a
historical server in the *same* environment **links them**: they are two views of
the same data, and a dashboard switches between them by period.

| Name | Environment | Kind |
|---|---|---|
| `order-rdb` | `orders` | Real-time |
| `order-hdb` | `orders` | Historical &nbsp;← linked to `order-rdb` |
| `refdata` | `marketdata` | Market data |

Admin's **Environments** panel shows each pair and confirms the link, or flags a
half-configured environment.

**Not every environment has two sides.** A date-partitioned feed with nothing
live behind it is historical and nothing else. Tick **No counterpart** on that
server and the environment stops being reported as half-configured: Admin shows
it as *historical only, by design*, it is no longer offered when you add a
server of the other kind, and a dashboard that asks it for real-time is told the
environment is historical only rather than to add a server nobody intends to
add. Untick it to go looking for the counterpart again.

The box is a statement about the *future*, not a description of today. A server
that simply has not been paired yet is left alone — that is somebody midway
through setting up, and it keeps the ordinary "add one in Admin" nag.

Every registered server has an **Edit** button for
its name, host, port, kind and environment — so linking two servers you already
registered, or moving one to a different environment, never means deleting and
re-adding it. Changing a server's address clears its cached schema, since the
tables on the new host may differ; run **Introspect** afterwards.

A dataset targets the *environment*, never a server. The dashboard's **period**
control — Real-time, Today, Last 7 days, Last 30 days, Month to date, Last month,
Year to date, or a custom range — decides which one is queried.

**A dashboard says which periods it offers.** *Periods offered*, in the editor
header, is one of:

| | |
|---|---|
| **Both — switch between them** | the default: the viewer picks any period, and each dataset resolves to the real-time server or its historical twin |
| **Real-time only** | the period control becomes a label; nothing offers a date range |
| **Historical only** | every range stays; only *Real-time* goes |

Switching period means switching server, so *both* is only honest where the
environments have both sides. Offering it over one declared single-sided is
reported as a problem naming the environment and the setting to use instead. A
period stored before the declaration — historical-only, but left on Real-time —
lands on today's partition rather than resolving to a server that is not there.
The caption beside the control lists what each environment this dashboard reads
can actually serve.

**Market data ignores the period.** Reference data is not partitioned by date, so
those datasets always hit the market-data server and never receive a date clause,
whatever the dashboard is showing. That means one page can mix a 7-day order
history with a live instrument list, and each dataset goes to the right server.

The date constraint is **never stored in the dataset's filters**; it is injected
at run time. So flipping a dashboard between real-time and a date range needs no
edit to the dataset, and flipping back is lossless. Guided datasets get
`date within (2026.06.01;2026.06.30)` as the **first** where-clause, which is what
lets kdb+ prune partitions instead of scanning.

Raw q must constrain `date` itself, using `{{date_from}}`, `{{date_to}}` or
`{{date_list}}`:

```q
select from target where date within ({{date_from}};{{date_to}}), side=`sellshort
```

For **one query that serves both modes**, wrap the parts that only apply to one
of them in `{{#historical}}…{{/historical}}` or `{{#realtime}}…{{/realtime}}`.
The block is kept or dropped depending on which server the period resolves to:

```q
select {{#historical}}date{{/historical}}{{#realtime}}date:.z.d{{/realtime}}, sym, size
  from target
  where {{#historical}}date within ({{date_from}};{{date_to}}), {{/historical}}
    side=`sellshort
```

Without the guard a date placeholder would reach KDB verbatim in real-time mode,
so a dataset that leaves one unguarded is refused with an explanation rather than
sent.

**Saving a historical raw dataset without a `date` reference is refused.** An
unconstrained query against a partitioned HDB does not error — it reads years of
data and hangs a refreshing page.

A dashboard stores the range as a *spec*, not as dates, so a saved "last 30 days"
dashboard means the last 30 days whenever you open it. Individual datasets can
override the dashboard period (`inherit` / `realtime` / their own range), which is
how you put a 30-day trend next to today's live fills on one page.

### Layout and widgets

A dashboard is rows of 1–4 widgets, each row with a printed height in inches.

| Widget | Notes |
|---|---|
| `kpi` | one aggregate, formatted, optionally turning red past a threshold |
| `table` | column picker, per-column headers and formats, conditional highlighting |
| `bar` `line` `scatter` | x/y, optional split-by series, sorting, trend line |
| `hist` `box` `heatmap` `pie` | distributions, spreads, grids, composition |
| `text` | markdown with `{{dataset.agg.column}}` placeholders that update with the data |

Formats are picked by **sample rather than by spec** — you choose `1,234.57` or
`27 Jul 2026` or `09:30:15`, not `,.2f` or `%d %b %Y`. The catalogue covers
numbers, dates, timestamps and q `time` columns (which arrive as durations, so
they take a time-of-day format and otherwise print to the millisecond), with a
Custom entry for anything else and a live sample of what your spec produces. A
table's headers and formats are keyed to the **column**, not to its position, so
removing one column never shifts another's settings onto it, and a column you
deselect keeps what you gave it in case you put it back. Print order is changed
where the columns are listed — arrows move a column up or down, taking its
header and format with it, instead of deselecting the lot and picking them again
in the order you wanted.

The **Layout** editor shows where the page breaks will fall before you generate
anything: a `page N` badge on every row, a `page break` marker where a new page
starts, how many inches are still free at the bottom of each page, and the total
page count. Row heights are printed inches, so reordering or resizing rows moves
the breaks — you can lay the report out from the app instead of exporting a PDF
to find out. The figures come from the same pagination the PDF uses, so the two
cannot disagree.

The editor has four sections — **Data**, **Layout**, **Preview** and
**Library** — and validates on save: unknown datasets or environments, duplicate
dataset names, forward references, missing tables, over-full rows and missing
date constraints are all reported before anything is written.

### The component library

A transform worth working out once — deriving `market` from a `sym` suffix, say
— and a widget worth laying out once are both worth keeping. The bookmark button
on any transform or widget saves it under a name; **From library** next to *Add
transform* / *Add widget* adds it back, here or in any other dashboard.

What lands in the dashboard is a **copy**, not a link. Edit it as freely as
anything else you built by hand: a one-off tweak stays a one-off, and the saved
component is untouched until you deliberately save over it. Saving offers the
names already in the library alongside a blank to type a new one, so "keep this
improvement" and "keep this as a variant" are the same two clicks. A saved widget
naming a dataset the target dashboard does not have is bound to one it does.

The **Library** section lists everything saved, with what each one contains, and
renames or deletes them. Deleting affects nothing already built — those are
copies too.

### Refresh

Each dashboard has its own interval (off, 5s … 15m) and runs inside a Streamlit
fragment while its page is open. Navigate away or switch tabs and it stops — a
dashboard you are not looking at costs nothing. Nothing runs in the background,
and the alert engine is untouched.

The tab strip uses pills rather than `st.tabs` deliberately: `st.tabs` executes
every tab's body on each rerun, which under a refresh timer would fire every
dashboard's queries at KDB continuously. The open dashboard is in the URL
(`?dash=<id>`), so it is bookmarkable and survives a browser refresh.

### PDF

*Generate PDF* renders the frames **already on screen** — it never re-queries, so
the downloaded page shows the numbers you were looking at, not a fresh fetch taken
a moment later. *Preview pages* shows the real printed pages inline first, with
Previous/Next and a slider to step through a multi-page report.

The report holds still while you read it. Frames are taken on the refresh
interval — or when **Refresh** is pressed, in the header bar and beside the
preview — and never merely because something was clicked, so turning a page
neither re-queries KDB nor redraws the pages. Each page is drawn once and kept
against the frames it came from; new frames replace the lot. The preview names
the snapshot it is showing (*the 09:15:03 frames*) so it is clear what is on
screen.

The title band is the dashboard name and the period it covers, nothing else —
`2026-06-01 → 2026-06-30` for a range, `2026-07-26 09:15` for a real-time
snapshot. Only page 1 carries it; continuation pages start straight into the
content, which reclaims a third of an inch on every later page.

On screen the charts are interactive Plotly (hover a line chart to read every
series at that x); in the PDF the same resolved plot model is drawn by
matplotlib/seaborn onto A4, with a shared palette so a series keeps its colour in
both. Pages paginate automatically, and a dataset that failed prints a visible
error panel rather than being silently dropped — a missing chart in a printed
report reads as "nothing to report".

**A table longer than its row carries on over the following pages.** The type
shrinks to fit first — a dozen rows print at full size, eighteen tighten a
little — and once it would drop below legible, the table continues instead. A
widget is titled where it starts and nowhere else: the pages it runs onto carry
no heading, only the column names above the rows. Every chunk landing on the
same page joins into one block, however many it takes, so the report reads as a
single table rather than the same header stamped every twenty rows. Everything
else in the row prints once, with the first chunk.

The page count therefore follows the data, and the Dashboards page states it
before you generate. The Layout editor can only count the layout, since it has
no results in hand, so it shows a floor: *prints on N pages at least*.

A table needing more than 50 chunks is treated as a data dump rather than a
report: it stops there and the last chunk says `showing 950 of 20,000 rows`.
Nothing is ever dropped silently. If you hit that, narrow the query or lower the
dataset's **Max rows** — a thousand-page PDF helps nobody.

### Sharing

Each card has its own **Export** button (one dashboard, named after it), and
**Export all** downloads the lot as `kdbmonitor_dashboards.json`. **Import**
accepts one or more `.json` files.

Imports never overwrite: a name that already exists is suffixed
`(imported)`, `(imported 2)` and so on, so re-importing gives you a copy to
compare against rather than replacing what you have. Dashboards reference
*environments* by name rather than servers, so a file lands cleanly on any
machine whose Admin has the same environment names.

---

## Sharing alerts and connections

Each moves from the page that owns it. Both write the same file format, so
either import accepts either file and takes the part it is responsible for.

**Admin → Import / export connections** hands someone your KDB servers.

- **Export connections** — writes `kdbmonitor-connections.json` with the registered servers (name, host, port, kind, environment) and **no alerts**.
- **Import** — new connections are added; any whose name already exists are skipped, keeping yours. Run **Introspect** afterwards so the guided builder knows their tables and columns. Upload a full bundle here and its alerts are left alone — it says so, and the Alert builder takes those.

**Builder → Import / export alerts** moves the alerts themselves.

- **Export alerts** — pick which to include (defaults to all). The downloaded `kdbmonitor-export.json` carries those alerts **and all your connections**: an alert without its server would import to nothing.
- **Import** — **alert name clashes abort the import.** If any incoming alert is named like one you already have, nothing is imported and you are told which. Rename or delete the existing ones first. Connections in the file are added or skipped by name, exactly as in Admin.

A connection's cached **schema is never exported** either way — it is derived data, re-fetched with Introspect after importing.

Older alert-only export files (from earlier versions) still import.

---

## Using a real KDB server

In **Admin → Add a KDB connection**, enter a **Name**, **Host**, and **Port** (no auth). Save, then click **Introspect** to load its tables and columns so the guided builder can offer them. From then on it behaves exactly like the demo servers, but against your real data.

Real KDB context this was designed around: a `KDP`-style server holding `QATT` (bid / ask / volume by symbol, historical by symbol + date or real-time by symbol), and a separate order server with tables like `target`, `work order`, `target_state`.

---

## Project layout

```
app.py                     # Streamlit entry: theme, navigation, page wiring
.streamlit/config.toml     # dark trading-desk theme (native, no custom CSS)
kdbmonitor/
  core/                    # UI-independent, unit-tested logic
    models.py              # dataclasses (Alert, Step, Filter, ...) + (de)serialization
    storage.py             # SQLite: connections, alerts, run history, settings
    client.py              # KDB client protocol, PyKX client, demo routing, cache
    mock.py                # in-memory mock KDB (the demo servers)
    schema.py              # table/column introspection
    qfmt.py                # q literal formatting
    chain.py               # build step qSQL, {{step.col}} substitution, run/preview
    conditions.py          # trigger-condition evaluation
    rearm.py               # re-arm decision (transition / cooldown / every_tick)
    notifiers.py           # in-app / email / webhook dispatch
    evaluate.py            # evaluate one alert end-to-end
    portability.py         # export / import bundles
    exporting.py           # Excel / CSV / copy helpers
    reporting.py           # day/period report model + Excel rendering
    dashboard_models.py    # dashboards: Dataset, Transform, Row, Widget + JSON
    timectx.py             # period spec -> dates, q date clause, {{date_*}} refs
    transform.py           # dataset transforms (derive/groupby/sort/...)
    dataset.py             # run a dataset: env+period -> connection -> rows
    theme.py               # palette shared by both renderers
    plotmodel.py           # widget + rows -> resolved, backend-agnostic plot
    render_plotly.py       # PlotModel -> interactive figure (screen)
    render_mpl.py          # PlotModel -> matplotlib axes (print)
    dashpdf.py             # rows -> A4 pages -> PDF bytes
  ui/                      # thin Streamlit views
    admin.py  builder.py  monitor.py  result.py  reports.py  common.py
    dashboards.py          # gallery, tab strip, live view, PDF export
    dashboard_editor.py    # dataset + layout editors, save-time validation
    engine.py              # the monitoring loop (runs in the app shell, every tab)
```

## Testing

```bash
python -m pytest
```

The core logic is covered by unit tests against a fake KDB client, and the Streamlit pages have smoke tests (via `AppTest`) that render each view against the demo mock.

---

## Notes and limitations

- Checks run only while the app is open and **Monitoring** is on — but on any tab, and the on/off state is remembered across restarts.
- KDB connections use host + port only (no authentication).
- Per-day counters, the monitoring on/off state, the "seen" state behind the NEW badge, and triggered-result daily snapshots are persisted in `kdbmonitor.db`, so they survive restarts. The *live* in-session result view is per-session; when it's gone the Result page falls back to the stored daily snapshot.
