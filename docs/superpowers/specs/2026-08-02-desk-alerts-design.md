# Desk Alerts — design

**Status:** design agreed, not built.
**Date:** 2026-08-02

---

## 1. The problem

KdbMonitor watches the algo KDB databases and knows, before anyone else does,
when something on the trading platform has gone wrong. Today that knowledge
stops at whoever happens to have KdbMonitor open. The people who need it are
sales traders on a coverage desk, and they are not looking at KdbMonitor —
they are looking at several trading platform windows at once.

So: put something on the trader's machine that tells them, in the moment,
what has just gone wrong with a name they cover.

Three constraints shape everything below, all of them from the desk rather
than from the code.

**Their eyes are everywhere.** An alert competes with several platform windows
for attention. It has to be caught by peripheral vision, in a place the eye
already knows, and understood without being studied.

**Their time is the scarce resource.** Reading an alert must cost a glance.
Not a sentence, not a click to expand, not a decision about whether it matters.

**There will be a lot of alerts.** Not five a day. This is the constraint that
drives most of the design, and it is covered on its own in §7. At volume the
difficulty is no longer getting an alert onto the screen. It is stopping the
screen from becoming wallpaper.

---

## 2. What this is and is not

The trader-side application — **DeskAlert** below — computes nothing. It holds
no connection to KDB, evaluates no condition, knows no thresholds. KdbMonitor
decides that something happened and sends an event; DeskAlert shows it and
sends back an acknowledgement. That is the entire contract, and keeping it
that narrow is what makes the thing safe to put on a trading desktop.

Deliberately out of scope, and to stay that way:

- Authoring or editing alerts from the trader machine.
- Any two-way messaging, chat, or trader-to-trader features.
- Mobile or out-of-building delivery.
- Showing result rows, tables, or charts on the trader machine. An alert is a
  headline. If somebody needs the rows, the rows are in KdbMonitor.
- Acting on the trading platform — no order cancellation, no click-through
  into the platform, nothing that touches a live order.

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  kdbmonitor-daemon           always on, one per environment      │
│  ├─ evaluation tick          every N seconds, no browser needed  │
│  └─ relay                    SSE out · heartbeats and acks in    │
└───────────────┬──────────────────────────────────┬───────────────┘
                │ SQLite (WAL)                     │ HTTPS, outbound
                │                                  │ only
┌───────────────┴───────────────┐   ┌──────────────┴───────────────┐
│  KdbMonitor (Streamlit)       │   │  DeskAlert  (trader machine) │
│  alerts · dashboards          │   │  strip · cards · tray        │
│  recipients · subscriptions   │   │  one exe, no config          │
│  agent registry               │   │                              │
│  can be closed at any time    │   │  N instances                 │
└───────────────────────────────┘   └──────────────────────────────┘
```

Three processes. The daemon is the only one that must always be running. The
Streamlit app becomes what it should always have been — a place to configure
things and look at things — and closing it stops nothing.

---

## 4. Part zero: getting the engine out of the browser

This is a prerequisite, not a feature, and nothing else in this document can
be trusted until it is done.

Today the evaluation loop is a Streamlit fragment in `app.py`:

```python
@st.fragment(run_every=_tick if _mon_on else None)
def _background_monitor():
    engine.run_tick(store, mgr)
