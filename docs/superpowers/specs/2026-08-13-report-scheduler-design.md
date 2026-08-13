# Report Scheduler — design

**Status:** design agreed, not built.
**Date:** 2026-08-13

---

## 1. The problem

A KdbMonitor dashboard is worth reading at 07:00, and the person who needs to
read it is not the person who built it. Today the only way a dashboard reaches
them is for somebody to open the app, pick the period, wait for the queries,
press *Download PDF*, and attach it to a mail. Every morning. That work is
mechanical, it is forgotten on holidays, and it happens late on the days it
matters most.

So: a process that does it on a clock.

Two constraints shape the whole design.

**It cannot depend on a browser.** A report due at 07:00 must go out whether or
not anybody left Streamlit open overnight. Streamlit only executes while a
session is being served, so the schedule cannot live inside it — the work has to
run in a process of its own.

**It must not be a second implementation of the dashboard.** The report that
arrives by mail has to be the report you would have downloaded by hand. Any
rendering, querying or pagination logic written a second time for the scheduler
is logic that will drift from the screen, and the drift will be discovered by a
recipient rather than by us.

The second constraint is satisfied for free, because of a property the codebase
already has: **`kdbmonitor/core/` imports no Streamlit.** Everything the report
needs is already a plain function.

```
run_datasets(dashboard, store, mgr, today, uploads, chosen, cache)
    -> dict[str, DatasetResult]                     core/dataset.py

dashboard_to_pdf_bytes(dashboard, results, rt, as_of, chosen)
    -> bytes                                        core/dashpdf.py
```

The scheduler is therefore not a new engine. It is a clock, a mailer, and the
bookkeeping that connects them to functions that already exist.

---

## 2. What this is and is not

In scope:

- Scheduled runs of an existing dashboard, producing the existing PDF.
- Delivery by e-mail to a fixed list of recipients, PDF attached.
- Per-job override of the dashboard's period and parameter values.
- A page in the app to define jobs, see their history, and start, stop or
  restart the scheduler process.

Deliberately out, and to stay out:

- **Excel or CSV attachments.** `core/exporting.py` exists and `openpyxl` is
  already a dependency, so this is a small addition later. It is not the ask.
- **Webhook or Teams delivery.** A webhook cannot carry a PDF, so it would
  deliver a different thing under the same name.
- **Saving reports to a folder or archive.**
- **Per-recipient personalisation.** One job, one PDF, one recipient list. A
  report per region is two jobs, which the parameter override already makes
  cheap.
- **Scheduling alerts.** Alerts have their own engine and their own active-hours
  model in `core/schedule.py`. This is about dashboards.

---

## 3. Shape

Two processes, one SQLite file, no shared memory.

```
Streamlit process                        Scheduler process
─────────────────                        ─────────────────
 ui/schedules.py    ──── writes ───►   kdbmonitor.db   ◄─── reads ────  scheduler/loop.py
 ui/admin.py (SMTP)                     report_jobs                            │
 app.py (autostart)                     report_job_runs ◄──── writes ──────────┤
        │                               settings                               │
        └──── spawns ──────────────────────────────────────────────────────────┘
                                                                               ▼
                                                                    core/reportrun.py
                                                        run_datasets → dashboard_to_pdf_bytes
                                                            → notifiers.send_report_email
```

The database is the only channel between them. That is a deliberate choice
rather than an economy: it means the scheduler can be started by the UI, by
hand, by a `.bat` in Startup or by Task Scheduler, and behaves identically in
each case, because none of those paths is privileged.

### 3.1 Modules

Each new module answers one question and can be read on its own.

| Module | Question it answers |
| --- | --- |
| `core/recurrence.py` | When does a job fire? Pure, no I/O. |
| `core/reportjob.py` | What is a job? The dataclass and its validation. |
| `core/reportrun.py` | How is one job run, once, start to finish? |
| `core/notifiers.py` *(extended)* | How is mail sent? Already owns `smtplib`. |
| `scheduler/supervisor.py` | Is a scheduler running, should one be, and who owns the lock? |
| `scheduler/loop.py` | The process: poll, run what is due, retry, record, sleep. |
| `scheduler/__main__.py` | `python -m kdbmonitor.scheduler`. |
| `ui/schedules.py` | The page. |
| `core/storage.py` *(extended)* | Two tables and their accessors. |

