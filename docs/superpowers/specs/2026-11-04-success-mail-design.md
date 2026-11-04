# All-clear mail — design

**Status:** design agreed, built in the same change.
**Date:** 2026-11-04

---

## 1. The problem

An alert mails the desk when it triggers. When it does not, the inbox is silent,
and silence means two different things: *nothing is wrong*, or *the check is not
running*. People who rely on an alert want to hear the first one said out loud.

So an alert can also send a **success mail** ("all clear") when it did not
trigger, either:

- **with every check** — each clean check sends one, or
- **on its own interval** — every 15 min / 30 min / 1 h / 2 h / 4 h / 1 day,
  for alerts that check too often for a mail per check to be bearable.

The subject and body can be written by the user; if left blank a default mail
is sent.

## 2. Decisions

| Question | Answer |
|---|---|
| What does an interval mail certify? | **All clear since the last one**: sent only if every check in the period was clean. One trigger or error in the period and no mail goes out for it. |
| How is the separate timing set? | One interval, from a short list of presets. |
| Who receives it? | The alert's own email recipients. It can only be turned on when Email delivery is picked. |
| Placeholders? | Yes: `{alert}` `{group}` `{checks}` `{since}` `{until}` `{rows}`. An unknown `{name}` is left exactly as typed. |

## 3. What "clean" means

A check is clean when the trigger condition was **not met** and the check did
not error. Not the same as `status == "armed"`: with the `on_change` re-arm a
repeat of the same triggering result is recorded as armed (it does not re-fire)
although the condition still holds — that is not an all-clear.
`EvalResult` therefore gains `condition_met`, and the decision uses that.

Off-hours time has no checks. A period with no checks at all sends nothing:
there is no evidence of anything.

## 4. Model

`core/models.py`:

```python
SUCCESS_MAIL_MODES = ("every_check", "interval")

@dataclass
class SuccessMail:
    enabled: bool = False
    mode: str = "interval"
    interval_secs: int = 3600
    subject: str = ""        # "" = the default subject
    body: str = ""           # "" = the default body
```

`Alert.success: SuccessMail`. `success_mail_from_dict` reads anything older (or
hand-edited) as "off", so upgrading or importing an existing alert mails nobody
new. It travels through export/import with the rest of the alert JSON.

## 5. The period (`core/successmail.py`, pure)

State per alert: `period_start` (UTC), `clean_checks` (int), `dirty` (bool).

`step(cfg, state, now, clean) -> (new_state, Optional[Period])` — called once
per evaluated check, after the check has run:

- **every_check:** a clean check returns a period of exactly that check
  (`checks=1`, `since == until == now`). State is not needed.
- **interval:**
  1. No state yet → open a period at `now`.
  2. Count the check: `clean_checks += 1` if clean, else `dirty = True`.
  3. If `now - period_start >= interval`: the period closes. It yields a mail
     iff `clean_checks > 0 and not dirty`. A new period opens at `now`
     regardless — the cadence is fixed, so an hour that saw a trigger simply
     has no mail and the next hour starts clean.

An interval shorter than the poll interval behaves like every check (each check
closes the period); the Builder says so rather than refusing it.

`render(cfg, alert, period, rows, tz) -> (subject, body)` fills the
placeholders, with `since` / `until` written in the alert's schedule zone as
`YYYY-MM-DD HH:MM`. Defaults:

- Subject: `KdbMonitor: {alert} all clear`
- Body (interval): `{alert} did not trigger between {since} and {until}.`
  `Clean checks: {checks}. Rows in the last check: {rows}.`
- Body (every check): `{alert} did not trigger at {until}.` `Rows returned: {rows}.`

Filling uses `str.format_map` with a dict that returns `{name}` for missing
keys; a stray `{` or `}` that breaks formatting leaves the text unfilled rather
than failing the send.

## 6. Storage

New table, created by `init_db` (so existing databases get it too):

```sql
CREATE TABLE IF NOT EXISTS alert_success_state (
    alert_id     INTEGER PRIMARY KEY,
    period_start TEXT NOT NULL,
    clean_checks INTEGER NOT NULL DEFAULT 0,
    dirty        INTEGER NOT NULL DEFAULT 0
);
```

`get_success_state`, `set_success_state`, `clear_success_state`. The state is
cleared when the alert is deleted, and when the alert is saved with success mail
off — so turning it back on starts a fresh period.

## 7. Engine

`successmail.after_check(store, alert, now, clean, rows, send)` holds the glue,
in core so it is tested without Streamlit; `ui/engine.py:run_tick` calls it after
each evaluation is recorded:

```
if alert.success.enabled and alert.channels.email_to:
    state, period = step(...)
    store.set_success_state(...)
    if period and send is not None (SMTP configured):
        send; on failure return the error — the engine shows it in-app
```

The period rolls over whether or not the send worked, so a broken SMTP server
costs one message per period rather than a retry every tick.

## 8. Builder

Inside **Notify & re-arm**, once Email is picked:

- checkbox **Also mail when all clear**
- **When**: *With every check*, or every 15 min / 30 min / 1 h / 2 h / 4 h / 1 day
- **Subject** (text input) and **Body** (text area), with the defaults as
  placeholders; blank sends the default
- caption listing the placeholders, and a note when the interval is not longer
  than the alert's own check interval

The notify badge and the Monitor's one-line summary add
`all-clear mail hourly` / `all-clear mail every check`.

## 9. Tests

- `tests/test_success_mail.py`: the period logic (clean period mails; a trigger
  or error suppresses; no checks sends nothing; every-check mode; on_change
  repeat is not clean), placeholder rendering, defaults, unknown and broken
  placeholders, model round trip and legacy alerts, storage, and a summary line.
- `after_check` against a real `Storage` with a fake mailer: an hour of clean
  checks mails once, a failing mailer returns its error and does not raise.
- A Builder AppTest that the controls produce the configured `SuccessMail`.