```

`st.fragment(run_every=...)` is driven by the browser's websocket session.
There is no thread, no scheduler, no service, and no entry point other than
`streamlit run app.py`. The README says so plainly: *"Nothing runs in the
background."*

For a tool somebody sits in front of, that is a reasonable design. For one
wired to a trading floor it is not: the last person to close their laptop
would silently stop alerting for everybody, and the failure would be invisible
until somebody asked why they never heard about an outage.

**The work:**

- A new console entry point, `kdbmonitor-daemon`, registered under
  `[project.scripts]` in `pyproject.toml`. It is the only thing that has to
  survive a logout.
- `engine.run_tick` splits. The evaluation and dispatch half moves into
  `core/` where it can be unit-tested; the browser-only half — `st.toast`, the
  JS notification payload, focus stealing — stays in `ui/`. The project rule
  that logic belongs in `core/` already asked for this; the daemon just makes
  it unavoidable.
- SQLite moves to WAL journal mode, since the daemon and the Streamlit app now
  write to the same file concurrently.
- The Streamlit fragment stays, but only as a display refresh. It must not be
  able to evaluate an alert. Two processes both firing the same alert is the
  worst outcome available here, and the daemon holds a lock in the `settings`
  table with a lease so that a second evaluator refuses to start rather than
  double-firing.

**Reused unchanged:** the whole existing evaluation path — `schedule.is_active`,
`is_due`, `evaluate_alert`, `store.record_run`, `store.save_result` — and the
existing `Channels` model. The trader agent becomes one more delivery kind
alongside `in_app`, `sound`, `browser`, `focus`, `popup`, rather than a special
case bolted to the side.

---

## 5. The event model

An **event** is one thing that happened, addressed to people. It is distinct
from an `alert_runs` row, which records that a check ran. A hundred checks can
produce one event; one check can produce none.

Each event carries:

| Field | Meaning |
|---|---|
| `alert_id` | which alert produced it |
| `severity` | `red` · `amber` · `grey` (see §6) |
| `subject` | the thing it is about — for a coverage desk, the sym |
| `dedupe_key` | `alert_id` + `subject`, the identity used to coalesce |
| `headline` | the one line the trader reads, already rendered |
| `detail` | up to three short lines, shown only on hover or in the panel |
| `created_at` | when it happened |
| `expires_at` | when it stops being worth showing (§5.3) |

### 5.1 The headline

Fixed grammar, always in this order:

```
SUBJECT · WHAT HAPPENED · AGE · COUNT
RIL.IB    7 orders stuck NEW    4m    ×7
```

The subject comes first because a coverage trader thinks in names. Their eye
should land on `RIL.IB` and decide whether to care before it reads a word of
the rest.

The alert designer writes the middle part as a template using the substitution
idiom the app already has — `{{step1.sym}}`, `{{count}}` — and picks which
column is the subject. The subject choice does double duty: it is what the eye
reads first, and it is what events are grouped by.

**Headlines are capped at 60 characters, enforced in the builder with a live
preview of the exact card the trader will see.** If a condition cannot be said
in sixty characters it is not an alert, it is a report, and reports belong in
KdbMonitor where there is room for them.

### 5.2 Severity

Three tiers. Not four, and never a fifth.

| | Meaning | Card | Sound | Persists |
|---|---|---|---|---|
| **red** | act now | yes | yes | until acknowledged |
| **amber** | look soon | yes | no | until it expires |
| **grey** | for the record | no | no | strip counter and history only |

Colour carries severity and nothing else — never category, never desk, never
instrument. A trader must be able to learn three colours once and never think
about it again.

Grey is the tier that makes volume survivable, and most alerts should be grey.
An alert nobody would interrupt their morning for is grey by definition.

### 5.3 Age and expiry

Every event has a time to live, set per alert, defaulting to five minutes.

A twenty-minute-old *"7 orders stuck in NEW"* is not information, it is
archaeology — the orders have since filled or been pulled, and showing it wastes
exactly the attention this design exists to protect. So an expired event leaves
the screen. It stays in DeskAlert's history panel and in KdbMonitor, where
looking backwards is the point.

Red events are the exception: they persist until acknowledged, however old they
get, because an unacknowledged red is precisely the thing that must not quietly
disappear.

### 5.4 Coalescing

This is the single most important mechanism in the design.

Events sharing a `dedupe_key` do not stack. The existing card stays where it
is, its count badge increments, and its age resets:

```
▌ RIL.IB     7 orders stuck NEW      4m   ×7
```

The card does not move, flash, or re-sound. A card that jumps every time its
underlying condition re-fires is a card the eye has to re-find, which is the
opposite of what the fixed-position design is for.

---

## 6. What the trader sees

### 6.1 The strip

A frameless, always-on-top bar roughly 28 pixels high, docked to one edge of a
monitor the trader chooses once. It is always there and never moves.

```
▌2  ●14   RIL.IB  7 orders stuck NEW              ● 10:42
 ▲   ▲    ▲                                       ▲
 red total  most severe live event            connection