The split that matters is between `reportrun` and `loop`. `reportrun` takes a
job and runs it *now*: it knows nothing about clocks, retries, or whether
anything is due. `loop` knows about time and knows nothing about PDFs. Keeping
that line means the whole report path is testable without waiting for a clock,
and the whole timing path is testable without a KDB server.

---

## 4. The job

```python
@dataclass
class ReportJob:
    id: Optional[int]
    name: str
    enabled: bool = True
    dashboard_id: int = 0

    recurrence: Recurrence = field(default_factory=Recurrence)

    # None = use whatever the dashboard has saved.
    period: Optional[dict] = None          # a time_context spec
    parameters: dict = field(default_factory=dict)   # name -> value

    recipients: list[str] = field(default_factory=list)
    subject: str = "{dashboard} — {date}"
    body: str = ""

    retries: int = 3
    retry_delay_secs: int = 120
    grace_minutes: int = 60
```

There is no per-job failure-notice switch and no per-job admin address. A failed
run is reported to one place, configured once (§6.1).

`period` holds a time-context spec of the same shape the dashboard stores, so
the same `resolve()` in `core/timectx.py` reads both and the job cannot express
a period a dashboard could not. Its presets are the existing ones — `today`,
`yesterday`, `last_7d`, `last_30d`, `mtd`, `last_month`, `ytd` — and they are
resolved **against the date the job runs on**, which is what makes a job saying
*Yesterday* mean yesterday every morning rather than one particular Tuesday.

`parameters` holds only what the job overrides. A parameter absent from the dict
takes the dashboard's own default, so adding a parameter to a dashboard does not
break the jobs that already point at it.

`subject` and `body` accept `{dashboard}`, `{date}`, `{time}` and `{job}`.
An unknown placeholder is left as written rather than raising — a typo in a
subject line must not stop the report going out.

### 4.1 Recurrence

```python
@dataclass
class Recurrence:
    mode: str = "times"            # times | cron
    times: list[str] = ...         # ["07:00", "16:35"]
    days: list[int] = ...          # 0=Mon .. 6=Sun, empty = every day
    cron: str = ""                 # 5-field, mode="cron" only
    tz: str = "local"
```

Both modes answer the same question — **is this minute a firing minute?** —
and that is the only primitive the rest of the system uses:

```python
def fires_at(rec: Recurrence, moment: datetime) -> bool
def next_run(rec: Recurrence, after: datetime) -> Optional[datetime]
def runs_between(rec: Recurrence, start: datetime, end: datetime) -> list[datetime]
def describe(rec: Recurrence) -> str
def validate(rec: Recurrence) -> list[str]
```

`next_run` and `runs_between` are built by stepping minute by minute over
`fires_at`, bounded at 366 days. A year of minutes is half a million
evaluations in the pathological case of a recurrence that never fires, and
about 1,440 in the ordinary case of one that fires tomorrow morning. Choosing
the dumb implementation buys one code path for two modes, and a `describe()`
that cannot disagree with the schedule it describes.

Times are read in the recurrence's own zone through `core/zones.py`, exactly as
`core/schedule.py` already reads an alert's active hours — so a Windows display
name, an IANA id or a bare offset are all accepted, and DST is computed at the
date in question. `parse_hhmm` is reused rather than rewritten.

Two consequences of working in wall-clock time, both correct and both worth
stating so nobody reports them as bugs. On the morning a zone springs forward,
a job set for 02:30 does not fire — that minute does not exist locally. On the
morning it falls back, 01:30 occurs twice; the job fires on the first, because
the run is recorded against the instant, and the second is a fire time already
covered by `last_fired_at`.

`validate()` is what the page shows before saving, in the spirit of
`schedule.validate()`: a recurrence that silently matches nothing looks exactly
like a scheduler that is broken.

### 4.2 Cron

The advanced field takes the five standard fields — minute, hour, day of month,
month, day of week — supporting `*`, `n`, `a-b`, `a,b` and `*/n`. Day-of-week
accepts `0-6` with `7` as Sunday.

No dependency is added for this. `fires_at` needs only a field-matches-value
test, which is a few dozen lines; a cron *library* earns its keep by computing
next-fire times, and that is the part being answered by minute-stepping
instead. What the parser must do is reject what it cannot handle at save time
rather than at 07:00 — an unparseable expression is a validation error on the
page, never a silent no-op.

---

