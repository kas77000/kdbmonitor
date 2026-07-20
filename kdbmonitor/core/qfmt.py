# kdbmonitor/core/qfmt.py
from __future__ import annotations

from typing import Any


def format_q_value(value: Any, value_type: str) -> str:
    if value_type == "symbol":
        return "`" + str(value)
    if value_type == "number":
        return str(value)
    if value_type == "string":
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    raise ValueError(f"unknown value_type: {value_type}")


def format_q_list(values: list, value_type: str) -> str:
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
    raise ValueError(f"unknown value_type: {value_type}")
