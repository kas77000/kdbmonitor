# Report archive — design

**Status:** proposed.
**Date:** 2026-08-20

---

## 1. The problem

A scheduled report goes to an inbox and nowhere else. That is fine for the
person who reads it at 07:05 and forgets it, and wrong for everybody else: the
desk that wants last quarter's PDFs in one place, the auditor who asks what was
circulated on the 14th, the analyst who wants to open six mornings side by side.
Today the only copy of a report lives in whatever mailboxes it landed in, which
means the archive is however good the recipients' filing is.

So: let a job also drop its PDF in a folder. The mail still goes — this adds a
destination, it does not replace one.

---

## 2. What this is and is not

In scope:

- One archive root folder, set in Admin, empty meaning the feature is off.
- A per-job switch, off by default, saying whether that job also writes.
- A folder per dashboard under the root, created on demand.
- A filename carrying the dashboard and the run's UTC timestamp to the second.
- The write happening **before** the mail, so a broken folder cannot produce
  duplicate emails through the retry loop.
- The written path recorded on the run and shown on the page.

Deliberately out:

- **A per-job path.** The admin sets where reports land; a job says yes or no.
  A job that could write anywhere would defeat the point of the setting being
  in Admin.
- **Archive without email.** Recipients stay mandatory. This is an additional
  copy, not an alternative delivery.
- **Retention or pruning.** The folder is somebody's share and they own what is
  in it. `Result snapshots` prunes rows in our own database; a network drive is
  not ours to delete from.
- **Following a dashboard rename.** Renaming starts a new folder and leaves the
  old files alone. Moving files on a share behind the user's back is worse than
  two folders.
- **Formats other than PDF.** The same bytes the email attaches.

---

## 3. Layout on disk

Admin gives one root. Everything below it is the process's business:

```
\\nas\reports\kdbmonitor\          ← the admin's path, must already exist
├── equity_orders\                 ← created on demand, per dashboard
│   ├── equity_orders_20260819_070000.pdf
│   └── equity_orders_20260820_070000.pdf
└── short_sell_monitor\
    └── short_sell_monitor_20260820_071500.pdf
```

Three consequences, each deliberate:

**The root is never created; the dashboard folder always is.** A missing root is
a typo in Admin and has to say so — silently creating `\\nas\reprots\` hides the
mistake until somebody goes looking for six weeks of reports. A missing
dashboard folder is just the first run for that dashboard, so `makedirs` it.

**Seconds in the name mean no collisions.** Two runs in the same second are not
reachable, so every run is its own file and nothing is ever overwritten. The
overwrite question does not arise.

**The folder is named from the dashboard's slug**, through the same slugify
`pdf_filename` already applies.

The timestamp is the run's fire time in UTC, matching everything else the
scheduler records.

The emailed attachment keeps its existing name — `equity_orders_2026-08-20_0700.pdf`,
dashes and no seconds. The two names differ on purpose: inbox filters are
written against the attachment name and changing it would break them for a
feature that has nothing to do with mail.

---

## 4. The module

A new `kdbmonitor/core/reportfile.py`, knowing nothing about jobs, stores or
Streamlit:

```python
ARCHIVE_DIR_KEY = "report_archive_dir"

def archive_filename(dashboard_name: str, when: datetime) -> str
    # equity_orders_20260820_070000.pdf

def check_root(path: str) -> Optional[str]
    # None when usable; otherwise the reason, in the words the page prints

def write_report(root: str, dashboard_name: str, when: datetime,
                 pdf: bytes) -> str
    # makedirs the dashboard folder, write, return the full path written
```

`check_root` is what earns the module a file of its own. Admin calls it to
validate the field and disable Save, `run_job` calls it before rendering, and
the tests call it against `tmp_path` with no job in sight. One definition of
"is this folder usable", three callers. Keeping it in `reportrun` would mean
writing it twice, and testing the write would mean constructing a job, a store
and a manager to reach it.

Its refusals, each a sentence a reader can act on:

| condition | message |
|---|---|
| blank | `no folder is set` |
| does not exist | `that folder does not exist` |
| exists, not a directory | `that path is a file, not a folder` |
| not writable | `no permission to write there` |

---

## 5. The job field

`ReportJob` gains one field beside `recipients`:

```python
archive: bool = False        # also write the PDF to the report archive
```

`job_from_dict` reads it with that default, so every job already in
`report_jobs.spec_json` loads unchanged with the feature off. No migration for
the jobs themselves.

`validate_job` grows one keyword:

```python
def validate_job(job: ReportJob, *, archive_root: str = "") -> list[str]:
    ...
    if job.archive and not archive_root.strip():
        problems.append("Turn off saving a copy, or set a report archive "
                        "folder in Admin > Report archive.")