## 5. Running one job

`core/reportrun.py` exposes one function.

```python
def run_job(job: ReportJob, store, mgr, now: datetime,
            send: Callable = notifiers.send_report_email) -> RunOutcome
```

In order:

1. Load the dashboard. Missing, disabled or file-sourced → fail immediately
   with a message that says which.
2. Resolve the period: `job.period or dashboard.time_context`, through
   `timectx.resolve(spec, now.date())`.
3. Merge parameters: the dashboard's defaults, overlaid with `job.parameters`.
4. `run_datasets(dashboard, store, mgr, now.date(), chosen=chosen)`.
5. **If any dataset failed, the run failed.** No PDF is rendered and nothing is
   sent. See §5.1.
6. `dashboard_to_pdf_bytes(dashboard, results, rt, as_of=now, chosen=chosen)`.
7. Attach under `dashpdf.pdf_filename(dashboard, now)` and send.

```python
@dataclass
class DatasetFailure:
    name: str
    error: str
    qsql: str = ""          # the query that was sent, for the admin notice

@dataclass
class RunOutcome:
    ok: bool
    pages: int = 0
    bytes_sent: int = 0
    recipients: int = 0
    failures: list[DatasetFailure] = ...
    error: Optional[str] = None      # one-line summary of why
```

### 5.1 Any dataset failing fails the run

On screen, a dashboard with one broken panel is still useful: the reader sees
the error in place, knows which number is missing, and reads the rest with that
in mind. A PDF in an inbox is not read that way. It arrives detached from the
app, is forwarded on, and is quoted from days later, by which time nobody knows
that the panel showing zero was a dead connection rather than a quiet morning.
A report that is wrong and plausible is worse than one that did not arrive.

So the rule is strict: **one dataset in error means no report is sent.**
Datasets in the *waiting* state count as failures too — `DatasetResult.waiting`
distinguishes "no upload yet" from "broken" for the reader's benefit, but either
way the frame is absent and the page would print without it.

`run_datasets` is still allowed to run to completion rather than being stopped
at the first error — it captures failures rather than raising, which is what
makes this cheap. One notice then names *every* broken dataset, instead of the
admin fixing one at 07:05 and discovering the next at 07:00 tomorrow.

This is why the retries in §7.2 matter more than they first appear: the common
dataset failure is a server that has not finished coming up, and three attempts
two minutes apart is the difference between a report that is late by four
minutes and one that does not arrive.

`send` is a parameter so tests drive the whole path without a mail server, and
so the page's *Send now* button and the scheduler share one implementation
rather than two that agree today.

### 5.2 Why file dashboards are refused

`Dashboard.source` is `kdb` or `file`. A file dashboard reads from a
spreadsheet somebody uploads in the browser; at 07:00 there is no browser and
no upload, and `run_datasets` would correctly report every dataset as *waiting*.
The page therefore does not offer file dashboards when choosing one, and states
why, rather than accepting the job and mailing an empty page every morning.

---

## 6. Mail

`core/notifiers.py` keeps sole ownership of `smtplib` and gains:

```python
def send_report_email(smtp_host: str, smtp_port: int, sender: str,
                      to: list[str], subject: str, body: str,
                      attachments: list[tuple[str, bytes, str]]) -> None
```

Built on `email.message.EmailMessage` rather than the `MIMEText` the existing
`send_email` uses, because that one cannot carry an attachment. `send_email`
stays as it is; the alert path has no reason to change.

Settings are the ones Admin already stores — `smtp_host`, `smtp_port`,
`smtp_sender` — and no others. The relay is an open one on the LAN, so there is
no username, no password and no TLS negotiation, and adding fields for
credentials nobody will fill in would only invite someone to put a password in
a SQLite file. Should that change, the place it changes is this one function.

Admin gains a **Send test email** button beside the existing fields. Verifying a
relay by waiting for tomorrow's 07:00 is not a reasonable way to find out that
the port is wrong.

### 6.1 Failure notices go to the admin, and to nobody else

**The recipients are never told that a run failed.** They receive the report or
they receive nothing.

This follows from what the recipient list is for. Its members are traders and
risk staff who asked for a PDF at 07:00; a mail telling them that `open_orders`
returned `handle: connection refused` asks them to act on something they cannot
act on, and a list that sometimes carries reports and sometimes carries
apologies gets filtered. The people who can do something about a failed run are
the people who run KdbMonitor, and they are the only ones written to.

