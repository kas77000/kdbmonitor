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
| `row_key` | identifies this row between runs (§5.4.0) — usually the order id |
| `breakdown` | what the stack counts on its second line — instance, market |
| `variant` | a state that must match to share a stack — limit *up* vs *down* |
| `group_id` | the stack it belongs to, if the result was long enough (§5.4) |
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

**An alert is a query, and its result decides everything here.** One row is one
order. Several rows mean several orders are triggering the same alert, and that
is what a burst *is* — not a rate, not something arriving over time, but a
result set that came back long. The query knows it the moment it returns.

That single fact removes most of what a burst design would otherwise need.
There is no detector, no sliding window, no threshold measured in events per
minute, and no leading edge where the first few slip through as individual
cards before the tool notices. The row count is known at once, so the decision
is made at once.

So there are two shapes, chosen by counting rows:

**Few rows — a card each.** One, two, three orders is the ordinary working day
and should look ordinary. Each row is a card, named by its subject.

```
▌ RIL.IB     limit up · 3 orders        4m
```

**Many rows — one stack.** Above the alert's threshold the whole result becomes
a single stack: the error, the count of orders, the count of names, and a
breakdown. Twenty rows is one thing that has happened, not twenty, and the
trader wants *"this error, forty orders, twenty-five names"* with the option to
open it rather than twenty lines to read.

```
▌ market data stale · 40 orders · 25 names   2m   ▾
    INST-03 22 · INST-07 18
```

Rows are grouped by the error, which is to say by the alert, because that is
what they have in common — different names, or one name with several orders, it
makes no difference to how it should read.

### 5.4.0 Across ticks

The alert re-runs on its schedule and returns a result each time, so the
display is of the *current* result rather than a history of arrivals. A row
that is still there is the same problem continuing; a row that has gone has
cleared (§5.5).

That needs rows to be identifiable between runs, so an alert names a **key
column** — the order id, usually. With it, an order stuck for four minutes
shows an age of four minutes instead of appearing new every tick, and it does
not re-sound. Without it the tool cannot tell continuation from recurrence, so
the key column is required rather than optional on any alert that raises cards.

A result crossing the threshold in either direction changes the shape: three
rows become twenty and the cards become a stack; twenty fall back to three and
the stack becomes cards again. Both are honest reports of what the query now
returns.

### 5.4.1 What a stack breaks down by

**The alert already carries its own scope, because the person who wrote it
chose one.** *Market data missing across all instances* and *limit up and down
in India* are two alerts, not one alert with a scope the tool has to work out.
Every event from the second is Indian by construction, and nothing needs to
deduce that.

Earlier drafts of this section had the tool classify each alert's scope —
whether the thing it named was a fault or a context, how far it reached, what
that implied. All of it was inventing structure the admin already supplies when
they write the alert. It is gone, and the design is smaller for it (§5.6).

What remains is one optional display setting per alert: a **breakdown column**,
naming what the stack should count on its second line. Purely presentational —
if the answer is *which instances*, the stack says which instances.

```
┌──────────────────────────────────────────────────────┐
│▌ market data stale · 40 orders · 25 names   2m   ▾  ▪│
│     INST-03 22 · INST-07 18                          │
├──────────────────────────────────────────────────────┤
│▌ limit up · 31 orders · 23 names            2m   ▾  ▪│
│     RIL.IB · INFY.IB · TCS.IB · +20 more             │
├──────────────────────────────────────────────────────┤
│▌ RIL.IB    limit up · 3 orders              1m      ▪│
└──────────────────────────────────────────────────────┘
```

Two rules remain, and both are about not saying more than is known.

**The breakdown is a count, never a cause.** *INST-03 22 · INST-07 18* states
what was seen. It must not be worded, and the stack must not be titled, so as
to imply one thing behind both. Where the admin's alert covers several markets
this matters most: Indian names at limit up and Japanese names at limit up are
unrelated stories that happen to share an alert, and the breakdown line is
exactly how one card can hold both honestly.

**The state is part of the identity.** Limit up and limit down are opposite
conditions, and *23 names at limit* leaves a trader not knowing which way the
market went — the only thing they wanted to know. An alert may therefore name a
**variant column** whose value must also match before events join a stack, so
that the group key is `alert + variant`. Often the admin will simply have
written two alerts instead, which does the same job; the column exists for when
one alert genuinely covers both.

