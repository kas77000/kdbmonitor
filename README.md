# KdbMonitor

A monitoring tool for KDB+/q databases. Build **chains of KDB queries** through a guided UI and get **notified** when a chain's final result meets a condition you define. Aimed at trading / algo teams who need to watch orders and market state without hand-running queries all day.

Built with [Streamlit](https://streamlit.io/) and [PyKX](https://code.kx.com/pykx/).

---

## What it does

- **Connect** to one or more KDB servers (just host + port).
- **Build alerts** as a chain of query steps. Each step runs a query; a later step can reuse an earlier step's result (see [The alert builder](#the-alert-builder)). The final step's result is checked against a **trigger condition**.
- **Clone** an existing alert as a starting point when you need a near-duplicate with a small query change.
- **Monitor** alerts live. Once monitoring is on it **keeps running on every tab and auto-resumes after a restart** (the on/off state is saved), so alerts keep arming and triggering all day without babysitting the Monitor tab. Each alert runs on its own interval.
- **Be told your own way**: every alert picks any combination of an in-app message, a sound, a **browser notification that shows even when the window is minimized**, a notification that **brings the window back to the front** when you click it (with a flashing tab title until you do), a **pop-up showing the rows that fired it**, an email, and a Teams/Slack message.
- **Only when it matters**: give an alert **active hours** — 16:30, or 17:45–18:00, Mon–Fri, in whatever timezone you think in — and it isn't evaluated at all outside them, so a check that only means something in a window stops crying wolf for the rest of the day.
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
| **Dashboards** | Build and watch saved dashboards: KPIs, tables and interactive charts over one or more KDB queries (or an uploaded file), refreshing on their own interval, exportable as a PDF. |
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
  - **Raw** lets you type qSQL directly, for anything the form can't express. This is also the only mode that can **reuse an earlier step's result** (next section). The box is a [q editor](#writing-q): line numbers, Tab to indent, and a coloured copy of the query underneath.
- **Query preview** — under each step you see the q query that will run, syntax-coloured and numbered. In Raw mode, references appear unresolved (they are filled in at run time).

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

### Writing q

Everywhere q is typed — a Raw builder step, a dashboard's raw dataset — the box
has **line numbers down the side**, **Tab indents** by two spaces instead of
jumping to the next control, **Enter keeps the indent** of the line above, and
a monospace face. Everywhere q is *shown* — the query preview, a dataset's
resolved query, a preview step — it is **numbered and syntax-coloured**.

The colouring is q's, not SQL's, which is the whole reason it is written here
rather than borrowed:

| | |
|---|---|
| `/ comment` | only at the start of a line or after a space. `sum/` is the *over* adverb and `px%qty` is division — neither greys out the rest of the line |
| a line that is only `/` | opens a comment block, closed by a line that is only `\` |
| `` `AAPL ``, `` `:localhost:5000 `` | symbols and handles, coloured as themselves |
| `"a string"` | including `` ` `` and `/` inside it, which are not a symbol and not a comment |
| `select … by … from … where` | plus q's built-in verbs; a table or column name deliberately stays body text |
| `.z.D`, `.Q.dd` | namespaces q reserves for itself. `.mydesk.helper` is just a name |
| `2026.07.30D09:30:00`, `1b`, `0N`, `0x1f` | read as one literal each |
| `{{step1.sym}}`, `{{param:venue}}`, `{{conn:PROD}}` | this app's placeholders, not q at all — given the accent colour so they stand out from the query they sit in |

The box itself is an ordinary Streamlit text area holding the real value; the
line numbers and key handling are added to it. If that script cannot run —
an old browser, a locked-down frame — you get a plain text box that still
works, rather than a missing field.

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

Pick as many deliveries as you want from **How this alert reaches you** — they all happen, and a one-line summary underneath says exactly what the alert will do:

- **In-app message** — a toast on whatever page you're on, plus the red banner on the Monitor.
- **Sound** — a short beep on trigger.
- **Browser notification** — an OS-level notification that appears even when the tab is minimized. Requires clicking **Enable alert notifications** once on the Monitor and allowing the browser prompt (see [Notifications](#notifications)).
- **Bring the window to the front** — the notification stays on screen until you click it, and clicking it raises the browser window; meanwhile the tab title flashes until you look. See [Bringing the window forward](#bringing-the-window-forward) for what a browser will and won't allow.
- **Pop-up with the result** — a modal in the app window with the rows that fired the alert, and a button through to the full Result page.
- **Email** — comma-separated addresses. Needs SMTP configured in Admin.
- **Teams / Slack webhook** — comma-separated incoming-webhook URLs.

The email and webhook boxes appear only once you pick those deliveries, and addresses are dropped if you unpick them — the summary line is the whole truth about who hears.

**Re-arm** controls how often it re-notifies while it stays triggered:

- **transition** — notify once when it goes from not-triggered to triggered (default).
- **cooldown** — re-notify at most every N seconds.
- **every_tick** — notify on every check while triggered.
- **on_change** — trigger only when the result data differs from the previous triggered snapshot.

### 6. Active hours (optional)

Some checks only mean something at certain times — a 16:30 mark, the last fifteen minutes before a cut-off — and produce false positives for the rest of the day. Turn on **Only run during set hours** and the alert is **not evaluated at all** outside its windows: no query, no trigger, no notification. It shows as **Off-hours** on the Monitor, with the time until it next wakes.

- **Timezone** — a searchable list of **IANA ids** (`Europe/London`, `Asia/Kolkata`), starting on your own machine's zone. Type to filter. Only IANA ids are offered, so a stored schedule means the same thing wherever it is read; daylight saving is computed on the day, not assumed. (An alert saved before this could hold a Windows name or an offset — it still runs, and opening it in the Builder resolves it to the equivalent IANA id.)
- **Days** — leave empty for every day, or pick the weekdays it runs.
- **Windows** — one or more `From`/`To` **time pickers**, stepping by the minute so `17:45` can be said. **At a moment** turns a `From` time into a one-minute window, which is how you say "alert me at 16:30". An end earlier than the start crosses midnight (`22:00` → `02:00` is one window), and a crossing window belongs to the day it *starts* on.

When a window closes, the alert is parked rather than left as it was — so a trigger still standing at 18:00 doesn't count as the "previous" state at tomorrow's open, and a `transition` re-arm fires properly the next day.

### 7. Keep result on trigger (retention)

Controls what the Monitor's **Result** view keeps for this alert. Data is only ever captured on a **triggered** check.

- **Latest** — refresh to the newest rows on every triggered check.
- **Snapshot** — freeze the rows from the moment it triggered (until the alert clears and fires again, or you Clear it).

### 8. Check result (preview) and Save

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
- **Now** — a live KPI row (Alerts / Armed / Triggered / Errors, plus **Off-hours** once something is parked) reflecting the current state of each alert this instant, a banner per currently-triggered alert, and one row per alert with a status badge, row count, and next-check countdown.
- **Off-hours** — an alert with [active hours](#6-active-hours-optional) outside its window shows this instead of a countdown, together with the window it keeps and how long until it opens. It is enabled and healthy, just deliberately not running — unlike **Disabled**, which is your switch.
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
2. Make sure the alert has **Browser notification** (and optionally **Sound**) selected in its notify settings.
3. Turn **Monitoring** on.

Requirements and gotchas:

- Browser notifications need a **secure context**. `http://localhost` counts, so running locally is fine. If you open the app via a network IP over plain HTTP (`http://10.x.x.x:8501`), browsers block notifications; use HTTPS for shared/network deployments.
- If nothing appears, check the tab's site-notification permission in the browser (it may be stuck on "default" or "blocked").
- **Email** needs SMTP host / port / from-address set in **Admin → Email (SMTP)**.
- **Webhooks** just need the incoming-webhook URL from Teams or Slack.

### Bringing the window forward

**No browser lets a page raise its own window on its own.** That permission was removed years ago because it was abused, and there is no flag or setting that gives it back. **Bring the window to the front** therefore does the strongest thing a page is actually allowed to do:

- the notification is posted with `requireInteraction`, so it **stays on screen until you click it** instead of fading after a few seconds — and on Windows, posting one also flashes the taskbar button;
- **clicking the notification focuses the browser window**, because that click is your own gesture and a browser honours `focus()` from inside it. One click and you're back in the app, whatever you were doing;
- the **tab title flashes** (`● AAPL bid breakout`) until you look at the window, so a window that is merely behind another one still gets noticed. It stops as soon as the window is focused, however you got there.

Pair it with **Browser notification** — on its own it is only the flashing title. For a genuinely unmissable alert, combine **Bring the window to the front** with **Pop-up with the result**: the click that raises the window lands you on the rows that fired it.

### The result pop-up

**Pop-up with the result** opens a modal in the app window as soon as the alert fires, wherever you are in the app: the alert's name, the time, the rows it returned (first 25), and **Open the full result** through to the Result page. It opens **once per trigger** — closing it is final, so a monitoring loop that ticks every few seconds cannot reopen a modal you just dismissed. A later trigger of the same alert opens it again.

---

## Dashboards

Saved pages built from KDB queries — or from a file you upload — of KPIs,
tables and charts that refresh while the page is open and export to a PDF of
exactly what is on screen. Where an alert answers "tell me when this happens", a
dashboard answers "show me the state of this, continuously".

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

Datasets run in order and can feed each other, the same substitution the alert
builder uses for chained steps. **Insert reference** in a raw dataset offers a
choice of two, because they are different things:

| Reference | What it becomes | What it is for |
|---|---|---|
| `{{name.column}}` | that column's distinct values, as a q list | a where clause — `sym in {{orders.sym}}` |
| `{{table:name}}` | the whole result, as a q table | a join — `{{table:mine}} lj \`id xkey orders` |

The column form can only ever ask *is it one of these*. The table form carries
the rows themselves, which is what you need when the answer has to keep what the
earlier dataset said beside each key — an uploaded file of order ids matched
against the OMS, with the file's own note column still attached. It works on any
dataset above it, including a raw query nobody has run yet, since it takes the
result whatever shape it turns out to be.

The rows travel inside the query text, so a large dataset makes a large query —
the same bargain the column form already offers, one column at a time. Types are
carried across properly: dates and timestamps are told apart by their values,
clock columns go as q `time` to the millisecond, and a gap arrives as a typed
null rather than as the word `nan`.

**A dataset can be another dataset, shaped again.** *Add dataset from another*
makes one whose source is not a server or a file but a dataset already on the
dashboard: `orders_by_basket` starts from whatever `orders` finished on and
applies its own transforms to it. `orders` is untouched — every widget already
pointed at it still shows what it showed — so one query can feed a table of raw
fills, a bar chart grouped by basket and a KPI ranked off that grouping without
being run three times or duplicated three ways. Derived datasets chain: one can
derive from another, as deep as the report needs.

A derived dataset may only read one **declared above it**, since that is the
order frames are produced in. That single rule is also what makes a cycle
impossible to express — one of any two datasets has to come second — and it is
why moving a dataset card down is what lets it reach another. Everything else
about it is ordinary: it takes transforms, a max-rows cap, parameters and
widgets exactly as any dataset does, and it belongs to a file dashboard and a
query dashboard alike. If what it reads is still waiting for an upload, so is
it — the panel says which file, rather than reporting a failure.

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
wrong rather than only that the dataset did. **Query sent** opens by itself when
nothing ran — the query is the thing to look at then — and carries a **Copy the
query** button, because what you do with a query that came back with an error is
run it somewhere you can poke at it. The queries in the alert builder's Check
result carry one for the same reason. It is the fully resolved query, dates,
dataset references, cross-process handles and parameters all filled in — the
text KDB actually received, not the template it was written as. Running a raw-q
dataset also teaches
the editor what columns that query returns, which is what the column pickers
offer from then on — and when a picker in the Layout section has nothing to
offer, **Find columns** sits above it and runs exactly that, so the answer is
where the question is. A picker still takes a typed name either way.

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

**An environment is one database and its twin, not a whole desk.** It holds a
single server per kind, so several databases a desk would call one environment
are registered as several — `PROD-ORDERS`, `PROD-QUOTES`, `PROD-REF`, each
pairing its own two sides. Registering a second real-time server under an
environment that already has one does not merge them: Admin says so, and until
one is moved only the first is reachable.

That is why a query spanning databases names the one it wants rather than asking
an environment to hold them all:

```q
h: hopen {{conn:PROD-QUOTES}};              / one named database
{{conn:PROD-ORDERS:historical}}             / a named side, from a live dataset
{{conn:PROD-ORDERS}}                        / whichever side the period is on
```

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
| `table` | column picker, per-column headers, formats and widths, conditional highlighting, on-screen grouping |
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
where the columns are listed: arrows nudge a column one place, and a **Move to**
box beside them takes a destination, so sending the last of twenty to the front
is typing `1` rather than nineteen clicks. It takes its header and format with
it either way. Rows are ordered the same way, and every row move says which row
went where — these controls belong to the *slot* they sit in, so using one twice
is a second, different move rather than the first one having failed.

The Move to box is deliberately **blank rather than showing the current
position**: a box that shows where you are is a box whose steppers move the row
and then spring back to the number they started on, which reads as nothing
having happened. The current position is the greyed-out placeholder instead.

**A column's width can be set rather than earned.** By default a column's share
of the table is proportional to the longest text in it, which is the right rule
until one note, comment or reason field earns half the table and squeezes the
eight short columns beside it. Setting a column to **Narrow**, **Medium** or
**Wide** fixes its width instead, so it no longer argues its case from its own
content. A name rather than a number, because a table has two outputs and each
spends its own currency: on screen Streamlit sizes the column in pixels, on the
page it takes a fixed share of the paper.

On the page a set width also buys back type size. A column the author has
narrowed takes no part in choosing how big the table prints — letting it would
mean one clipped note column shrinking every figure on the page, the opposite of
what narrowing it was for. It is cut to its box instead, with an ellipsis, the
same way an over-wide table has always been handled once the paper runs out.

**A table on screen can be read as a tree.** Pick a column in **Group rows by**
and the rows are gathered under its values — every order on a venue under that
venue, every fill in a basket under the basket — one heading per value with a
row count beside it, folded away until you open it. It narrows nothing: every
row is still there, one level down, and the column gathered on is not repeated
inside the group because the heading is already saying it.

Which column that is belongs to **whoever is reading**, not to whoever built the
dashboard. The same picker sits above the table in the live view, so the same
table gets read by venue at nine and by basket when something goes wrong at half
past, without opening the editor. What you choose there sticks — including
choosing *(none)* over a grouping the author set — and survives the refresh
underneath you, the same way a column filter does. The setting in the editor is
where a reader lands, not where they are stuck.

A few headings arrive open, since that is the whole table anyway; more than a
handful arrive folded, so the page is a summary you open one line of. Searching
or filtering opens them again — *4 of 900 rows* over four closed doors would be
that line telling the truth and showing nothing. A column with a different value
in every row is not offered (a fold between every row is not a grouping), and a
grouping that would run past 50 headings is listed flat instead, with the page
saying so rather than appearing to ignore the picker.

Grouping is a screen affordance, like sorting and the search box: **the PDF
prints every row in one flat list.** Paper has no folds to open. To print
by-group figures, group the *data* instead — a `groupby` transform, or a derived
dataset — which is a different and permanent thing.

The **Layout** editor shows where the page breaks will fall before you generate
anything: a `page N` badge on every row, a `page break` marker where a new page
starts, how many inches are still free at the bottom of each page, and the total
page count. Row heights are printed inches, so reordering or resizing rows moves
the breaks — you can lay the report out from the app instead of exporting a PDF
to find out. The figures come from the same pagination the PDF uses, so the two
cannot disagree.

**Copy puts a widget or a row on a clipboard, and you choose where it lands.**
Copying a widget makes a *Paste* button appear on every row with room for one;
copying a row makes a *Paste row here* appear in every gap between rows,
including the bottom. What is held is said once at the top of the section, and it
stays held until you clear it — so one carefully built table can be dropped onto
four rows without being built four times. Each paste is a real copy: editing one
never edits another.

The editor has four sections — **Data**, **Layout**, **Preview** and
**Library** — and validates on save: unknown datasets or environments, duplicate
dataset names, forward references, a derived dataset reading one declared below
it, missing tables, over-full rows and missing date constraints are all reported
before anything is written.

### Parameters — a form the reader fills in

A dashboard can ask for values before it draws anything. You declare the inputs
in **Data → Parameters**; the reader gets them as a row of controls above the
page, and each is referenced as `{{param:name}}`.

**Kinds** (what the reader is given): `text`, `number`, `date` (a date picker),
`toggle`, `choice` (a fixed list), `column` (a list read from a dataset's own
values). Nothing about the mechanism knows what any particular form is *for* —
one dashboard asks for an instrument and a date, the next for a trader id, a
venue and a threshold.

**Where you put it decides what it costs:**

| `{{param:x}}` appears in | changing it |
|---|---|
| a transform or a widget spec | re-shapes frames already in hand — no round trip, controls stay live |
| **a dataset's query** (raw q, or a guided filter's value) | goes back to the server, so the controls become a form with **Apply** and **Reset** |

A query parameter is substituted as a **q literal**, not as text — that is what
**Written into q as** on the parameter's card decides, and the card shows you
the answer as you pick it:

| written as | `AAPL` reaches the query as |
|---|---|
| `symbol` | `` `AAPL `` |
| `string` | `"AAPL"` (quotes escaped) |
| `number` / `date` | `100.5` / `2026.07.30` |
| `boolean` | `1b` / `0b` |
| `expression` | as typed — q the reader writes |

```
select from target where date={{param:d}}, sym in ({{param:sym}}), qty>{{param:min_qty}}
```

#### Rules — what a valid value looks like

Each parameter carries its own rules, and **the query does not run until every
value passes**. The reader is told which value and why, next to the control,
rather than being shown an empty dashboard.

- **Required** — a blank blocks the run.
- **Must match** — a regular expression, with **Say instead** for the sentence
  the reader should actually get ("Use an uppercase ticker, e.g. AAPL"). Your
  words are used in preference to the machinery's.
- **Minimum / Maximum / Whole numbers only** — for numbers.
- **Not before / Not after** — for dates, written absolutely (`2026-01-01`) or
  relative to the day they are read (`today`, `today-90d`, `today+1d`), so
  "nothing older than 90 days" is written once rather than retyped every
  morning.
- **Weekdays only** — for dates. A Saturday has no partition in most HDBs, so
  asking for one returns nothing and explains nothing; this says *"2026-08-01
  is a Saturday — pick a weekday"* instead.

Two checks are not yours to switch off, because they are about whether the
value can honestly be written into q at all: a `symbol` may only contain
letters, digits, `.`, `_` and `:`, and a `number`/`date` must actually be one.
`` `AAPL; delete from t `` parses as a symbol *and* a delete, and q would run
both. The one exception is the `expression` type, which exists to send q the
reader wrote — the editor says so in as many words, and a **Must match** rule
is how you narrow it again.

Rules are checked while you build, too: a default that fails its own rules, a
minimum above its maximum, or a pattern that will not compile are all reported
before the dashboard can be saved.

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

### Tabs

The strip along the top holds **the dashboards you have opened**, not every one
you have saved — so a desk with fifteen dashboards gets the two or three it is
working with, and the strip stays one row.

- **Open several at once** — tick as many as you like in the gallery and press
  **Open selected (N)**. Each gets a tab and you land on the first. A card's own
  **Open** button opens just that one; a card that already has a tab says so.
- **Tabs (N)** beside the strip is the tab list: close any tab (including ones
  scrolled out of sight), **Close others**, **Close all**, or open more without
  going back to the gallery.
- Closing the tab you are looking at moves to its left-hand neighbour, as a
  browser does; closing the last one puts you back in the gallery. **All
  dashboards** goes back to the gallery *keeping* your tabs — it is the new-tab
  page, not closing the window.
- **Closing a tab drops what it was holding**: the frames, the built PDF, the
  rendered pages and any file uploaded to it. That is the point of closing it.

Which tabs are open lives in the URL beside the active one
(`?dash=3&tabs=1,3,7`), so a refresh, a bookmark or a link sent to a colleague
brings back the same set. An id in the URL that no longer exists is dropped
rather than argued with.

The strip is `st.pills` restyled, not `st.tabs`, deliberately: `st.tabs`
executes every tab's body on each rerun, which under a refresh timer would fire
every open dashboard's queries at KDB continuously. Exactly one dashboard is
live at a time. The browser-tab look — one scrollable row, squared-off tops,
names clipped rather than tabs stretched — is scoped CSS hung off the widget's
own key (`st-key-dash_tabs`); a browser that doesn't support it gets the same
strip, wrapping.

### Refresh

Each dashboard has its own interval (off, 5s … 15m) and runs inside a Streamlit
fragment while its page is open. Navigate away or switch tabs and it stops — a
dashboard you are not looking at costs nothing. Nothing runs in the background,
and the alert engine is untouched.

### Dashboards from a file

A dashboard can read an uploaded CSV instead of KDB. Set **Data from** to *An
uploaded file* and it becomes a template: you profile one sample here, and
whoever opens the dashboard uploads their own file of the same shape and sees
their own numbers. It has no environment, no period and no refresh interval —
those describe a server, and there is none.

Profiling is a declaration, not a discovery. You say which line carries the
headers (the first, unless the export has a preamble), whether they run across
the page or down it, and where the data starts. The one thing read from your
sample is each column's type, offered as a list you correct — a column of
integer-looking order IDs is text, and only you know that. You can also name a
single cell outside the table, a report date sitting in line 1, and it becomes a
value the dashboard can show.

**The sample is then discarded.** Only the shape and the column contract are
stored, so a dashboard you export carries no data at all — just the shape of the
data it expects.

A file somebody uploads is checked rather than trusted, and checked by *reading*
each column as the type you declared rather than by guessing a type from their
file. Integers satisfy a number column, `"125,000"` reads as `125000`, and
anything that genuinely will not read is refused with the column, how many
values broke, and the line of the first: *column 'qty' expects a number; 12 of
500 value(s) could not be read as one (line 14: 'N/A')*. A missing column lists
what did arrive. Headers anywhere but the declared line are refused, quoting
that line so you can see what the app saw — nothing is searched for, because an
app that decides a file is close enough does not fail when it is wrong, it
reports the wrong thing.

Blanks become real nulls and print as `—` like any other gap. `NA`, `N/A`,
`NULL`, `-` and a few others count as blank by default, and the list is editable
per dataset — worth taking `-` off it if `-` is a real value in your data. A
column can be marked *No gaps* to refuse a file with blanks in it at all, which
is worth setting on whatever a chart is plotted against. A number too large to
hold is refused rather than quietly becoming an infinity.

Everything after that is ordinary: the same transforms, the same widgets, the
same layout, the same printed page. A file dataset and a query produce the same
kind of result, and by the time a widget sees one it cannot tell which it was.

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

**A wide table turns the page.** Columns are bound by the width of the sheet the
same way rows are bound by its height, and text given less room than it needs
does not shrink to suit — it prints over the column beside it. So the type size
is settled by the columns as well as the rows, whichever binds harder: a table
of a dozen columns sets smaller rather than colliding.

Where even that is not enough, **Printed page** decides which way up the report
comes out. On `auto` — the default — it prints portrait until a table cannot be
set legibly across it, and then the whole report turns landscape: A4 turned is
48% wider, which is worth roughly four more columns. The choice is made once for
the document, never page by page, so a reader is not rotating one report back
and forth; the Dashboards page says which way it will print and why (*turned
landscape to fit a table's columns*). Turning costs height, so a turned report
can run to more pages — that is the trade being made, legible type for paper.

Set it to `portrait` or `landscape` to decide yourself. Only the data knows how
wide a column really is, so `auto` is resolved when the PDF is generated; the
Layout editor, having no results, plans against portrait. A table too wide even
for a turned page has nothing left to give, so its cells are cut with an
ellipsis — `ORD…` — which at least says it was cut.

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
    qhighlight.py          # reading q well enough to colour it
    parameters.py          # a reader's values: into transforms, and into q
    paramrules.py          # what a form value must satisfy before a query runs
    chain.py               # build step qSQL, {{step.col}} substitution, run/preview
    conditions.py          # trigger-condition evaluation
    rearm.py               # re-arm decision (transition / cooldown / every_tick)
    schedule.py            # an alert's active hours: is_active / next_open
    notifiers.py           # in-app / email / webhook dispatch, browser payload
    evaluate.py            # evaluate one alert end-to-end
    portability.py         # export / import bundles
    exporting.py           # Excel / CSV / copy helpers
    reporting.py           # day/period report model + Excel rendering
    dashboard_models.py    # dashboards: Dataset, Transform, Row, Widget + JSON
    timectx.py             # period spec -> dates, q date clause, {{date_*}} refs
    transform.py           # dataset transforms (derive/groupby/sort/...)
    dataset.py             # run a dataset: env+period -> connection -> rows
    tablefilter.py         # narrowing a table by its columns, spreadsheet-style
    tablegroup.py          # gathering a table's rows under the value they share
    theme.py               # palette shared by both renderers
    plotmodel.py           # widget + rows -> resolved, backend-agnostic plot
    render_plotly.py       # PlotModel -> interactive figure (screen)
    render_mpl.py          # PlotModel -> matplotlib axes (print)
    dashpdf.py             # rows -> A4 pages -> PDF bytes
  ui/                      # thin Streamlit views
    admin.py  builder.py  monitor.py  result.py  reports.py  common.py
    dashboards.py          # gallery, tab strip, live view, PDF export
    dashboard_editor.py    # dataset + layout editors, save-time validation
    qeditor.py             # the q box (numbers, Tab) and coloured q output
    tables.py              # a dashboard table on screen: formats, search, filters, grouping
    popup.py               # the modal a fired alert opens, with its rows in it
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
