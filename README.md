# KdbMonitor

A monitoring tool for KDB+/q databases. Build **chains of KDB queries** through a guided UI and get **notified** when a chain's final result meets a condition you define. Aimed at trading / algo teams who need to watch orders and market state without hand-running queries all day.

Built with [Streamlit](https://streamlit.io/) and [PyKX](https://code.kx.com/pykx/).

---

## What it does

- **Connect** to one or more KDB servers (just host + port).
- **Build alerts** as a chain of query steps. Each step runs a query; a later step can reuse an earlier step's result (see [The alert builder](#the-alert-builder)). The final step's result is checked against a **trigger condition**.
- **Monitor** alerts live. While monitoring is on, each alert runs on its own interval. When one triggers you get an in-app banner, an optional sound, a **browser notification that shows even when the window is minimized**, and optionally an email or a Teams/Slack message.
- **Investigate** results: preview an alert's output while building it, and open a full **Result page** for a triggered alert to view, export (Excel/CSV) or copy the data.
- **Share** whole setups (connections + alerts) with teammates via a JSON export/import.
- **Try it with no KDB at all** using the built-in **demo mock**.

> Note on scope: checks run **only while the app is open** in a browser and monitoring is toggled on. There is no always-on background daemon (by design, for now). The core logic is written to make adding one later straightforward.

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

## The three views (plus a Result page)

| View | Purpose |
|------|---------|
| **Monitor** | The live dashboard. Turn monitoring on/off, set the check granularity, watch statuses, get notifications, open results. |
| **Builder** | Create, edit, delete, enable/disable alerts. Import/export. Preview an alert's result before saving. |
| **Admin** | Register KDB connections (host + port), introspect their tables/columns, load the demo servers, and set SMTP for email alerts. |
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
- **Save alert** stores it. Existing alerts are listed under **Your alerts** where you can toggle, edit, or delete them.

---

## Monitoring

On the **Monitor** view:

- **Monitoring** toggle — checks (and notifications) run **only while this is on**. Turning it off, or just interacting with the page, never fires alerts.
- **Check granularity** — how often the loop wakes (5s–15m). Each alert still only runs when its own interval has elapsed. Set the granularity at or below your fastest alert's interval.
- A KPI row (Alerts / Armed / Triggered / Errors), a banner per currently-triggered alert, and one row per alert with a status badge, row count, and next-check countdown.
- **View** on an alert opens the **Result** page (full table + exports + copy). See below.

The loop uses a Streamlit fragment, so it refreshes without reloading the whole page (your toggles and state survive).

## The Result page

Reached via **View** on a Monitor row (available once an alert has captured a triggered result). It gives you the full table plus flexible ways to get the data out:

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

## Sharing alerts and connections

**Builder → Import / export (alerts & connections)** moves a whole setup between machines or teammates.

- **Export** — pick which alerts to include (defaults to all). The downloaded `kdbmonitor-export.json` contains those alerts **and all your connections** (name, host, port). A connection's cached **schema is not exported** (it is derived data; re-fetch it with Introspect after importing).
- **Import** — upload an export file. Then:
  - **Alert name clashes abort the import.** If any incoming alert has the same name as one you already have, nothing is imported and you're told which names conflict. Rename or delete the existing ones first.
  - **Connections are matched by name.** New ones are added; any whose name already exists are skipped (your local connection is kept). After importing, run **Introspect** on the new connections in Admin so the guided builder knows their tables/columns.

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
  ui/                      # thin Streamlit views
    admin.py  builder.py  monitor.py  result.py  common.py
```

## Testing

```bash
python -m pytest
```

The core logic is covered by unit tests against a fake KDB client, and the Streamlit pages have smoke tests (via `AppTest`) that render each view against the demo mock.

---

## Notes and limitations

- Checks run only while the app is open and **Monitoring** is on.
- KDB connections use host + port only (no authentication).
- Alert result snapshots live in the browser session (they are not persisted to disk).