Both the breakdown and the variant are optional. An alert with neither produces
one stack that lists its members, which is the right answer more often than
not.

### 5.4.2 The threshold, and where the number comes from

An alert stacks when its result returns more than `stack_at` rows. Default
four: two or three reads fine as individual cards, twenty does not. That is the
whole rule, and it is deliberately something a person can hold in their head.

**`stack_at` is set by hand, per alert. Nothing here tunes itself.**

That is a decision rather than a shortcut. How many names at limit up counts as
a lot cannot be derived: it moves from one day to the next, it differs between
Mumbai and Tokyo, and a number learned from last week is wrong on the week that
matters. Somebody who knows the alert picks a number, watches it, and changes
it. The noise report (§7.2) is what tells them whether they were right, and
replaying the existing `alert_runs` history is a way to inform that first guess
— but a person makes the choice, and no version of this design should quietly
start making it for them.

**This is a display threshold, not an alerting one.** It decides when cards
become a stack. Whether five Indian names at limit up is worth an alert at all
is the alert's own trigger condition, which KdbMonitor already expresses
(`row_count_gte` and the rest) and which can differ per market by being a
different alert. Keeping those two numbers apart matters: one is about screen
real estate, the other is about what the desk considers abnormal, and
conflating them would make a display tweak silently change what gets reported.

Stated in one sentence, which is the test of whether it can be explained to a
trader: *when the alert comes back with a lot of orders, they arrive as one
stack instead of a lot of cards.*

### 5.4.3 Escalating with the row count

An alert may declare that it changes severity as its result grows — *amber
normally, red beyond N rows.* Off by default, opt-in per alert, never inferred.

**Whether that is right is a judgement only the admin can make**, and it is the
one place where getting it wrong is expensive rather than merely untidy. Two
alerts, opposite answers:

- *Market data missing* returning forty rows instead of one is worse. Something
  is spreading and somebody must act. **Escalate.**
- *Limit up in India* returning forty names instead of one is not worse. It is
  what a volatile day looks like. An alert that reddens on it floods the floor
  on precisely the day the desk can least afford the interruption, and teaches
  them within a quarter that red means nothing. **Do not escalate.**

The tool cannot tell these apart — both are queries returning forty rows — and
earlier drafts of this document tried to, with a taxonomy that classified an
alert's scope and inferred the answer. It was guessing at something the admin
knows for certain. So it is a switch they set, its default is off, and the
noise report (§7.2) shows how often it fired so a wrong answer is visible
rather than merely suffered.

### 5.5 Clearing

**A row that has left the result has cleared**, and an alert whose result comes
back empty has cleared entirely. This falls straight out of §5.4.0 — comparing
one run's keys against the last is the whole mechanism — and it is worth as
much as the raising.

When forty orders' worth of trouble shows as one stack and then the query stops
returning them, the stack simply vanishing tells the desk nothing. *"Is it
over?"* is the question everybody has after an open goes badly, and a tool that
cannot answer it sends people back to the platform windows to find out by hand.

So a stack whose result empties shows a resolved state for a short while before
it goes, and a stack that partly clears just gets smaller:

```
✓ market data stale · cleared 09:31 · lasted 4m
```

KdbMonitor already thinks this way. `RearmPolicy(mode="transition")` fires on
the false-to-true edge, which means the true-to-false edge is known and is
currently discarded; `alert_runs.result_hash` already exists to tell one
result from the next. Clearing is that edge, given somewhere to go.

A cleared red is acknowledged automatically. There is nothing left to act on,
and making somebody dismiss a problem that has already gone is the kind of
small tax that gets a tool resented.

---

### 5.6 What the admin sets, and what the tool must never guess

The person writing the alert in KdbMonitor knows things the tool cannot derive:
what the query means, whether spreading further is worse, how many is a lot on
that market. **This design puts those decisions in their hands and keeps them
there.** Nothing here infers, learns, or tunes itself, and no later version
should start without a deliberate choice to do so.

Per alert:

