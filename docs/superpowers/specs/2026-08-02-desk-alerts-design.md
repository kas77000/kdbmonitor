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

**There will be a lot of alerts, and they arrive in bursts.** Not five a day,
and not evenly spread. The day has phases: long quiet stretches, an ordinary
state where one or two things are wrong, and sudden bursts — typically at the
open or the close — where many orders across many names show symptoms at once.
Designing for the average would get every phase wrong. This drives most of the
document and has §7 to itself. At volume the difficulty is no longer getting an
alert onto the screen; it is stopping the screen from becoming wallpaper.

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
| `scope_kind` | `fault` · `cohort` · `none` — how the scope should be read (§5.4.1) |
| `scope_value` | which instance, which market — what a burst groups by |
| `variant` | the state that must also match to roll together — limit *up* vs *down* |
| `dedupe_key` | `alert_id` + `subject`, the identity used to coalesce |
| `group_id` | the burst it belongs to, if any (§5.4) |
| `headline` | the one line the trader reads, already rendered |
| `detail` | up to three short lines, shown only on hover or in the panel |
| `created_at` | when it happened |
| `expires_at` | when it stops being worth showing (§5.3) |
| `cleared_at` | when the condition went away, if it has (§5.5) |

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

### 5.4 Grouping

This is the single most important mechanism in the design, and it has three
modes because the desk's day has three shapes (§7).

**Single.** One event, one card. The quiet case.

**Coalesced** — same alert, same subject. Events sharing a `dedupe_key` do not
stack. The existing card stays where it is, its count badge increments, and its
age resets:

```
▌ RIL.IB     7 orders stuck NEW      4m   ×7
```

The card does not move, flash, or re-sound. A card that jumps every time its
condition re-fires is a card the eye has to re-find, which is the opposite of
what a fixed position is for.

**Rolled up** — same alert, *many different* subjects sharing a scope. This is
the open, the close, and any infrastructure failure. Forty orders showing one
symptom is one thing that has happened, not forty, and the trader needs to see
it that way.

Coalescing by subject would make this *worse*, not better — every name is
distinct, so nothing coalesces and forty cards arrive. Rolling up is the
mechanism that actually protects the open.

### 5.4.1 Blast radius: faults and cohorts

Each alert declares a **scope column** beside its subject column — the column
whose value the affected orders have in common. But there are two quite
different reasons orders can have something in common, and treating them alike
is the mistake this section exists to prevent.

**A fault.** The named thing is broken, and it is the cause. An algo instance
loses market data; every order it carries is affected, across however many
markets that instance happens to serve. The name on the card *is* the problem.

**A cohort.** The named thing is where it is happening, not what is wrong.
Twenty-five Indian names hit limit up inside a minute. India is not broken.
There is nothing to fix. The market is simply what the affected names have in
common, and the cause is out in the market — volatility, news, a circuit
breaker regime.

| | fault | cohort |
|---|---|---|
| example | market data stale on INST-03 | limit up and down across Indian names |
| the name on the card is | the cause | the context |
| how much of the scope | usually all of it | **always a subset** — particular names in particular states |
| what the desk does | escalate, somebody must fix it | trade around it, tell the client |
| spreading further means | it is getting worse | on a volatile day, nothing |

```
┌──────────────────────────────────────────────────────┐
│▌ INST-03   market data stale · 40 orders · 3 markets │  fault
│     .IB 22 · .T 12 · .HK 6                           │
├──────────────────────────────────────────────────────┤
│▌ .IB       limit up · 25 names                       │  cohort
│     RIL.IB · INFY.IB · TCS.IB · +22 more             │
├──────────────────────────────────────────────────────┤
│▌ RIL.IB    limit up · 3 orders                       │  single
└──────────────────────────────────────────────────────┘
```

Four rules follow, and they are the careful part of this document.

**A cohort card never claims its scope.** It names the condition and counts
names: *limit up · 25 names*. Never *.IB down*, never a count of orders phrased
so as to imply the whole market. A cohort is by definition partial, and a card
that reads as though India has failed sends a trader to support for something
support cannot fix.

**Cohorts are never aggregated across scope values.** Indian names at limit up
and Japanese names doing something else are two unrelated stories that happen
to share an alert definition. Rolling them into *50 names across 2 markets*
would invent a common cause that does not exist. Per-scope-value detection
(§5.4.2) already produces two cards; this is the reason it must stay that way.
A **fault** card counting its markets is the opposite case and is legitimate,
because there one cause genuinely does reach all of them.