One setting in Admin, beside the SMTP fields:

```
scheduler_admin_email = "kdb-support@corp.local"       # comma-separated for several
```

A single setting rather than a field on each job: when the person carrying
support changes, that is one edit and not one per report. If a particular job
ever needs its own owner, this is where that field goes.

The notice is sent once per failed run — not once per attempt — after the
retries are exhausted, carries no attachment, and names in full:

```
Subject: [KdbMonitor] FAILED — Morning OMS report

Job:        Morning OMS report
Dashboard:  OMS Overview
Due:        2026-08-14 07:00 Europe/London
Attempts:   3
Recipients: 3 (not sent to)

2 of 5 datasets failed:

  open_orders   handle: connection refused
                select from orders where date=2026.08.13, ...

  fills         `type error
                select sum size by sym from fills where ...
```

The failing q text is included because the admin's next action is to run it, and
finding it otherwise means opening the dashboard and reconstructing which
substitutions produced it. It is safe to include *here* precisely because this
mail goes to one internal support address rather than to a distribution list.

**When no admin address is set,** a failure is silent by mail. That is a real
hole, so it is closed in the app rather than by guessing an address: Admin shows
a warning whenever report jobs exist and the field is empty, the Schedules page
marks the job failed, and the sidebar badge turns red — which is the only part
of this anybody sees without going looking.

---

## 7. The scheduler process

`python -m kdbmonitor.scheduler`, with `run_scheduler.bat` beside it for
starting by hand or dropping in Startup.

At startup it sets the matplotlib `Agg` backend before anything imports pyplot —
a headless process has no display, and `core/dashpdf.py` imports pyplot lazily,
which is what makes setting the backend at the entry point sufficient. It then
claims the lock (§8), and on failing to claim it prints who holds it and exits
with status 0. That is a normal outcome, not an error.

The loop, once per tick (default 15 s):

1. Write the heartbeat.
2. Re-read `scheduler_desired`; if `stopped`, release the lock and exit.
3. For each enabled job, ask `runs_between(rec, job.last_fired_at, now)` what
   it missed.
4. Run the most recent due fire time, if any, and record it.

A newly created job has `last_fired_at` set to its creation time rather than
left null, so it starts from the moment it was written down. A null would make
its first tick ask what it missed since the beginning of time.

Sleep is taken in one-second slices with step 2 between them, so a Stop takes
about a second rather than a tick. An in-flight run is allowed to finish first:
interrupting a report halfway is how a recipient gets half a report.

### 7.1 Missed runs and the grace window

Several missed fire times collapse into a single run. A report is a snapshot of
the present, not a queue of past ones — a scheduler down from Friday to Monday
should send Monday's report, not three of them.

A fire time older than `grace_minutes` (default 60) is not run at all. It is
recorded with status `missed`. The 07:00 report exists because somebody reads it
at 07:05; the same PDF arriving at 14:00, headed with a period the reader has to
work out for themselves, is worse than its absence — and its absence is visible
on the page, which is where a missed run belongs.

### 7.2 Retries

A failed run is retried up to `retries` times at `retry_delay_secs` apart,
within the same tick's work. Three attempts two minutes apart covers the case
this exists for: a KDB server still coming up at 07:00. It does not cover a
server that is down all morning, and it should not — that is what the failure
notice is for.

The wait between attempts is taken in the same one-second slices as the tick
sleep, checking `scheduler_desired` between them, or a Stop pressed during a
four-minute retry cycle would appear to hang. A Stop during that wait abandons
the remaining attempts and records the run as `failed`, noting how many were
made.

`attempts` is recorded on the run so the page can distinguish *worked first
time* from *worked on the third go*, which is the difference between a healthy
job and one about to start failing.

### 7.3 Logging

A rotating file at `logs/scheduler.log` (5 files × 1 MB), and stdout. The
directory is `logs/` under the working directory, overridable with
`KDBMONITOR_LOG_DIR` for the case where the app is run from a read-only or
shared location. The file is not optional: a detached process has no console to
write to, and without it the answer to "why did nothing arrive" is unavailable.
The page shows the resolved path.

---

## 8. One scheduler, and only one

Two schedulers on one database means every recipient gets every report twice.
That is the failure this section exists to make impossible, and it has to hold
against two browser tabs loading at once, a UI restarted while the scheduler is
already running, and somebody starting one by hand.

The mechanism is a lock in the existing `settings` table, held by whoever is
running and refreshed while they run.

```
scheduler_lock = "2026-08-13T06:59:12Z|8412|THISBOX"
```

The value leads with an ISO-8601 UTC timestamp, so *stale* is a lexicographic
comparison and the claim is a single conditional statement — atomic in SQLite,
needing no JSON extension and no second table:

```sql
INSERT INTO settings(key, value) VALUES ('scheduler_lock', :mine)
ON CONFLICT(key) DO UPDATE SET value = :mine
 WHERE substr(settings.value, 1, 20) < :cutoff