| Setting | | Default |
|---|---|---|
| **severity** | red · amber · grey (§5.2) | amber |
| **headline** | template over the result columns, ≤60 chars, live preview | — |
| **subject column** | what the eye reads first — usually the sym | — |
| **key column** | identifies a row between runs (§5.4.0) | — |
| **stack threshold** | rows above which it becomes one stack (§5.4.2) | 4 |
| **breakdown column** | what the stack counts on its second line | none |
| **variant column** | a state that must match to share a stack (§5.4.1) | none |
| **time to live** | when an event stops being worth showing (§5.3) | 5 min |
| **escalates with spread** | and at what row count (§5.4.3) | off |
| **subscriptions** | recipients and desks (§8) | — |

Only three have no default, and two of those are columns the query already has.
**That is the point:** an admin adding an ordinary alert should be able to name
a subject, a key and a headline and be done. If the common case needs ten
decisions, forty alerts becomes a configuration project nobody finishes, and
the settings that matter get filled in carelessly along with the ones that do
not.

Worth building for the same reason: **presets**. Most of a desk's alerts fall
into a few shapes, and *"like the stuck-order one"* should copy those settings
rather than re-derive them. The dashboards side of the app already has a
copy-a-widget-config affordance; this is the same idea one level up.

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
below, and a row that persists from one run to the next never moves. The result
is that during the worst few minutes of the day the thing the eye is aiming at
holds still.

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

**One or two problems.** The ordinary working state: an alert's query comes
back with a row or two, and each is a card named by its subject. This is what
the design would be if it were the only phase, and most of the day it is.

**A burst.** The open, the close, or something breaking: the same query comes
back with forty rows. Everything below exists for this phase, because it is the
one where a naive design actively harms the desk — forty cards at once is worse
than no tool at all, since the trader now has to triage the alerts as well as
the orders.

Bursts differ in what they mean, and the difference is not something the tool
can see. *Market data missing* returning forty rows is one thing spreading and
somebody must act; *limit up in India* returning forty names is what a volatile
day looks like and nobody can fix it. Both are queries returning forty rows.
Which is which is the admin's to declare (§5.4.3), and the rest of this section
is what the design does regardless.

### 7.1 Surviving the burst

**One stack per error** (§5.4). This is the mechanism that matters, and it is
the inverse of what serves the quiet phase. Coalescing by name does nothing at
the open because every name is different — the thing they have in common is
the error, so that is what they are gathered by.

**A cross-market burst reaches people who do not share a market.** When an
instance fails, everyone with orders on it is affected regardless of what they
cover, so the roll-up card goes to all of them — and they all see the same
card, with the same scope on it, so when they turn to each other they are
describing the same thing rather than three separate mysteries. With
coverage-based routing (§8.1) this falls out on its own.

**There is no leading edge to worry about.** A design that detected bursts from
a rate would let the first few through as individual cards before it noticed,
and would need pre-arming around the open to compensate. Because the burst is
the row count of a single result (§5.4), the twenty-fifth row is known at the
same instant as the first and the stack forms whole. Nothing slips through, and
no schedule needs to anticipate anything.

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
events            (id, alert_id, group_id, run_id, row_key, severity, subject,
                   breakdown, variant,
                   headline, detail_json, first_seen, last_seen, expires_at,
                   cleared_at)                       -- UNIQUE (alert_id,
                                                     -- row_key) while live
event_groups      (id, alert_id, variant, stacked,
                   opened_at, closed_at, row_count, subject_count,
                   spread_json, peak_severity)
deliveries        (event_id, recipient_id, agent_id,
                   delivered_at, acked_at, ack_login)
