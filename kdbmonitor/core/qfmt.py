# kdbmonitor/core/qfmt.py
"""Python values -> q literals, for the where clause a guided filter builds.

Every type here has to be *told* what it is. q has no way to look at the text
"2026-07-30" and know whether it was meant as a date or as arithmetic, and it
does not ask: it reads it as 2026 minus 7 minus 30 and returns 1989. So the
value type is part of the filter, and each one has exactly one meaning.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

VALUE_TYPES = ("symbol", "number", "string", "date", "time", "expression")

# A placeholder another stage fills in: {{dataset.column}} from an earlier
# dataset, {{param:name}} from the reader, {{date_from}} from the period. It is
# not a value yet, so it is passed through rather than formatted — a symbol type
# would otherwise turn {{orders.sym}} into `{`{`o`r`d`e`r`s..., one backtick per
# character, and the substitution that came next would find nothing to replace.
_PLACEHOLDER = re.compile(r"^\s*\{\{[^{}]+\}\}\s*$")


def is_placeholder(value: Any) -> bool:
    """Whether this value is a token for a later stage rather than a value."""
    return isinstance(value, str) and _PLACEHOLDER.match(value) is not None

# A date written any of the ordinary ways. q wants dots; people type dashes,
# slashes, or paste whatever their last export used.
_DATE_TEXT = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*$")


def q_date(value: Any) -> str:
    """A q date literal — ``2026.07.30``.

    Accepts a ``date``/``datetime``, or text written with dashes, slashes or
    dots. Anything else raises rather than being passed through: a date that
    silently became subtraction is the failure this type exists to prevent, and
    it fails as a wrong number rather than as an error.
    """
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return f"{value:%Y.%m.%d}"

    match = _DATE_TEXT.match(str(value))
    if not match:
        raise ValueError(
            f"'{value}' is not a date. Write it as 2026-07-30, or use the "
            f"expression type for something q works out itself, like .z.D-1.")
    year, month, day = (int(p) for p in match.groups())
    return f"{date(year, month, day):%Y.%m.%d}"


def format_q_value(value: Any, value_type: str) -> str:
    if is_placeholder(value):
        return str(value).strip()
    if value_type == "symbol":
        return "`" + str(value)
    if value_type == "number":
        return str(value)
    if value_type == "string":
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if value_type == "date":
        return q_date(value)
    if value_type == "time":
        # A time of day, or a timestamp, written as q already spells it.
        return str(value).strip()
    if value_type == "expression":
        # q the author wrote, sent as it stands: .z.D-1, .z.D, .z.P, or a
        # sub-select. Guided mode has always been able to reach raw q through
        # the raw mode beside it, so this adds no reach — it saves rewriting a
        # whole query to compute one value.
        return str(value).strip()
    raise ValueError(f"unknown value_type: {value_type}")


def format_q_list(values: list, value_type: str) -> str:
    # One placeholder standing in for the whole list. Whatever fills it in
    # produces a q list already, so it must not be enlisted or type-formatted
    # here — `sym in {{orders.sym}}` becomes `sym in `AAPL`MSFT`, which is the
    # list, not a list holding one thing.
    if len(values) == 1 and is_placeholder(values[0]):
        return str(values[0]).strip()
    if value_type == "symbol":
        if not values:
            return "`$()"          # empty symbol vector — keeps `x in ...` valid
        joined = "".join("`" + str(v) for v in values)
        return joined if len(values) > 1 else "enlist " + joined
    if value_type == "number":
        if not values:
            return "0#0"           # empty numeric vector
        joined = " ".join(str(v) for v in values)
        return joined if len(values) > 1 else "enlist " + joined
    if value_type == "string":
        if not values:
            return "()"            # empty list
        parts = [format_q_value(v, "string") for v in values]
        return "(" + ";".join(parts) + ")" if len(values) > 1 else "enlist " + parts[0]
    if value_type in ("date", "time"):
        if not values:
            # An empty date vector. `0#0d` types it, so `date in ...` stays a
            # comparison of dates rather than of longs.
            return "0#0d" if value_type == "date" else "0#0t"
        parts = [format_q_value(v, value_type) for v in values]
        joined = " ".join(parts)
        return joined if len(values) > 1 else "enlist " + parts[0]
    if value_type == "expression":
        if not values:
            return "()"
        parts = [format_q_value(v, "expression") for v in values]
        return "(" + ";".join(parts) + ")" if len(values) > 1 else "enlist " + parts[0]
    raise ValueError(f"unknown value_type: {value_type}")