```

`changes()` says whether the claim was won. A process that loses exits. The
heartbeat is the same statement without the `WHERE`, run each tick; staleness is
60 s against a 15 s beat, which tolerates three missed beats before another
process may take over.

`scheduler/supervisor.py` owns all of it:

| Function | Called by | Does |
| --- | --- | --- |
| `claim(store, now)` | scheduler, at startup | The statement above. True if we own it. |
| `heartbeat(store, now)` | scheduler, each tick | Refreshes it. |
| `release(store)` | scheduler, on clean exit | Clears it, so a restart is instant rather than waiting out 60 s. |
| `status(store, now)` | UI | `running` / `stale` / `stopped`, with pid, host and beat age. |
| `ensure_running(store, spawn=...)` | `app.py` | Spawns when it should be running and is not. |

A crash needs no special handling: the lock goes stale on its own, and the next
thing that looks reconciles it.

---

## 9. Start, stop, restart

One setting is the intent, and both processes reconcile to it:

```
scheduler_desired = "running" | "stopped"        (default "running")
```

- The **scheduler** re-reads it between sleep slices. On `stopped`, it releases
  the lock and exits.
- The **UI** spawns when `desired == "running"` and the lock is not fresh.

Making the UI's spawn rule depend on the desired state is what makes Stop
actually stop: the naive version, "spawn whenever nothing is running", would
respawn the process the moment after you stopped it. It also collapses autostart
and the Start button into the same rule rather than two features that must be
kept consistent.

**Restart** sets `stopped`, waits for the lock to clear, sets `running`, and
spawns. It is on its own button because it is what you need after changing the
SMTP host or pulling new code, and doing it as two clicks invites doing it as
one.

**Stopping is cooperative, never a kill.** Windows has no `SIGTERM` to send
another process, and killing by recorded pid risks killing whatever unrelated
process has since inherited that pid. Writing an intent the owner reads has
neither problem and needs no privileges.

**When it does not land:** if the lock has not cleared within 10 seconds the
process is wedged, and a Stop button that shrugs is worse than no button. The
page then reports `not responding (pid 8412 on THISBOX)` and offers a **Force
stop**, which appears only in that case and only when the recorded host is this
machine — `taskkill` cannot reach another box, and offering a button that
cannot work is a way of lying about what happened.

### 9.1 Spawning

```python
subprocess.Popen([sys.executable, "-m", "kdbmonitor.scheduler"],
                 creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,  # Windows
                 start_new_session=True,                                     # POSIX
                 stdout=log, stderr=log, close_fds=True,
                 env={**os.environ, "KDBMONITOR_DB": DB_PATH})