**The state is part of the identity.** Limit up and limit down are opposite
conditions, and *25 names at limit* leaves a trader not knowing which way the
market went — which is the only thing they wanted to know. So an alert may name
a **variant column** whose value must also match before events roll together.
The group key is `alert + scope value + variant`, and limit up and limit down
become two cards.

**Escalation by spread applies to faults only** (§5.4.3). A fault reaching more
orders is worse. A cohort reaching more names is what a volatile day looks
like, and escalating on it would turn every big move into a red.

`scope: none` exists for alerts where rolling up would destroy the information,
and each event then stands alone. Use it sparingly — an alert that feels
inherently per-name still gathers into a cohort on a big day, which is exactly
the case the desk already sees.

### 5.4.2 The switch

**Detection is per scope value, not per alert.** An alert enters rolled-up mode
for a given scope value when it produces events for more than `burst_subjects`
distinct subjects sharing that value inside `burst_window` (defaults: four in
sixty seconds), and leaves it after a full quiet window.

Per scope value rather than per alert matters: two instances failing at once
are two problems and get two cards. A per-alert detector would have merged them
into one and hidden the fact that it was happening twice.

Stated in one sentence, which is the test of whether it can be explained to a
trader: *if one alert starts hitting a lot of orders behind the same instance,
market or name, it becomes one card instead of a lot of them.*

### 5.4.3 Escalating with the blast radius

A **fault** alert may declare that it changes severity as it spreads — *amber
normally, red beyond N orders.* One order behind a failing instance is worth a
look; forty is worth interrupting somebody.

**Cohort alerts must not do this**, and the reason is worth stating plainly
because the mistake would be easy and expensive. Twenty-five Indian names at
limit up is not twenty-five times worse than one; it is what a volatile day
looks like. An alert that escalates on cohort spread turns every large market
move into a floor-wide red, which is precisely the day the desk least needs to
be interrupted and the fastest way to teach them that red means nothing.

Escalation is opt-in, declared per alert, never inferred, and refused outright
on a cohort-scoped alert. Severity that moves for reasons a trader cannot
predict would undo the point of having three fixed colours (§5.2). Declared, it
stays predictable, and the noise report shows how often it fired.

### 5.5 Clearing

An event can be resolved as well as raised, and during a burst this matters as
much as the raising.

When forty cards' worth of trouble collapses into one card and then the
condition goes away, the card simply vanishing tells the desk nothing. *"Is it
over?"* is the question everybody has after an open goes badly, and a tool that
cannot answer it sends people back to the platform windows to find out by hand.

So a rolled-up or coalesced card whose condition clears shows a resolved state
for a short while before it goes:

```
✓ 40 orders stuck NEW · cleared 09:31 · lasted 4m
```

KdbMonitor already tracks this. `RearmPolicy(mode="transition")` fires on the
false-to-true edge, which means the true-to-false edge is known too and is
currently discarded. Clear events are that edge, given somewhere to go.

A cleared red is also acknowledged automatically — there is nothing left to
act on, and making somebody dismiss a problem that has already gone is the kind
of small tax that gets a tool resented.

---

## 6. What the trader sees

### 6.1 The strip

A frameless, always-on-top bar roughly 28 pixels high, docked to one edge of a
monitor the trader chooses once. It is always there and never moves.

It looks different in each of the day's three phases, and the difference is
itself information.

**Nothing wrong** — near-silent. No colour, no counts, just proof of life:

```
                                                  ● 10:42
```

This matters more than it looks. A strip that is visually loud when it has
nothing to say is a strip that gets tuned out by eleven o'clock, and then it is
still tuned out at the open. Colour appearing at all must mean something
happened.

**One or two problems** — the ordinary case:

```
▌1  ●2    RIL.IB  7 orders stuck NEW              ● 10:42
 ▲   ▲    ▲                                       ▲
 red total  most severe live event            connection
```

**A burst** — the open, the close, or an outage:

```
▌1  ●40   40 orders stuck NEW · 25 names          ● 09:16
```

The trader needs no badge saying BURST. *"25 names"* is the signal, and it is
the fact they need anyway.

Permanence is the point of all three. A Windows toast is the wrong answer here:
it appears in a corner every trader has spent years learning to ignore, and then
it vanishes whether or not anybody looked. A strip that has been in the same
place all morning gets noticed when its colour changes, without being looked
at.

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