```

Permanence is the point. A Windows toast is the wrong answer for this: it
appears in a corner that every trader has spent years learning to ignore, and
then it vanishes whether or not anybody looked. A strip that has been in the
same place all morning gets noticed when its colour changes, without being
looked at.

### 6.2 The cards

Red and amber events also raise a card, stacked in one fixed corner:

```
┌───────────────────────────────────────────────┐
│▌ RIL.IB      7 orders stuck NEW      4m  ×7  ▪│  red
├───────────────────────────────────────────────┤
│▌ INFY.IB     fill rate 12% (avg 60%) 1m      ▪│  amber
├───────────────────────────────────────────────┤
│  +14 more                                     │
└───────────────────────────────────────────────┘
```

The stack is bounded — five cards, then a collapsed count. An unbounded stack
covers the screen during exactly the incident where the screen matters most.

Clicking a card copies its subject to the clipboard, ready to paste into a
platform window. Cheap to build, and it removes a retype from every single
alert a trader acts on.

Clicking the strip opens the full panel: everything live, plus history,
filterable by severity and by subject.

### 6.3 The rules that matter

**Never steal focus.** The window is created `WS_EX_NOACTIVATE |
WS_EX_TOOLWINDOW`. A trader mid-keystroke in a platform window must never have
a character swallowed by an alert appearing. This is the one defect that would
get the tool uninstalled on its first day, and it is worth a dedicated test on
every release.

**Sound is scarce.** Red only, one short distinct tone, never the Windows
default. Rate-limited to one sound every twenty seconds no matter how many
events arrive, and never more than one per alert per minute. On a floor, if
everything beeps then nothing does.

**Say when you are broken.** The strip carries a connection dot: green when the
relay is answering, red when it is not, with the time of the last contact.
Without it, *"nothing is wrong"* and *"the alerting is dead"* look exactly the
same, and the second one looks reassuring.

**Acknowledgement is one click**, and it travels back to KdbMonitor with the
login and the timestamp. There is a bulk *ack all amber*, because at volume the
alternative is fourteen clicks.

---

## 7. Volume

The desk expects a lot of alerts. Everything in this section exists because of
that, and it is the part most likely to decide whether the tool survives its
first month.

**Grey absorbs the volume.** Most alerts should be grey and never raise a card
at all. The review question when adding an alert is not *"is this useful?"* but
*"would I interrupt somebody's morning for this?"*, and the honest answer is
usually no.

**Coalescing by subject** (§5.4) turns a broken feed producing four hundred
events into a handful of cards with large count badges.

**Rate limiting per alert, per recipient.** Beyond N cards a minute the rest
roll into one summary card — *"RIL.IB — 23 events in 2m"*. This is the
guardrail against a misfiring alert papering the floor, and it works even when
coalescing does not because the subjects differ.

**A floor-wide kill switch.** One click in KdbMonitor silences an alert for
everybody, without editing it, without a deploy, and with a reason recorded.
When an alert misfires at nine in the morning the person who notices needs to
stop it in seconds, and *"edit the alert definition carefully under pressure"*
is not that.

**Per-recipient snooze.** A trader can mute an alert, or a whole desk's amber
tier, for a chosen period — thirty minutes, until the close. **Red can never be
muted locally**, and every snooze is visible in KdbMonitor so the desk can see
who has switched what off. A tool people cannot quieten is a tool people close;
a tool they can quieten invisibly is a tool that lies about its coverage.

**A weekly noise report.** Per alert: events sent, cards raised, acknowledged,
median time to acknowledge, and how often it was snoozed. An alert that is never
acknowledged is not doing anything except costing attention, and this is the
report that lets somebody argue for deleting it with evidence.

---

## 8. Routing

Three new concepts in KdbMonitor.

**Recipient** — a person. Has a display name and one or more Windows logins
(`DOMAIN\jsmith`). Logins rather than machines, so a trader who hot-desks is
still themselves, and a trader with a desktop and a laptop gets alerts on both.

**Desk** — a named group of recipients, e.g. *India coverage*. Subscribing a
desk is what stops the subscription lists rotting once there are forty alerts.

**Subscription** — joins an alert to recipients and/or desks, with an optional
`min_severity`. That last field lets a desk head take everything while the
juniors take only red, which at volume is the difference between the desk head
being informed and being buried.

Severity itself is set on the alert, not per person, so that everyone on the
floor shares one vocabulary. When two traders say *"there's a red on RIL"* they
must mean the same thing.

**There is no concept of a person in KdbMonitor today** — no users, no roles,
no login of any kind. The only recipient notion in the codebase is
`Channels.email_to`, free text typed per alert. So this is new schema and a new
page rather than an extension of something existing, and it carries a
consequence worth stating plainly: **anyone who can open KdbMonitor can change
who gets alerted.** That may be acceptable on your floor. It should be a
decision rather than a discovery. See §14.

---

## 9. The agent registry

Every DeskAlert instance heartbeats every fifteen seconds, reporting its
Windows login, hostname, version, uptime and which monitor it is docked to.
KdbMonitor gets an **Agents** page:

| User | Machine | Version | Last seen | Alerts | |
|---|---|---|---|---|---|
| DOM\jsmith | TRD-NY-14 | 1.2.0 | 3s ago | 6 | Send test |
| DOM\aroy | TRD-MUM-07 | 1.1.4 | 2h ago | 4 | ⚠ stale |

This answers the original requirement directly: which machine, and which user,
has the tool set up.

Three warnings on that page matter more than the table:

- **Subscribed but no agent running.** Somebody is meant to be receiving alerts
  and is not. This is the expensive failure and it is otherwise completely
  invisible.
- **Agent running under an unmapped login.** An unclaimed install, one click to
  attach it to a recipient.
- **Version behind.** So a rollout can be seen rather than assumed.

Plus **Send test** per row — indispensable during setup, and reassuring
afterwards.

Acknowledgements are recorded against the event, so the desk can answer *"did
anyone see it, and when?"*, which today is unanswerable.

---

## 10. Transport and security

**The agent opens the connection, never the daemon.** Server-sent events over
HTTPS, held open, with polling fallback. Outbound only means no inbound port
and no firewall exception on any trader desktop — in a trading firm that is the
difference between weeks of approvals and none.

Reconnection uses exponential backoff **with jitter**. Without jitter, a daemon
restart brings every agent on the floor back simultaneously and the first thing
the newly-started daemon experiences is a stampede.

Events queue per recipient while an agent is away. On reconnect the agent
receives what is still inside its TTL, flagged *while you were away*; the rest
went to history.

**Identity.** In the first version the daemon trusts the login the agent
reports, over a bootstrap token shared at install time, on the corporate
network. This is worth being honest about rather than dressing up: it means a
determined user on the LAN could impersonate a colleague and read their alerts.
For an internal tool on a trading floor that is very likely acceptable — but it
is a conscious trade, and §14 records it as such. Kerberos/SPNEGO is the upgrade
path if it turns out not to be.

Alert content is not encrypted at rest beyond whatever the machine already
does. Event bodies are headlines about the firm's own order flow, which is
sensitive but not more so than the platform windows already open beside them.

---

## 11. Data model

New tables. Nothing existing changes shape.

```sql
recipients        (id, name, email, enabled)
recipient_logins  (recipient_id, login)              -- several per person
desks             (id, name)
desk_members      (desk_id, recipient_id)
subscriptions     (id, alert_id, recipient_id, desk_id, min_severity)
                                                     -- exactly one of
                                                     -- recipient_id/desk_id