```

Detached, so closing the terminal that ran Streamlit does not take the
scheduler with it. `KDBMONITOR_DB` is passed explicitly because the UI already
reads it from the environment and the child must open *that* database, not the
default relative path from whatever directory it inherits.

`app.py` calls `ensure_running` from a `@st.cache_resource` function beside the
existing `get_store()` and `get_manager()`. That decorator runs its body once
per Streamlit server process, which is exactly "when the UI launches" — not on
every rerun, and not once per browser session.

`KDBMONITOR_NO_AUTOSTART=1` suppresses the spawn without touching
`scheduler_desired`, for when the process is managed by Task Scheduler or a
service. It is an environment variable rather than a setting because it
describes the deployment, not the intent.

A spawn that raises — wrong interpreter, an import error, a missing package —
is caught and surfaced in the sidebar badge as `failed`, with the reason. A
scheduler that silently declines to start is the worst available outcome,
because everything looks normal until a report does not arrive.

---

## 10. Storage

Two tables, created in `init_db()` and evolved through the existing additive
`_migrate()`.

```sql
CREATE TABLE IF NOT EXISTS report_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    dashboard_id INTEGER NOT NULL,
    spec_json    TEXT NOT NULL DEFAULT '{}',   -- recurrence, period, parameters,
                                               -- recipients, subject, body, retries
    last_fired_at TEXT,                        -- ISO UTC, the fire time, not the run time
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_job_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL,
    fired_for   TEXT NOT NULL,       -- the scheduled instant
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,       -- ok | failed | missed
    attempts    INTEGER NOT NULL DEFAULT 1,
    pages       INTEGER,
    recipients  INTEGER,
    error       TEXT,                -- one-line summary
    detail_json TEXT                 -- [{name, error, qsql}, ...] per failed dataset
);
CREATE INDEX IF NOT EXISTS ix_report_job_runs_job ON report_job_runs (job_id, fired_for DESC);
```

Everything variable about a job lives in `spec_json`, following how `alerts` and
`dashboards` already store their payloads. `dashboard_id` is a column rather
than a JSON field because it is the one thing that must be queryable — the page
warns about jobs pointing at a deleted dashboard.

`last_fired_at` records the **fire time**, not the moment the run happened. It
is what `runs_between` is asked from, so recording the wall-clock end of a slow
run would silently skip the next fire time when a run overran.

Run history is trimmed to the most recent 200 rows per job on insert, in the
spirit of the existing `alert_runs` retention.

### 10.1 WAL

`Storage.__init__` currently opens SQLite in the default rollback-journal mode,
under which a second writing process meets `database is locked`. Two processes
is the entire premise here, so `__init__` gains:

```python
self.conn.execute("PRAGMA journal_mode=WAL")
self.conn.execute("PRAGMA busy_timeout=5000")
```

This is a change to existing shared code, made because this feature requires it
and not as incidental tidying. WAL permits one writer with concurrent readers,
which is exactly the shape of the traffic: the scheduler writes a heartbeat and
a run row, the UI mostly reads. The 5-second busy timeout covers the moments
they collide.

---

## 11. The page

A new **Schedules** entry in `st.navigation`, between Dashboards and Admin.

**Top — the process.**

```
Scheduler   ● running   pid 8412 on THISBOX   beat 4s ago
            [Stop]  [Restart]                 log: logs/scheduler.log
```

`● stopped` offers `[Start]` alone. `● not responding` adds `[Force stop]`.
The sidebar carries the same state as a compact badge beside the existing
monitoring one, so it is visible from every page — that badge is where somebody
will notice, since nobody visits this page on a normal morning.

**Below — the jobs.** One expander each:

```
✓  Morning OMS report        07:00 Mon–Fri (Europe/London)     next: Thu 14 Aug 07:00
   last run  ✓ 13 Aug 07:00 · 2 pages · 3 recipients
✗  Risk EOD                  16:35 Mon–Fri (Europe/London)     next: Thu 14 Aug 16:35
   last run  ✗ 12 Aug 16:35 · 2 datasets failed · 3 attempts · admin notified