**The stack is ordered by severity, then oldest first within a severity, and it
does not re-sort.** Not newest-first: during a burst, newest-first makes the
whole stack churn every few seconds and the trader can never finish reading a
line. A card that has sat unacknowledged for four minutes stays at the top,
which is also where the most neglected thing belongs. New arrivals appear
below, and coalescing never moves anything. The result is that during the worst
few minutes of the day the thing the eye is aiming at holds still.

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

## 7. The day has phases

The load is not steady, and designing for an average would get both ends wrong.
The desk described three shapes to its day, and the tool should have the same
three.

**Quiet.** Nothing is wrong, sometimes for hours. The design problem here is
not missing an alert — it is the tool making itself ignorable before the alert
arrives. Hence the near-silent strip (§6.1). A tool that is visually loud with
nothing to say has spent its credibility before the open.

**One or two problems.** The ordinary working state. A name has a problem, the
trader gets that name, per-subject cards, coalescing on repeats. This is what
the design would be if it were the only phase, and it is where §5.4's
*coalesced* mode lives.

**A burst.** The open, the close, or something breaking: many orders showing
symptoms at once. Everything below exists for this phase, because it is the one
where a naive design actively harms the desk — forty cards arriving in ten
seconds is worse than no tool at all, since the trader now has to triage the
alerts as well as the orders.

Bursts are not all the same size or shape, and the difference is the alert's
nature rather than the time of day.

Most are **cohorts** — a set of names in one market sharing a state, such as
Indian names hitting limit up together. Nothing is broken and nothing needs
fixing; the desk needs to know because it changes how they work the orders and
what they tell the client. Several markets can be in unrelated cohorts at the
same time, and they stay separate stories (§5.4.1).

The dangerous one is a **fault** — a market data problem on an algo instance
takes out every order that instance carries, and those orders span several
markets, so this burst crosses the boundary the desk normally thinks in.

Some alerts barely burst at all. §5.4.1 is how the design tells these apart;
this section is what it does about them.

### 7.1 Surviving the burst

**Roll up by what broke, not by name** (§5.4). This is the mechanism that
matters, and it is the inverse of what serves the quiet phase. Coalescing by
subject does nothing at the open because every name is different.

**A cross-market burst reaches people who do not share a market.** When an
instance fails, everyone with orders on it is affected regardless of what they
cover, so the roll-up card goes to all of them — and they all see the same
card, with the same scope on it, so when they turn to each other they are
describing the same thing rather than three separate mysteries. With
coverage-based routing (§8.1) this falls out on its own.

**The leading edge is the weak spot.** A detector that needs four subjects in
sixty seconds lets the first three through as individual cards. That is a
tolerable cost at 11:30 and a bad one at the open, where those three are noise
and everybody knows a burst is coming. So the burst threshold can be lowered
inside declared windows: **`Schedule` and `Window` already exist in the
codebase** — same shape, same timezone handling — so an alert can say *expect
bursts 09:15–09:30 and 15:20–15:30* and be pre-armed rather than always one
burst behind.

Detection stays adaptive underneath. Outages do not check the clock, and a
schedule-only design would be defenceless at 11:30.

**Sound collapses to onset.** One tone when a burst begins, then silence until
it ends, regardless of how many events arrive. The existing rule — one sound
per twenty seconds — is right for the ordinary phase and still far too much for
the open. The information *"something big is starting"* is worth a sound
exactly once.

**Say when it is over** (§5.5). After a bad open, *"has it stopped?"* is the
question, and answering it is worth as much as the original alert.

**A trader's own names are never rolled up.** If a recipient covers RIL.IB and
RIL.IB is caught in a burst, it keeps its own card and the other twenty-four
names collapse behind it. This is the one place the design uses the fact that
these are *coverage* traders rather than generic recipients, and it is what
stops the roll-up hiding the very thing a given trader is responsible for. It
depends on knowing who covers what (§8.1).

### 7.2 The steady-state guardrails

These apply in every phase.

**Grey absorbs the volume.** Most alerts should be grey and never raise a card
at all. The review question when adding an alert is not *"is this useful?"* but
*"would I interrupt somebody's morning for this?"*, and the honest answer is
usually no.

**Rate limiting per alert, per recipient.** A hard ceiling on cards per minute
that sits above the roll-up. Roll-up handles a burst that makes sense; the rate
limit handles an alert that has simply gone wrong, which is a different problem
and needs a floor under it that does not depend on the events being related.

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

### 8.1 Coverage

