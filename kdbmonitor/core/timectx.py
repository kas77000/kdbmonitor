"""Time context: turning a stored range *spec* into concrete dates.

Dashboards store the spec, never resolved dates, so a saved "last 30 days"
dashboard means the last 30 days whenever it is opened rather than freezing on
the day it was built. Resolution happens once per refresh.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

PRESETS = ("today", "yesterday", "last_7d", "last_30d", "mtd", "last_month", "ytd")

PRESET_LABELS = {
    "today": "Today", "yesterday": "Yesterday", "last_7d": "Last 7 days",
    "last_30d": "Last 30 days", "mtd": "Month to date",
    "last_month": "Last month", "ytd": "Year to date",
}


@dataclass(frozen=True)
class ResolvedTime:
    mode: str                    # realtime | historical
    start: Optional[date]
    end: Optional[date]

    @property
    def label(self) -> str:
        if self.mode == "realtime":
            return "Real-time"
        return f"Historical · {self.start:%Y-%m-%d} → {self.end:%Y-%m-%d}"


def _preset(name: str, today: date) -> tuple[date, date]:
    if name == "today":
        return today, today
    if name == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if name == "last_7d":
        return today - timedelta(days=6), today
    if name == "last_30d":
        return today - timedelta(days=29), today
    if name == "mtd":
        return today.replace(day=1), today
    if name == "last_month":
        last_day_prev = today.replace(day=1) - timedelta(days=1)
        return last_day_prev.replace(day=1), last_day_prev
    if name == "ytd":
        return today.replace(month=1, day=1), today
    raise ValueError(f"unknown preset: {name}")


def _relative(n: int, unit: str, today: date) -> tuple[date, date]:
    if unit not in ("days", "weeks"):
        raise ValueError(f"unknown relative unit: {unit}")
    days = n * 7 if unit == "weeks" else n
    return today - timedelta(days=days - 1), today


def resolve(spec: dict, today: date) -> ResolvedTime:
    """Resolve a time-context spec against ``today``."""
    if (spec or {}).get("mode") != "historical":
        return ResolvedTime("realtime", None, None)

    rng = spec.get("range") or {}
    kind = rng.get("kind")
    if kind == "absolute":
        start = date.fromisoformat(rng["from"])
        end = date.fromisoformat(rng["to"])
    elif kind == "relative":
        start, end = _relative(int(rng.get("n", 1)), rng.get("unit", "days"), today)
    elif kind == "preset":
        start, end = _preset(rng.get("name", ""), today)
    else:
        raise ValueError(f"unknown range kind: {kind}")

    if start > end:
        raise ValueError(f"range starts after it ends: {start} > {end}")
    return ResolvedTime("historical", start, end)


# --- what a dashboard offers ------------------------------------------------
#
# Switching period means switching server, so a dashboard can only offer both
# where every environment it reads has both sides. One that reads a historical
# feed with nothing live behind it says so once, and is never asked for a period
# it cannot answer.

PERIOD_MODES = ("both", "realtime", "historical")

PERIOD_LABELS = {"both": "Both — switch between them",
                 "realtime": "Real-time only",
                 "historical": "Historical only"}

# The period a historical-only dashboard falls back to, when what it had stored
# was real-time. Today's partition is the nearest thing to "now" the HDB has.
_DEFAULT_HISTORICAL = {"mode": "historical",
                       "range": {"kind": "preset", "name": "today"}}


def offers(periods: str, mode: str) -> bool:
    """Whether a dashboard offering ``periods`` can be asked for ``mode``."""
    return periods == "both" or periods == mode


def coerce_spec(spec: dict, periods: str) -> dict:
    """A stored period, held to what the dashboard offers.

    A dashboard can be declared one-sided after it was built, and its stored
    period outlives the declaration. Rather than resolve to a server that is not
    there, it lands on the nearest period that is offered — which is also what
    stops the picker being asked to select an option it no longer lists.
    """
    mode = (spec or {}).get("mode", "realtime")
    if offers(periods, "historical" if mode == "historical" else "realtime"):
        return spec
    return {"mode": "realtime"} if periods == "realtime" else dict(_DEFAULT_HISTORICAL)


def q_date(d: date) -> str:
    """A kdb+ date literal: 2026.06.01."""
    return f"{d:%Y.%m.%d}"


def date_clause(rt: ResolvedTime) -> str:
    """The where-clause constraining the partition column. Empty in real-time."""
    if rt.mode != "historical":
        return ""
    return f"date within ({q_date(rt.start)};{q_date(rt.end)})"


_DATE_REF = re.compile(r"\{\{(date_from|date_to|date_list)\}\}")
_DATE_WORD = re.compile(r"\bdate\b")

# Mode-conditional blocks: {{#historical}}…{{/historical}} is kept only when the
# dataset resolves to the HDB, {{#realtime}}…{{/realtime}} only when it does not.
# Without these, one raw query cannot serve both modes — a date predicate is
# mandatory against a partitioned HDB and meaningless against an RDB.
_BLOCK = re.compile(r"\{\{#(historical|realtime)\}\}(.*?)\{\{/\1\}\}", re.S)


def apply_mode_blocks(qsql: str, mode: str) -> str:
    """Keep the blocks matching ``mode``, drop the others."""
    return _BLOCK.sub(lambda m: m.group(2) if m.group(1) == mode else "",
                      qsql or "")


def unresolved_date_refs(qsql: str) -> bool:
    """Whether any {{date_*}} placeholder survived substitution.

    In real-time mode nothing fills them, so one left outside a
    {{#historical}} block would be sent to KDB verbatim.
    """
    return _DATE_REF.search(qsql or "") is not None


def substitute_dates(qsql: str, rt: ResolvedTime) -> str:
    """Resolve mode blocks, then fill {{date_from}} / {{date_to}} / {{date_list}}."""
    qsql = apply_mode_blocks(qsql, rt.mode)
    if rt.mode != "historical":
        return qsql

    def repl(m: re.Match) -> str:
        if m.group(1) == "date_from":
            return q_date(rt.start)
        if m.group(1) == "date_to":
            return q_date(rt.end)
        days = (rt.end - rt.start).days + 1
        return " ".join(q_date(rt.start + timedelta(days=i)) for i in range(days))

    return _DATE_REF.sub(repl, qsql)


def has_date_constraint(qsql: str) -> bool:
    """Whether a raw query mentions the ``date`` column at all.

    The guard against an unconstrained scan of a partitioned HDB — which does not
    error, it just reads years of data and hangs a refreshing page.
    """
    return _DATE_WORD.search(qsql or "") is not None