```

Opened, a job edits: name, dashboard (kdb-sourced only), recurrence (times +
days + zone, with cron behind an *Advanced* toggle), period override, parameter
values drawn from that dashboard's declared parameters, recipients, subject and
body. Validation problems print before the save button, as the Builder does with
alert schedules.

Two actions per job beyond save and delete: **Send now**, which calls
`run_job` in the Streamlit process and reports what happened without touching
`last_fired_at`; and **History**, the last runs from `report_job_runs`.

A failed run opens to the same detail the admin was mailed — every failed
dataset with its error and the query that produced it, read back from
`detail_json`. Deliberately the same content from the same source: an admin
mailed at 07:00 and an admin who comes looking at 09:00 must not be given two
different accounts of one failure. *Send now* is the natural next step from that
screen once the cause is fixed.

*Send now* is worth the small cost of building. Without it, the first honest
test of a new job is tomorrow at 07:00, and it either works or produces a
failure nobody sees until they look.

---

## 12. Testing

Everything below runs without a KDB server, a mail server, a clock, or a
spawned process. `core/mock.py` already provides `MockKdbClient` and
`MockHdbClient`, which `ConnectionManager` serves for any connection whose host
is `demo`.

**Recurrence** — pure, and the largest body of tests. Times and days match the
right minutes and no others; an empty day list means every day; a zone is read
through `zones.py`; `next_run` crosses midnight, month end and year end; a
spring-forward 02:30 does not fire and a fall-back 01:30 fires once;
`runs_between` returns every fire time in a gap, in order; cron parses `*`,
`n`, `a-b`, `a,b`, `*/n`, treats day-of-week `7` as Sunday, and rejects
malformed input through `validate()` rather than at match time.

**Running a job** — against a demo connection, `run_job` returns `ok` with a
page count and bytes beginning `%PDF`. A job overriding the period produces a
different `ResolvedTime` from the dashboard's saved one; a parameter override
reaches `run_datasets` as `chosen`, and an absent one falls back to the
dashboard's default. A file-sourced dashboard is refused with a message naming
the reason.

**Failing a run** — the rule in §5.1 gets its own tests, since it is the one
place a wrong answer reaches a recipient. One dataset of five failing gives
`ok=False`, names that dataset in `failures` with its error and query, and —
asserted explicitly — **calls `send` zero times**. Two failing are both listed,
so the run is not abandoned at the first. A dataset in the `waiting` state fails
the run in the same way as one in error. A run where every dataset succeeds
still sends exactly once.

**Mail** — `smtplib.SMTP` monkeypatched. Recipients, sender and subject are as
configured; placeholders in the subject are substituted and an unknown one is
left intact; the attachment carries the `pdf_filename()` name and
`application/pdf`. The failure notice goes to `scheduler_admin_email` alone —
asserted against the recipient list to show that no address from it appears —
carries no attachment, names every failed dataset with its query, and is sent
once for a run of three attempts rather than three times. With
`scheduler_admin_email` empty, no mail is sent at all and the run is still
recorded `failed`. Empty `smtp_host` fails the run with a message that says SMTP
is not configured, rather than raising.

**The loop** — with an injected clock and an injected `run_job`. A job due now
runs once and only once; a second tick in the same minute does not run it
again; a fire time inside the grace window runs; one outside is recorded
`missed` and not run; three missed fire times produce one run; a failing job is
retried to its limit and then records `failed` and sends one notice; a job that
fails then succeeds records `ok` with `attempts=2`; a disabled job never runs;
`scheduler_desired = stopped` exits the loop and releases the lock.

**The lock** — two `claim()` calls against one store: the first wins, the second
loses. A lock older than the staleness window can be claimed; a fresh one cannot.
`release()` frees it. `status()` reports pid, host and beat age.

**Autostart** — `ensure_running` with an injected spawn: called when desired is
`running` and the lock is stale; not called when the lock is fresh; not called
when desired is `stopped`; not called under `KDBMONITOR_NO_AUTOSTART`; a spawn
raising is reported as `failed` rather than propagating.

**The page** — a render smoke test beside `tests/test_all_pages_render.py`, with
no jobs, with jobs, and with the scheduler in each of its states.

---

## 13. Consequences worth stating

- **A failed report is silent to the people expecting it.** By design (§6.1):
  its absence is the only signal they get, and if they do not notice the absence,
  nobody on the desk notices at all. This is the right trade for a report that
  would otherwise be wrong, but it puts the whole burden of noticing on the admin
  address being set and read — which is why Admin warns when it is empty.
- **One broken dataset stops the whole report,** including the panels that
  worked. A dashboard carrying one flaky optional dataset alongside four
  dependable ones will fail every morning that dataset does. The fix is to split
  it into two dashboards rather than to weaken the rule.
- **Reports run against live connections at odd hours.** A job at 07:00 queries
  whatever the servers hold at 07:00. Nothing here waits for a feed to be ready;
  a report that must not run before a load completes should be scheduled after
  it.
- **The scheduler holds its own `ConnectionManager`,** so its pykx handles are
  separate from the UI's. Two processes will hold connections to the same
  servers.
- **A shared database over a network share means one scheduler between all of
  them.** That is the correct behaviour — the alternative is duplicate mail —
  but the machine that happens to hold the lock is the one running the reports,
  and the page names it.
- **Rendering is not instant.** A dashboard with several long tables takes
  seconds to a minute. Fire times a minute apart on the same job will overlap in
  intent; the loop runs jobs one at a time, in declaration order, and a slow job
  delays the ones behind it. At the scale this is built for — a handful of
  reports a day — that is simpler than concurrency and easier to reason about
  when something is late.