A recipient can also carry a **coverage list** — the names, or whole markets,
that trader is responsible for. Both kinds, because coverage is not always at
the same granularity and a trader who covers India should not have to have
every Indian name enumerated. It does two things:

- **It protects their names during a burst** (§7.1). Their syms keep individual
  cards while everything else rolls up. Without this, the roll-up that saves the
  desk at the open can hide the one name a particular trader actually owns.
- **It can route.** An alert can be addressed to *whoever covers the name in the
  event* rather than to a fixed list, which is a much better fit for a coverage
  desk than maintaining subscriptions by hand and keeps working when coverage
  changes.

This is the one place the design uses what the desk actually is rather than
treating them as generic recipients, and it is worth the extra table.

It does need the coverage data to exist and stay current, which is a question
about your firm rather than about this tool — if it lives in a system that can
be read or exported, the list should be synced from there rather than typed in
and left to rot. If there is no such source, coverage is typed in per recipient
and the burst protection degrades gracefully to *everything rolls up*, which is
still the correct behaviour, just less kind.

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
recipient_coverage(recipient_id, kind, value, source) -- §8.1; kind = 'sym' |
                                                     -- 'market', source =
                                                     -- 'manual' | 'synced'
desks             (id, name)
desk_members      (desk_id, recipient_id)
subscriptions     (id, alert_id, recipient_id, desk_id, min_severity,
                   by_coverage)                      -- exactly one of
                                                     -- recipient_id/desk_id,
                                                     -- or by_coverage=1
agents            (id, login, hostname, version, screen,
                   first_seen, last_seen)
snoozes           (recipient_id, alert_id, until, set_at)
events            (id, alert_id, group_id, dedupe_key, severity, subject,
                   scope_kind, scope_value, variant,
                   headline, detail_json, count, created_at, expires_at,
                   cleared_at)
event_groups      (id, alert_id, scope_kind, scope_value, variant, mode,
                   opened_at, closed_at, subject_count, event_count,
                   spread_json, peak_severity)       -- mode = 'single' |
                                                     -- 'coalesced' | 'rolled'
                                                     -- UNIQUE (alert_id,
                                                     -- scope_value, variant)
                                                     -- while open
deliveries        (event_id, recipient_id, agent_id,
                   delivered_at, acked_at, ack_login)
