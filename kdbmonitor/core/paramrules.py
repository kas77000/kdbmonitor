r"""What a form value has to satisfy before a query is sent.

Two kinds of rule live here and they are checked in this order, because they
answer different questions.

**Can this be written into q at all.** A value is substituted into a query, so
a symbol with a semicolon in it is not a formatting problem — ``\`AAPL; delete
from t`` parses as a symbol *and* a delete, and q would run both. This check is
not the author's to switch off; a parameter that reaches a query is checked
against its ``q_type`` whatever else was configured. The one exception is the
``expression`` type, which exists precisely to send q the author wrote, and
which the editor labels as such.

**What this particular dashboard considers a sensible value.** Required or not,
a pattern to match, bounds, whole numbers, weekdays only. These are the
author's, they are all optional, and they are the reason the reader hears
"that date is a Sunday — the HDB has no partition for it" instead of watching
an empty dashboard and wondering.

Pure and dateless: ``today`` is passed in, never read, so a rule that says
"within the last 30 days" is testable on a Tuesday in March.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from kdbmonitor.core.dashboard_models import DEFAULT_Q_TYPE, Parameter

# A q symbol is a name: letters, digits, dots, underscores, and the colons a
# handle like `:localhost:5000 needs. Everything else — a space, a semicolon, a
# bracket, a backtick — ends the symbol and starts something q would execute.
_SAFE_SYMBOL = re.compile(r"^[A-Za-z0-9._:]*$")

# 'today', 'today-30d', 'today+1d' — a bound that follows the calendar rather
# than being retyped every morning.
_RELATIVE = re.compile(r"^today\s*(?:([+-])\s*(\d+)\s*d)?$", re.IGNORECASE)

_DATE_TEXT = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*$")

_TRUE = ("true", "1", "yes", "on")


def q_type_of(parameter: Parameter) -> str:
    """The q type this parameter is written as."""
    if parameter.q_type:
        return parameter.q_type
    return DEFAULT_Q_TYPE.get(parameter.kind, "symbol")


def label_of(parameter: Parameter) -> str:
    return parameter.label or parameter.name or "This value"


def as_date(value: Any) -> Optional[date]:
    """``value`` as a date, or None if it is not one.

    Accepts what a date picker returns and what a stored default holds — the
    same value arrives as a ``date`` from one and as text from the other.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = _DATE_TEXT.match(str(value or ""))
    if not match:
        return None
    year, month, day = (int(p) for p in match.groups())
    try:
        return date(year, month, day)
    except ValueError:          # 2026-02-31 and friends
        return None


def as_number(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def resolve_bound(text: str, today: date) -> Optional[date]:
    """A date bound written absolutely or relative to ``today``."""
    raw = (text or "").strip()
    if not raw:
        return None
    relative = _RELATIVE.match(raw)
    if relative:
        sign, days = relative.groups()
        if not days:
            return today
        delta = timedelta(days=int(days))
        return today - delta if sign == "-" else today + delta
    return as_date(raw)


def _describe_bound(text: str, today: date) -> str:
    """A bound as the reader should hear it: the date, and why if it moves."""
    resolved = resolve_bound(text, today)
    if resolved is None:
        return str(text)
    if _RELATIVE.match((text or "").strip()):
        return f"{resolved.isoformat()} ({text.strip()})"
    return resolved.isoformat()


def _check_writable(parameter: Parameter, value: Any) -> Optional[str]:
    """Whether this value can be written into a query as its q type at all."""
    kind = q_type_of(parameter)
    label = label_of(parameter)
    text = str(value).strip()

    if kind == "expression":
        return None                 # q the author asked for, by definition
    if kind == "symbol":
        if not _SAFE_SYMBOL.match(text):
            return (f"{label}: '{text}' cannot be a symbol — use letters, "
                    f"digits, '.', '_' or ':' only.")
        return None
    if kind == "number":
        if as_number(text) is None:
            return f"{label}: '{text}' is not a number."
        return None
    if kind in ("date", "time"):
        if kind == "date" and as_date(value) is None:
            return f"{label}: '{text}' is not a date."
        return None
    if kind == "boolean":
        if text.lower() not in _TRUE + ("false", "0", "no", "off"):
            return f"{label}: '{text}' is not a yes or a no."
        return None
    return None                     # string: qfmt escapes it, so anything goes


def _check_rules(parameter: Parameter, value: Any, today: date) -> Optional[str]:
    label = label_of(parameter)
    text = str(value).strip()
    kind = q_type_of(parameter)

    if parameter.pattern:
        try:
            matched = re.search(parameter.pattern, text) is not None
        except re.error as exc:
            return f"{label}: this parameter's own pattern is broken ({exc})."
        if not matched:
            return (parameter.pattern_message
                    or f"{label}: '{text}' does not match {parameter.pattern}.")

    if kind == "number":
        number = as_number(text)
        if number is None:
            return None             # already reported by the writable check
        if parameter.integer and number != int(number):
            return f"{label}: {text} must be a whole number."
        low = as_number(parameter.minimum)
        high = as_number(parameter.maximum)
        if low is not None and number < low:
            return f"{label}: {text} is below the minimum of {parameter.minimum}."
        if high is not None and number > high:
            return f"{label}: {text} is above the maximum of {parameter.maximum}."

    if kind == "date":
        chosen = as_date(value)
        if chosen is None:
            return None             # already reported by the writable check
        low = resolve_bound(parameter.minimum, today)
        high = resolve_bound(parameter.maximum, today)
        if low is not None and chosen < low:
            return (f"{label}: {chosen.isoformat()} is earlier than "
                    f"{_describe_bound(parameter.minimum, today)}.")
        if high is not None and chosen > high:
            return (f"{label}: {chosen.isoformat()} is later than "
                    f"{_describe_bound(parameter.maximum, today)}.")
        if parameter.weekdays_only and chosen.weekday() >= 5:
            day = chosen.strftime("%A")
            return (f"{label}: {chosen.isoformat()} is a {day} — pick a "
                    f"weekday.")
    return None


def check(parameter: Parameter, value: Any, *, today: date) -> Optional[str]:
    """What is wrong with this value, or None if nothing is.

    A blank is either the whole problem or not a problem at all: there is
    nothing to say about the shape of a value nobody gave, and running every
    other rule against "" would report a missing symbol as a bad one.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        if parameter.required:
            return f"{label_of(parameter)} is required."
        return None
    # The author's rules first, so that where they wrote a message for exactly
    # this case the reader hears it rather than the machinery underneath —
    # "Use an uppercase ticker, e.g. AAPL" beats "cannot be a symbol: use
    # letters, digits, '.', '_' or ':' only". Nothing is let through by the
    # swap: a value that satisfies the author still has to be writable, and the
    # rules that overlap with a type defer to it (see the number and date
    # branches, which say nothing about a value that is not one).
    return (_check_rules(parameter, value, today)
            or _check_writable(parameter, value))


def check_all(parameters: list[Parameter], values: dict[str, Any], *,
              today: date, only: Optional[set[str]] = None) -> dict[str, str]:
    """Every problem, by parameter name. Empty when the form is good to run.

    ``only`` narrows it to the parameters that actually matter for what is
    about to happen — the ones a query reads, when the question is whether a
    query may be sent.
    """
    problems: dict[str, str] = {}
    for p in parameters:
        if only is not None and p.name not in only:
            continue
        problem = check(p, values.get(p.name, p.default), today=today)
        if problem:
            problems[p.name] = problem
    return problems
