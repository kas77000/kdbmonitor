"""Values the reader of a dashboard chooses, and how they reach its transforms.

Substitution rather than a new mechanism: ``{{param:name}}`` is filled the same
way ``{{stepN.column}}`` and ``{{date_from}}`` already are, so there is one idea
about how a stored dashboard takes a run-time value rather than four.

Nothing here reaches a query. A parameter feeds transforms and widget specs, so
changing one re-shapes frames already in hand instead of going back to the
server — which keeps a control change distinct from a refresh, and keeps a
historical dashboard from re-reading partitions every time somebody picks
something from a dropdown.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from kdbmonitor.core.dashboard_models import Dashboard, Parameter

_PARAM_REF = re.compile(r"\{\{param:([^{}]+)\}\}")


def substitute(value: Any, values: dict[str, str]) -> Any:
    """``value`` with every ``{{param:name}}`` filled in, copied not rewritten.

    A placeholder naming a parameter that does not exist is left as it stands.
    Blanking it would turn a filter into one that matches everything and says
    nothing about why; left intact it arrives as "no column '{{param:typo}}'",
    which names the mistake.
    """
    if isinstance(value, str):
        return _PARAM_REF.sub(
            lambda m: values.get(m.group(1).strip(), m.group(0)), value)
    if isinstance(value, dict):
        return {k: substitute(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, values) for v in value]
    return copy.deepcopy(value)


def params_in(value: Any) -> set[str]:
    """Every parameter name referenced anywhere inside ``value``."""
    if isinstance(value, str):
        return {m.group(1).strip() for m in _PARAM_REF.finditer(value)}
    if isinstance(value, dict):
        out: set[str] = set()
        for v in value.values():
            out |= params_in(v)
        return out
    if isinstance(value, list):
        out = set()
        for v in value:
            out |= params_in(v)
        return out
    return set()


def unresolved_params(dashboard: Dashboard) -> set[str]:
    """Every ``{{param:...}}`` this dashboard's transforms or widgets use.

    Validation compares this against what is declared, so a typo is caught while
    the dashboard is being built rather than as an empty panel afterwards.
    """
    used: set[str] = set()
    for ds in dashboard.datasets:
        for t in ds.transforms:
            used |= params_in(t.params)
    for row in dashboard.rows:
        for w in row.widgets:
            used |= params_in(w.spec)
            used |= params_in(w.title)
    return used


def choices_for(parameter: Parameter,
                frames: dict[str, Any]) -> list[str]:
    """What this parameter offers.

    A ``column`` parameter reads the **raw** frame of its dataset, before that
    dataset's transforms run — which is the only frame where every value is
    still there. After the filter the parameter itself drives, exactly one is.
    """
    if parameter.kind != "column":
        return [str(c) for c in parameter.choices]
    frame = frames.get(parameter.dataset)
    if frame is None or parameter.column not in getattr(frame, "columns", []):
        return []
    seen = frame[parameter.column].dropna().tolist()
    return [str(v) for v in dict.fromkeys(seen)]


def resolve_values(parameters: list[Parameter], chosen: dict[str, Any],
                   frames: dict[str, Any]) -> dict[str, str]:
    """The value each parameter actually has, ready to substitute.

    A chosen value no longer on offer falls back to the default, and a default
    no longer on offer to the first choice: a selection outlives the file that
    offered it, and a dashboard rendering nothing because of a stale pick is
    worse than one rendering something the reader can change.
    """
    out: dict[str, str] = {}
    for p in parameters:
        options = choices_for(p, frames)
        value = chosen.get(p.name, p.default)
        if options:
            if str(value) not in options:
                value = p.default if p.default in options else options[0]
        elif p.kind in ("choice", "column"):
            value = p.default or ""
        out[p.name] = "" if value is None else str(value)
    return out