```

`event_groups` is what makes a burst a first-class thing rather than a display
trick. It gives the roll-up card an identity that survives a reconnect, gives
the clear (§5.5) something to attach to, and gives the noise report a way to
say *"the open produced three bursts lasting a total of nine minutes"* instead
of only counting events. The uniqueness on `(alert_id, scope_value, variant)`
is what keeps two instances failing at once as two cards, and keeps limit up
and limit down from collapsing into one meaningless *"25 names at limit"*.

`spread_json` holds the rung below the scope — the market breakdown under an
instance, the name breakdown under a market — which is what the card's second
line prints and what makes it useful without expanding.

Per-alert settings — `scope_column`, `burst_subjects`, `burst_window`, the
escalation threshold, the declared windows, the rate limit — live inside the
existing `alert_json` blob, following the app's established habit of
serialising an alert whole rather than normalising its every field into
columns.

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
| Burst detector fires when it should not | A handful of related alerts show as one card that expands | The card says how many names; the noise report counts bursts |
| Scope column missing or null on an event | Falls back to ungrouped — a card of its own | Logged; the alert's editor warns at design time |
| Two instances fail at once | Two cards, one per instance | Groups are unique per scope value, not per alert |
| An alert is given the wrong scope | Cards group by the wrong thing, but nothing is lost | Visible immediately on the first burst; a settings change, not a rebuild |
| A cohort alert is marked as a fault | It escalates on a volatile day and floods the floor with red | The kill switch, then the noise report; this is the misconfiguration to watch for |
| Two markets in unrelated cohorts | Two cards, never merged | Enforced by the group key, not by judgement |
| Limit up and limit down at once | Two cards, one per direction | The variant column |
| Burst detector fails to fire | Up to the rate limit in cards, then rolled anyway | The rate limit is the floor under the detector |
| Agent reconnects mid-burst | Receives the group, not its hundreds of member events | `event_groups` survives the reconnect |
| A burst never clears | Red persists unacknowledged, correctly | It stays on the strip; that is the point |

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
5. **Is there a source for coverage** (§8.1) that can be read or exported? If
   yes, sync it. If no, coverage is typed in and the burst protection is
   coarser.
6. **What names the instance and the market in the result?** The whole of
   §5.4.1 rests on there being a column for each. If the algo tables carry an
   instance identifier and a market or exchange code, this is free; if the
   market has to be derived from the sym suffix, that derivation needs a home
   and should live in `core/` beside the existing transforms rather than being
   written into every alert's query by hand.
7. **What are the real burst windows?** The open and the close were mentioned;
   whether they are the only ones, and their exact times per market, decides
   what gets pre-armed. Note that a firm covering several markets has several
   opens, so this is a list per market rather than one pair of times.
8. **Which alerts escalate with spread** (§5.4.3), and at what count? Faults
   only, and worth deciding per alert with the desk rather than choosing a
   global default.
9. **Which alerts are faults and which are cohorts?** (§5.4.1) This has to be
   set per alert by somebody who knows what the alert means, and it is the
   setting most likely to be got wrong — a cohort mislabelled as a fault floods
   the floor with red on the busiest day of the quarter. Going through the
   existing alert list and marking each one is a short exercise and worth doing
   before any of this is built.
10. **How many names at limit up is normal for each market?** A cohort
    threshold that is right for Tokyo may be wrong for Mumbai. The honest
    answer is that somebody who knows the market sets it, and the noise report
    (§7.2) shows whether they were right. Deliberately not automated in a first
    version.

---

## 15. Testing

Following the project's existing rule — logic in `core/`, unit-tested; `ui/`
thin and not unit-tested.

**Unit-tested in `core/`:** subscription resolution (recipients, desks, the
overlap between them, `min_severity`, coverage-based routing), dedupe key
derivation and coalescing, rate limiting, TTL and expiry, snooze windows,
headline rendering and the 60-character cap, and the daemon's evaluation tick —
which becomes testable for the first time by virtue of leaving the browser.

**The burst logic deserves its own test file**, because it is the part with
real state and the part that fails in the way that matters. It is also easy to
test properly: feed a timed sequence of events to a pure function and assert
the grouping. Worth covering explicitly — entering rolled-up mode at the
threshold and not one event before it; a trader's covered name staying out of
the roll-up; the group surviving a reconnect; leaving rolled-up mode only after
a full quiet window rather than flapping on the first gap; a clear arriving for
a group that was never opened; and a burst that starts inside a declared window
with the lowered threshold.

The scope logic (§5.4.1) needs its own cases on top, and these are the ones
that protect against telling the trader a false story: two instances failing at
once staying two cards rather than merging; a fault spanning three markets
counting markets and not names; **two markets in unrelated cohorts never
merging into one card, however many names each has**; limit up and limit down
staying separate through the variant; a cohort headline never rendering a form
of words that implies the whole market; escalation refused on a cohort-scoped
alert even when configured; an event whose scope or variant column is null
falling back to ungrouped rather than raising; and escalation firing at its
declared count and not one subject earlier.

**Integration:** relay tested against a scripted fake agent — connect, receive,
acknowledge, drop the connection, reconnect, receive the backlog, confirm
expired events are absent, and confirm a mid-burst reconnect delivers one group
rather than four hundred events.

**Replay against real history.** The existing `alert_runs` table holds months
of what actually happened. Before any of the burst thresholds are chosen they
should be run against that history to see how many bursts a given setting would
have declared on a real open. This costs almost nothing and replaces guesses
with measurements.

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
2. **Routing.** Recipients, desks, subscriptions, coverage.
3. **The alert.** Severity and headline template with live preview in the
   builder, then the strip, cards, sound, acknowledgement, coalescing, TTL.
   This is the *quiet* and *one or two problems* phases — a working tool for
   an ordinary day.
4. **The burst.** Roll-up, `event_groups`, clears, the pre-armed windows,
   coverage protection, sound at onset. Thresholds chosen by replaying real
   `alert_runs` history, not picked from the air.
5. **Volume control.** Rate limits, kill switch, snooze, the noise report.
6. **Trust.** Connection dot, acknowledgement tracking, the three warnings,
   history panel.

Step 4 is separated from step 3 deliberately. The burst is the phase where the
tool earns its place, but it is also the one that cannot be judged from a desk
chair — it needs a real open to tell whether the roll-up threshold is right.
Shipping step 3 first means arriving at that open with something already
trusted on an ordinary day, and one variable to watch rather than ten.

Steps 0 and 1 are worth doing before anybody commits to the rest. If a signed
exe cannot be got onto trader machines, or the relay cannot be reached through
the network as it actually is, that is far better learned in week one than
after the interface is built.
