r"""Values the reader of a dashboard chooses, and where they reach.

Substitution rather than a new mechanism: ``{{param:name}}`` is filled the same
way ``{{stepN.column}}`` and ``{{date_from}}`` already are, so there is one idea
about how a stored dashboard takes a run-time value rather than four.

A parameter reaches wherever it is written, and *where* decides what changing it
costs:

* in a **transform or a widget spec** it re-shapes frames already in hand, so a
  dropdown does not send a historical dashboard back to re-read partitions.
  There it is substituted as plain text, because that is what it is compared
  against — a pandas value, not q.
* in a **query** it goes back to the server, and it is substituted as a *q
  literal* through :mod:`kdbmonitor.core.qfmt` — `AAPL` becomes ``\`AAPL``, a
  date becomes ``2026.07.30``, a string has its quotes escaped. Pasting the raw
  text in would be both wrong (q cannot compare a symbol column to a char
  vector) and unsafe, and qfmt is already the one place that translation lives.

:func:`query_params` is what tells the two apart, by looking at where the token
actually appears.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from kdbmonitor.core.dashboard_models import Dashboard, Parameter
from kdbmonitor.core import paramrules, qfmt

_PARAM_REF = re.compile(r"\{\{param:([^{}]+)\}\}")

_TRUE = ("true", "1", "yes", "on")


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


def params_in_query(dataset) -> set[str]:
    """Every parameter this dataset's *query* reads.

    Both modes: a raw query naming ``{{param:sym}}`` anywhere, and a guided
    filter whose value is one — ``qfmt`` passes a placeholder through untouched
    (see :func:`kdbmonitor.core.qfmt.is_placeholder`), so a guided filter and a
    raw query arrive at the same substitution by the same route.
    """
    used = params_in(dataset.raw_qsql or "")
    for f in getattr(dataset, "filters", []) or []:
        used |= params_in(f.value)
    return used


def query_params(dashboard: Dashboard) -> set[str]:
    """Every parameter any of this dashboard's queries reads.

    The one place that decides which parameters cost a round trip — the
    controls form, the refresh, and the validation gate all read it rather than
    each deciding for itself.
    """
    used: set[str] = set()
    for ds in dashboard.datasets:
        used |= params_in_query(ds)
    return used


def unresolved_params(dashboard: Dashboard) -> set[str]:
    """Every ``{{param:...}}`` this dashboard uses, anywhere.

    Validation compares this against what is declared, so a typo is caught while
    the dashboard is being built rather than as an empty panel afterwards — or,
    for a query, as a parse error from kdb naming a token it has never heard of.
    """
    used: set[str] = set()
    for ds in dashboard.datasets:
        used |= params_in_query(ds)
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


def as_q(parameter: Parameter, value: Any) -> str:
    r"""One value as the q literal it will be substituted with.

    Typed rather than pasted: the same text is ``\`AAPL``, ``"AAPL"`` or
    ``2026.07.30`` depending on what the author said this parameter *is*, which
    is the whole reason a q type is stored beside the control's kind.
    """
    kind = paramrules.q_type_of(parameter)
    if kind == "boolean":
        return "1b" if str(value).strip().lower() in _TRUE else "0b"
    if kind == "date":
        chosen = paramrules.as_date(value)
        # A date that got this far has passed its rules; anything else is left
        # to qfmt, which raises with a message naming the value.
        return qfmt.q_date(chosen if chosen is not None else value)
    return qfmt.format_q_value(value, kind)


def q_values(parameters: list[Parameter], chosen: dict[str, Any],
             frames: dict[str, Any]) -> dict[str, str]:
    """Every parameter as a q literal, ready to substitute into a query.

    Values are resolved exactly as :func:`resolve_values` resolves them for
    transforms — same fallbacks, same staleness handling — and only then
    formatted, so a control and the query it feeds can never disagree about
    which value applied.
    """
    resolved = resolve_values(parameters, chosen, frames)
    out: dict[str, str] = {}
    for p in parameters:
        raw = resolved.get(p.name, "")
        if raw == "":
            # Nothing chosen and nothing to fall back on. Left unsubstituted:
            # the query then fails naming the token, which is a better error
            # than a query silently missing a predicate.
            continue
        try:
            out[p.name] = as_q(p, raw)
        except ValueError:
            continue                # same reasoning: leave it to be reported
    return out


def substitute_query(qsql: str, literals: dict[str, str]) -> str:
    """Fill every ``{{param:name}}`` in a query with its q literal."""
    return _PARAM_REF.sub(
        lambda m: literals.get(m.group(1).strip(), m.group(0)), qsql or "")