```

A job whose archive is on, in an app with no archive folder, genuinely is
invalid — so this belongs in `validate_job` rather than bolted onto the page,
and the page gets its Save disabled through the mechanism already there.
`schedules.py` is the only production caller and passes the stored root.

---

## 6. The run

`run_job` gains a pre-flight beside the existing SMTP check, and a write just
before `send`:

```python
if job.archive:
    root = (store.get_setting(reportfile.ARCHIVE_DIR_KEY, "") or "").strip()
    problem = reportfile.check_root(root)          # before any querying
    if problem:
        return RunOutcome(ok=False,
                          error=f"the report archive is unusable: {problem}")
...
archived = reportfile.write_report(root, dashboard.name, now, pdf)
send(...)
```

Two orderings matter and both are load-bearing:

**The root is checked before the datasets run.** A broken share costs a failure
notice, not thirty seconds of KDB time followed by a failure notice.

**The write happens before `send`.** `_attempt` in the scheduler loop retries a
failed run up to `job.retries` times. If the mail went first and the write
failed the run, every retry would send the report again — two and three copies
of the same PDF to the same desk. Writing first means a failed write has sent
nothing, so the retry is safe and needs no special case. The cost is that a full
disk stops a report that could have been emailed, which is the same trade
`reportrun` already makes for a failed dataset: absent beats half-right.

`RunOutcome` gains `archived_path: Optional[str] = None`.

---

## 7. Where a written file shows up

`report_job_runs` gains an `archived_path TEXT` column through `_migrate`, the
same `ALTER TABLE` pattern `storage.py` already uses. `record_report_run` takes
it as a keyword.

That column pays for itself three times:

- **Send now** — `Sent 3 pages to 2 recipients. Saved to \\nas\reports\kdbmonitor\equity_orders\equity_orders_20260820_143012.pdf`
- **History tab** — the run line gains `· archived` and a caption with the path,
  so "where did Tuesday's go" is answered on the page rather than by browsing a
  share.
- **Admin failure notice** — needs no change. An unusable archive comes back as
  `outcome.error`, which `failure_notice` already prints.

---

## 8. The pages

### Admin — a new "Report archive" section

Directly under *Admin notifications*; both are about scheduled reports.

- One text input, **"Folder for scheduled report PDFs"**, placeholder
  `\\nas\reports\kdbmonitor`. Empty means off everywhere.
- Feedback printed back under it, the way the admin-email field prints its
  parsed addresses: `→ writable` or the `check_root` reason.
- **Save settings**, disabled while the path is bad, mirroring the sections
  around it.
- **Test write** beside it — writes and deletes a probe file, the same idea as
  *Send test email*. Verifying a share by waiting for tomorrow's 07:00 is what
  that button exists to prevent.
- A warning when jobs have the switch on and this field is empty, parallel to
  the existing "scheduled reports but no admin address" warning.

### Schedules — the job editor

In *To whom*, under Recipients:

- A switch, **"Also save a copy to the report archive"**, off by default.
- When on, a caption showing the destination —
  `→ \\nas\reports\kdbmonitor\equity_orders\equity_orders_20260820_070000.pdf`
  — so it is visible before the first run rather than after.
- When on with no root configured, the `validate_job` warning appears and Save
  is disabled.

**Send now** runs the real path, write included, and says where the file went.
That makes it the honest test of the folder and its permissions before the first
scheduled run, which is the reason the button exists.

---

## 9. Testing

`tests/test_reportfile.py` — the new module against `tmp_path`: the name shape,
the dashboard folder created on demand, the root *not* created, and each of
`check_root`'s four refusals.

`tests/test_reportrun.py` — the wiring: archive off writes nothing; archive on
writes the PDF and returns its path; a bad root fails the run without calling
`send` and without running a dataset.

The ordering test is the one that matters most: a fake `send` that records
whether the file already existed when it was called. Retry-safety rests entirely
on that property, and it is the one that would rot silently.

`tests/test_reportjob.py` — the new validation rule, and a job dict without the
key still loading.

`tests/test_report_storage.py` — `archived_path` round-trips, and a database
created before this migrates.

`tests/test_schedules_page.py` and `tests/test_admin_render.py` — the switch,
the disabled Save, and the new Admin section rendering.