```

`row_key` and `first_seen` are what make a run comparable to the one before it.
An order still in the result keeps its row, its age and its acknowledgement; an
order that has left it is cleared (§5.5). Without them every tick would look
like a fresh problem, and a stuck order would re-sound every fifteen seconds
for as long as it stayed stuck.

`event_groups` makes a stack a first-class thing rather than a display trick.
It survives an agent reconnect, gives clearing something to attach to, and lets
the noise report say *"the open produced three stacks lasting nine minutes"*
rather than only counting rows. `spread_json` holds the breakdown — which
instances or markets and how many of each — which is what the card's second
line prints, and what lets one card carry honestly what would otherwise have
been several.

The per-alert settings of §5.6 — the subject, key, breakdown and variant
columns, `stack_at`, the TTL, the escalation switch and the rate limit — live
inside the existing `alert_json` blob, following the app's established habit of
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
| No key column set on an alert | Every tick looks new; ages reset and sound repeats | Refused at design time — the editor will not save a card-raising alert without one |
| Key column is not unique in the result | Rows collide and one hides another | Detected on the first run and reported; the editor previews duplicates |
| Breakdown column null on some rows | Those rows count under *unknown* on the second line | Visible on the card itself |
| Escalation switched on for the wrong alert | It reddens on a volatile day and floods the floor | The kill switch, then the noise report; **this is the misconfiguration to watch for** (§5.4.3) |
| Several markets in one result | One stack, split on its second line — never worded as one cause | The breakdown is a count, not a judgement |
| Limit up and limit down in one result | Two stacks, one per direction | The variant column |
| `stack_at` set too high | Twenty cards instead of one stack | Obvious on the day; a settings change, and the noise report counts it |
| `stack_at` set too low | Three ordinary rows become a stack | Harmless, and the stack still expands |
| The query returns thousands of rows | One stack, breakdown truncated to what fits | `RESULT_MAX_ROWS` already caps this at 500 upstream |
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
6. **Does every alert's result carry something that identifies a row between
   runs?** (§5.4.0) An order id in most cases. This is the one genuinely
   blocking dependency left: without it a stuck order looks new every fifteen
   seconds. Worth checking against the existing alert list, since an alert
   whose query does not select a key needs its query amended rather than its
   settings.
7. **Which alerts escalate with the row count** (§5.4.3), and at what number?
   Off by default; worth going through the existing alerts with the desk rather
   than choosing a global answer. This is the setting most likely to be got
   wrong and the most expensive when it is.
8. ~~What are the real burst windows?~~ **No longer needed.** A rate detector
   would have had to be pre-armed around the open; a row count does not
   (§7.1).
9. ~~Which alerts are faults and which are cohorts?~~ **Dropped.** The
   taxonomy was inferring what the admin already decides by writing the alert
   (§5.4.1). What survives of it is one switch, item 7 above.
10. ~~How many names at limit up is normal?~~ **Settled: set by hand.** It
    cannot be derived — it moves day to day and differs by market — so a person
    who knows the alert picks the number and the noise report tells them
    whether they were right (§5.4.2). No part of this design tunes itself, and
    none should be added later without a deliberate decision to do so.

---

## 15. Testing

Following the project's existing rule — logic in `core/`, unit-tested; `ui/`
thin and not unit-tested.

**Unit-tested in `core/`:** subscription resolution (recipients, desks, the
overlap between them, `min_severity`, coverage-based routing), rate limiting,
TTL and expiry, snooze windows, headline rendering and the 60-character cap,
and the daemon's evaluation tick — which becomes testable for the first time by
virtue of leaving the browser.

**The grouping deserves its own test file**, and it is unusually easy to test
well: a result set in, a set of cards out, no clock involved.

Because the burst is now a row count rather than a rate, most of this is a pure
function of one result set and trivially testable: feed it a result, assert the
cards. Worth covering explicitly — stacking at exactly `stack_at` rows and not
one row earlier; several orders on one name joining the same stack as one order
each on many names; two instances in one stack still showing as two on the
breakdown line; limit up and limit down staying separate through the variant;
and a breakdown value that is null counting as *unknown* rather than raising.

The run-to-run logic (§5.4.0) is the part with real state and deserves the most
care: a row present in two consecutive results keeping its age, its position
and its acknowledgement; a row that disappears clearing; an empty result
clearing the whole stack; a result crossing the threshold in each direction
reshaping cards into a stack and back; a duplicate key detected rather than
silently collapsing two orders into one; and a red that clears acknowledging
itself.

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
3. **The alert.** Severity, subject and key columns, headline template with
   live preview in the builder, then the strip, cards, run-to-run continuity
   (§5.4.0), sound, acknowledgement, clearing, TTL. This is the *quiet* and
   *one or two problems* phases — a working tool for an ordinary day.
4. **The burst.** Stacking on the row count, `event_groups`, the breakdown and
   variant columns, coverage protection, sound at onset. Thresholds are typed
   in by a person, informed by replaying real `alert_runs` history rather than
   guessed — and never computed (§5.4.2).
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