agents            (id, login, hostname, version, screen,
                   first_seen, last_seen)
snoozes           (recipient_id, alert_id, until, set_at)
events            (id, alert_id, dedupe_key, severity, subject,
                   headline, detail_json, count, created_at, expires_at)
deliveries        (event_id, recipient_id, agent_id,
                   delivered_at, acked_at, ack_login)
```

`events` is separate from the existing `alert_runs` on purpose. `alert_runs`
records *we checked*; `events` records *we told somebody*. Conflating them
would make both harder to reason about, and the existing daily statistics are
already derived from `alert_runs`.

Retention follows the existing convention — the app already keeps twenty days
of `alert_results` — so `events` and `deliveries` are trimmed on the same
schedule.

---

## 12. Failure modes

The section a spec is judged by.

| What breaks | What happens | How anyone finds out |
|---|---|---|
| Daemon stops | No evaluation, no dispatch | Every agent's dot goes red within 60s; the strip says *last contact 10:42* |
| Agent stops | That trader gets nothing | Agents page marks them stale; the *subscribed but no agent* warning fires |
| Network partition | Agent shows last known state, dot red | The dot, immediately |
| KDB unreachable | Existing behaviour — the run is recorded failed | Existing monitor page |
| Streamlit app closed | Nothing. Alerting continues | — |
| Two daemons started | Second refuses to start | Lease in `settings`; it logs and exits |
| Clock skew between machines | Ages and expiry wrong | Daemon sends absolute timestamps and the agent renders ages from its own clock against a measured offset |
| SQLite locked | Writes retry, then back off | WAL mode; contention logged |
| An alert misfires floor-wide | Kill switch, one click | Whoever sees it (§7) |
| Trader mutes something quietly | Visible on the Agents page | Snoozes are recorded and shown (§7) |

---

## 13. Deployment

One PyInstaller-built exe, signed, installed per machine. Auto-start via the
`HKCU\...\Run` key rather than a Windows service, because the agent draws a
window and therefore needs the interactive user's session. A single-instance
mutex stops a second copy starting.

The agent stores no configuration a trader has to fill in. It learns its
Windows login from the OS and the relay address from an install-time setting.
Anything a trader could be asked to configure is something that can be
configured wrong on a busy morning.

**Stack: Python and PySide6.** Same language as the rest of the project, so the
logic lives in a testable `core/` exactly as the codebase already requires, and
there is no second toolchain to maintain. Qt is the only realistic option that
does frameless, always-on-top, non-focus-stealing, per-monitor-DPI windows
properly on Windows. PySide6's LGPL licence is appropriate for internal use;
PyQt's is not without a commercial licence.

Monitor placement is remembered by **device name, not index**, because indices
shuffle whenever a monitor is unplugged and a trader who docks their laptop
should not find their alerts have moved.

Rollout: the desk's own machines first, then one trader who volunteers, for a
week, before anybody else. The thing being tested in that week is not whether
alerts arrive. It is whether they are still being read on day five.

---

## 14. Decisions still open

1. **Who may change subscriptions?** Today anyone who can open KdbMonitor can.
   Options: leave it, add a light admin gate on the routing pages, or bind
   changes to Windows login. Needs a decision before this is wired to desktops.
2. **Agent identity** — trust-the-LAN with a bootstrap token, or Kerberos
   (§10).
3. **How many traders**, so the rate limits and retention have real numbers
   behind them rather than guesses.
4. **Who owns severity?** Somebody has to be able to say *"no, that is not
   red"*, or everything becomes red within a quarter and the design collapses.
   This is a process question, not a code one, and it is the most likely cause
   of failure in the whole document.

---

## 15. Testing

Following the project's existing rule — logic in `core/`, unit-tested; `ui/`
thin and not unit-tested.

**Unit-tested in `core/`:** subscription resolution (recipients, desks, the
overlap between them, `min_severity`), dedupe key derivation and coalescing,
rate limiting, TTL and expiry, snooze windows, headline rendering and the
60-character cap, and the daemon's evaluation tick — which becomes testable for
the first time by virtue of leaving the browser.

**Integration:** relay tested against a scripted fake agent — connect, receive,
acknowledge, drop the connection, reconnect, receive the backlog, confirm
expired events are absent.

**Not unit-tested:** the Qt UI, consistent with the existing `ui/` rule. It
gets a manual checklist per release, and the first item on it is that a card
appearing while typing into another window swallows no keystroke.

---

## 16. Build order

Each step should leave something that works.

0. **Daemon.** Engine out of the browser, `core/` split, WAL, entry point,
   evaluation lease. Nothing user-visible; everything depends on it.
1. **The pipe.** Relay, agent connects and heartbeats, Agents page, Send test.
   No real alerts yet — this proves deployment and the network path, which are
   the parts that can fail for reasons no amount of code will fix.
2. **Routing.** Recipients, desks, subscriptions.
3. **The alert.** Severity and headline template with live preview in the
   builder, then the strip, cards, sound, acknowledgement, coalescing, TTL.
4. **Volume control.** Rate limits, kill switch, snooze, the noise report.
5. **Trust.** Connection dot, acknowledgement tracking, the three warnings,
   history panel.

Steps 0 and 1 are worth doing before anybody commits to the rest. If a signed
exe cannot be got onto trader machines, or the relay cannot be reached through
the network as it actually is, that is far better learned in week one than
after the interface is built.
