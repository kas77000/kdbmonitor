# KdbMonitor — Design Spec

**Date:** 2026-07-15
**Status:** Draft for review

## 1. Purpose

A tool for the Algo trading account team to monitor orders and market state
by running **chains of KDB queries** and firing a **notification** when a chain
produces a defined result (no rows, some rows, or at least one row matching a
condition).

Users build alerts through a guided builder that leverages live schema
introspection (real table and column names). Alerts run while the app is open
and notify the alert's chosen channels when triggered.

## 2. Key decisions (settled)

| Decision | Choice |
|---|---|
| Evaluation model | Checks run **only while the Streamlit app is open**, via an auto-refresh loop. No separate daemon (for now). |
| Deployment | **Local now, structured for shared server later.** Config store and admin/user roles designed so a move to a shared host / background scheduler needs no rewrite. |
| Connections | Register a KDB server with **hostname + port only**. No authentication. |
| Query authoring | **Hybrid:** guided form (server → table → filters from real columns → condition) with an **advanced raw-qSQL escape hatch** per step. |
| Chaining | A chain is an ordered list of steps. Later steps reference earlier outputs via `{{stepN.column}}`. **Steps may target different servers** (cross-server chains are first-class). |
| Notifications | Channels available: **in-app banner + sound**, **email (SMTP)**, **Teams/Slack webhook**. **Each alert's creator selects which channels fire** for that alert. |
| Alert management | Full CRUD: **add / edit / delete**, plus an **enable/disable toggle** per alert. |

## 3. Architecture

Single Streamlit app with three role-gated areas, backed by a thin,
UI-independent **core** package.

```
Streamlit UI
├── Admin → Connections   register servers, introspect + cache schema, SMTP/webhook settings
├── Builder → Alerts      CRUD alerts; author query chains + trigger rule + channel selection
└── Monitor → Live        auto-refresh loop; evaluate due alerts; show status; fire notifications

core/ (no Streamlit imports — testable, reusable by a future scheduler)
├── connections    pykx QConnection management (cache, reconnect-on-failure)
├── schema         table/column introspection
├── chain          step expansion, {{stepN.col}} substitution, sequential execution
├── conditions     evaluate final result against a trigger condition
├── rearm          transition/cooldown state machine
├── notifiers      one interface per channel (in-app, email, webhook)
└── storage        SQLite persistence layer
```

Keeping all logic in `core/` means the same evaluation path can later be driven
by a background scheduler on a shared server without touching the UI.

## 4. Data model (SQLite)

- **connections** — `id, name, host, port, cached_schema (JSON), last_introspected_at`
- **alerts** — `id, name, enabled, poll_interval_secs, chain (JSON), trigger_condition (JSON), channels (JSON), rearm_policy (JSON), created_at, updated_at`
- **alert_runs** — `id, alert_id, ts, status (armed|triggered|error), row_count, message` — history + audit, powers the Monitor view

## 5. Chain model (core abstraction)

An **alert** = ordered list of **steps** + a final **trigger rule**.

Each **step**:
- `server` — which registered connection to query (may differ per step)
- `table` — target table
- `filters` — built from real column names, OR raw qSQL (advanced mode)
- `output` — named result whose columns later steps can reference

**Reference syntax:** `{{stepN.column}}` expands to the distinct values of that
column from step N's result (e.g. a symbol list injected into the next `where`).

**Trigger condition** (evaluated on the final step's result) — covers all the
flexibility requested:
- `no rows` / `has rows` / `row count >= N`
- `at least one row where <col> <op> <value>`
- `all rows where <col> <op> <value>`
- aggregate: `max|min|avg|sum of <col> <op> <value>`

## 6. Re-arm policy (avoid notification spam)

Default: **notify on transition into `triggered`, then stay quiet until the
condition clears** (re-arms). Options:
- **Cooldown:** re-notify at most every X minutes while still triggered.
- **Every tick:** notify on every evaluation while triggered (override).

## 7. Notifications

Per-alert channel selection. Notifier layer with one interface per channel:
- **In-app + sound** — always available; banner/toast in the Monitor view + audio.
- **Email** — SMTP settings held in Admin; per-alert recipient list.
- **Teams/Slack** — incoming webhook URL per channel target.

## 8. Query execution & safety

- One cached pykx `QConnection` per server, reused across ticks, reconnect on failure.
- Steps run **sequentially**; a step error marks the alert `error` (surfaced in
  Monitor) rather than crashing the app.
- Guided-form filter values are **parameterized, not string-concatenated**.
- The raw-qSQL escape hatch is powerful and unsafe by nature; acceptable for an
  internal trusted tool, clearly labeled "advanced."

## 9. Defaults

- **Poll interval:** per-alert, default **30s**, floor **5s**.
- **Re-arm:** transition-into-triggered (not every tick).
- **Monitor** shows a clear "actively monitoring" heartbeat so it's obvious the
  loop is alive (checks only run while the page is open).

## 10. Testing

`core/` logic tested against a **fake KDB connection** returning canned tables —
no live server required. Covered: chain expansion, `{{stepN.col}}` substitution,
condition evaluation, re-arm state machine, channel selection. UI kept thin.

## 11. Out of scope (for now / YAGNI)

- Background daemon / always-on evaluation (deferred; architecture allows it later).
- Authentication to KDB servers.
- Multi-user auth/permissions beyond the admin/user role flag.
