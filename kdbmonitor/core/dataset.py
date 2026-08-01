"""Running a dashboard's datasets.

One dataset -> one DataFrame. Failures are *captured*, never raised: a dead
server must degrade one panel, not blank the page. The historical date clause is
built here rather than stored in the dataset's filters, which is what makes
flipping a dataset between environments lossless.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Optional

import pandas as pd

from kdbmonitor.core import parameters as params_mod
from kdbmonitor.core.chain import filter_clause, substitute_refs
from kdbmonitor.core.dashboard_models import Dashboard, Dataset
from kdbmonitor.core.models import (
    CONNECTION_KINDS, KIND_LABELS, Connection,
)
from kdbmonitor.core.timectx import (
    ResolvedTime, date_clause, has_date_constraint, resolve, substitute_dates,
    unresolved_date_refs,
)
from kdbmonitor.core.transform import Step, apply_transforms, transform_steps


@dataclass
class DatasetResult:
    name: str
    df: Optional[pd.DataFrame]      # None when the dataset failed
    qsql: str                       # the query that was (or would have been) sent
    error: Optional[str]
    row_count: int = 0              # true size before max_rows capping
    truncated: bool = False
    # A file dataset with nothing uploaded yet has not failed — it is waiting.
    # The distinction is for the reader: "waiting for your export" is an
    # instruction, and a red panel saying the same thing reads as a fault.
    waiting: bool = False
    # What each parameter offered, read from this dataset's frame as fetched.
    # Carried on the result so the controls can be built from the last run
    # rather than by fetching a second time.
    choices: dict = field(default_factory=dict)


@dataclass
class DatasetTrace:
    """A dataset run kept stage by stage: the query, then each transform.

    ``error`` is a failure that happened before any frame existed (no server, a
    refused query); a transform failure lives on the step that raised it.
    """
    name: str
    qsql: str
    error: Optional[str]
    steps: list[Step] = field(default_factory=list)
    # Same reasoning as DatasetResult.choices — read from the frame as fetched,
    # before this dataset's own transforms could have narrowed it down.
    choices: dict = field(default_factory=dict)

    @property
    def df(self) -> Optional[pd.DataFrame]:
        """The frame the pipeline ends on, or None if nothing ran."""
        return self.steps[-1].df if self.steps and self.steps[-1].df is not None \
            else None

    @property
    def failed_step(self) -> Optional[Step]:
        return next((s for s in self.steps if s.error), None)


def resolve_connection(store, env: str, kind: str) -> Connection:
    """The connection serving ``env`` in the given environment kind."""
    envs = store.list_environments()
    if env not in envs:
        raise ValueError(f"unknown environment: '{env}'")
    conn = envs[env].get(kind)
    if conn is None:
        raise ValueError(
            f"environment '{env}' has no {kind} server — add one in Admin")
    return conn


def is_marketdata_env(pair: dict) -> bool:
    """Market-data environments hold reference data (instruments and the like)."""
    return pair.get("marketdata") is not None


def standalone_side(pair: dict) -> "str | None":
    """The one kind this environment has, when that is all it will ever have.

    An environment is normally a real-time server and its historical twin, and a
    missing side reads as setup left half-done — worth saying so, because a
    dashboard cannot switch period without both. Some environments are not like
    that: a date-partitioned feed with no live counterpart is historical and
    nothing else, and its owner has said so here.

    That does not make the missing side answerable. It changes what there is to
    say about it: not "add one in Admin", which is advice for a server nobody is
    going to add, but that this environment only does the one period.
    """
    if is_marketdata_env(pair):
        return None                     # its own kind already stands alone
    for kind in ("realtime", "historical"):
        conn = pair.get(kind)
        other = "historical" if kind == "realtime" else "realtime"
        if conn is not None and conn.standalone and pair.get(other) is None:
            return kind
    return None


def resolve_target(store, env: str,
                   rt: ResolvedTime) -> tuple[Connection, ResolvedTime]:
    """The server to query, and the time context that actually applies to it.

    Market data is not partitioned by date, so a dashboard's period does not
    apply to it: those datasets resolve to the market-data server and run with
    no date clause whatever the period says. Real-time/historical environments
    resolve by the period as usual.
    """
    envs = store.list_environments()
    if env not in envs:
        raise ValueError(f"unknown environment: '{env}'")

    pair = envs[env]
    if is_marketdata_env(pair):
        return pair["marketdata"], ResolvedTime("realtime", None, None)

    conn = pair.get(rt.mode)
    if conn is None:
        solo = standalone_side(pair)
        if solo:
            raise ValueError(
                f"environment '{env}' is {KIND_LABELS[solo].lower()} only, so it "
                f"cannot answer {KIND_LABELS[rt.mode].lower()}")
        raise ValueError(
            f"environment '{env}' has no {rt.mode} server — add one in Admin")
    return conn, rt


def effective_time(ds: Dataset, dashboard_time: ResolvedTime,
                   today: date) -> ResolvedTime:
    """The time context this dataset actually runs under."""
    if ds.time_mode == "realtime":
        return ResolvedTime("realtime", None, None)
    if ds.time_mode == "custom":
        return resolve(ds.time_context or {"mode": "realtime"}, today)
    return dashboard_time


# A cross-process handle reference: {{conn:ENV}} is replaced with the
# `:host:port hsym of that environment's server, so a raw query can `hopen` it
# and pull a second database's tables across in the same query.
_CONN_REF = re.compile(r"\{\{conn:([^{}]+)\}\}")


def resolve_handle(store, target: str, rt: ResolvedTime) -> Connection:
    """The server a ``{{conn:...}}`` names, in any of its three forms.

    ``ENV``
        that environment's server for the period in force — the original form,
        and still the one to reach for when a dashboard should federate to the
        historical twin on a historical period and the live one otherwise.
    ``ENV:historical`` / ``ENV:realtime`` / ``ENV:marketdata``
        one named side of it, whatever period the dataset is running under. A
        live query that needs yesterday's reference data asks for it plainly
        rather than being told it is asking for today's.
    ``name``
        one connection, by its own name. An environment holds a single server
        per kind, but a desk's environment holds many databases, so a query
        that must reach one of them in particular has to be able to say which.

    Raises with what it was given, which ``run_dataset`` captures as the panel's
    error rather than letting it blank the page.
    """
    text = target.strip()
    if ":" in text:
        env, _, kind = text.partition(":")
        env, kind = env.strip(), kind.strip().lower()
        if kind not in CONNECTION_KINDS:
            raise ValueError(
                f"'{kind}' is not a kind of server "
                f"(have: {', '.join(CONNECTION_KINDS)})")
        pair = store.list_environments().get(env)
        if pair is None:
            raise ValueError(f"unknown environment: '{env}'")
        conn = pair.get(kind)
        if conn is None:
            raise ValueError(
                f"environment '{env}' has no {KIND_LABELS[kind].lower()} server")
        return conn

    envs = store.list_environments()
    if text in envs:
        return resolve_target(store, text, rt)[0]

    by_name = {c.name: c for c in store.list_connections()}
    if text in by_name:
        return by_name[text]
    raise ValueError(
        f"'{text}' is neither an environment nor a connection. Known "
        f"environments: {', '.join(sorted(envs)) or '(none)'}.")


def substitute_connections(qsql: str, store, rt: ResolvedTime) -> str:
    r"""Resolve every ``{{conn:...}}`` to that server's ``\`:host:port``.

    See :func:`resolve_handle` for what may go inside. An unknown name or a
    missing side raises, which ``run_dataset`` captures as the panel's error.
    """
    def repl(m: re.Match) -> str:
        return f"`:{resolve_handle(store, m.group(1), rt).host}:" \
               f"{resolve_handle(store, m.group(1), rt).port}"

    return _CONN_REF.sub(repl, qsql)


def build_qsql(ds: Dataset, rt: ResolvedTime, outputs: dict, store=None,
               params: Optional[dict] = None) -> str:
    """The query for this dataset, with dates, dataset refs, cross-process
    connection handles and the reader's parameters resolved. ``store`` is needed
    only to resolve ``{{conn:ENV}}`` handles; omit it and those tokens are left
    untouched.

    ``params`` are already q literals — see
    :func:`kdbmonitor.core.parameters.q_values` — and they are filled in last,
    for both modes at once. A guided filter reaches here with its placeholder
    still intact because ``qfmt`` passes one through rather than quoting it, so
    "the value of this filter is a parameter" needs no separate machinery.
    """
    literals = params or {}
    if ds.mode == "raw":
        q = substitute_refs(substitute_dates(ds.raw_qsql or "", rt), outputs)
        if store is not None:
            q = substitute_connections(q, store, rt)
        return params_mod.substitute_query(q, literals)

    clauses: list[str] = []
    if rt.mode == "historical":
        clauses.append(date_clause(rt))       # first, so kdb+ prunes partitions
    clauses += [filter_clause(f) for f in ds.filters]
    base = f"select from {ds.table}"
    q = base if not clauses else f"{base} where {', '.join(clauses)}"
    return params_mod.substitute_query(substitute_refs(q, outputs), literals)


def _file_source(ds: Dataset) -> str:
    """What stands in for the query on a file dataset — what it reads, not how.

    The editor and the result panel both show a dataset's query. A file dataset
    has none, and showing nothing there reads as something missing rather than
    as something that does not apply.
    """
    shape = ds.shape
    if shape is None:
        return "file: no shape configured yet"
    where = "down column" if shape.header_axis == "column" else "on line"
    return (f"file: {len(shape.columns)} column(s), headers {where} "
            f"{shape.header_row + 1}")


def _fetch(ds: Dataset, rt: ResolvedTime, store, mgr, outputs: dict,
           uploads: Optional[dict] = None,
           params: Optional[dict] = None) -> tuple[str, Optional[pd.DataFrame],
                                                   Optional[str]]:
    """Send the dataset's query — no transforms — as (qsql, frame, error).

    Never raises: every failure comes back as the error, along with whatever the
    query looked like at that point, so a caller can show it. Shared by the plain
    run and the step-by-step trace, so both send exactly the same query. A file
    dataset sends nothing — its frame either sits in ``uploads`` already read and
    validated at the upload box, or it does not, and either way this is the only
    branch that knows the difference; every line after it is source-agnostic.
    """
    # A file dataset sends nothing. Its frame was read and checked at the upload
    # box (``core.filesource``), so all that happens here is picking it up —
    # which is why a file dataset and a KDB dataset are indistinguishable from
    # the next line on.
    if ds.source == "file":
        frame = (uploads or {}).get(ds.name)
        if frame is None:
            return (_file_source(ds), None,
                    f"waiting for {ds.file_label or 'a file to be uploaded'}")
        return _file_source(ds), frame, None

    # Resolve first: the date guard must apply to the server actually queried,
    # and a market-data environment is never historical.
    try:
        conn, effective = resolve_target(store, ds.env, rt)
    except Exception as exc:      # noqa: BLE001 - a broken panel, not a page
        return "", None, str(exc)

    if effective.mode == "historical" and ds.mode == "raw" \
            and not has_date_constraint(ds.raw_qsql or ""):
        return (ds.raw_qsql or "", None,
                "historical query must constrain 'date' — add a "
                "date within ({{date_from}};{{date_to}}) clause")

    qsql = ""
    try:
        qsql = build_qsql(ds, effective, outputs, store, params)
        if unresolved_date_refs(qsql):
            # Only reachable in real-time, where nothing fills them. Sending
            # '{{date_from}}' to KDB would be a baffling parse error.
            return (qsql, None,
                    "this query uses {{date_from}}/{{date_to}} outside a "
                    "{{#historical}}…{{/historical}} block, so it cannot run in "
                    "real-time — wrap the date predicate in that block")
        return qsql, mgr.get(conn).query(qsql), None
    except Exception as exc:      # noqa: BLE001 - a broken panel, not a broken page
        return qsql, None, str(exc)


def _shaped(ds: Dataset, values: Optional[dict]) -> list:
    """This dataset's transforms with the reader's choices filled in.

    Rebuilt rather than edited: the dashboard is stored, and substituting into
    it would persist one reader's choice for whoever opens it next.
    """
    if not values:
        return list(ds.transforms)
    return [replace(t, params=params_mod.substitute(t.params, values))
            for t in ds.transforms]


def _apply(ds: Dataset, qsql: str, df: pd.DataFrame,
          values: Optional[dict]) -> DatasetResult:
    """The transform half of a run: shape the fetched frame, then cap it.

    Split out of :func:`run_dataset` so :func:`run_datasets` can resolve a
    dataset's parameter values — which needs that dataset's *raw* frame — after
    the fetch but before this runs.
    """
    try:
        shaped = apply_transforms(df, _shaped(ds, values))
    except Exception as exc:      # noqa: BLE001 - a broken panel, not a broken page
        return DatasetResult(ds.name, None, qsql, str(exc))

    total = len(shaped)
    capped = shaped.head(ds.max_rows).reset_index(drop=True)
    return DatasetResult(ds.name, capped, qsql, None,
                         row_count=total, truncated=total > len(capped))


def run_dataset(ds: Dataset, rt: ResolvedTime, store, mgr, outputs: dict,
                uploads: Optional[dict] = None,
                values: Optional[dict] = None,
                params: Optional[dict] = None) -> DatasetResult:
    """Run one dataset, capturing any failure as an error on the result.

    ``values`` are already-resolved ``{{param:name}}`` substitutions for the
    transforms, and ``params`` the same values formatted as q literals for the
    query — see :func:`run_datasets`, which is what resolves both dataset by
    dataset. A caller with no parameters to thread through (the historical
    direct-call shape) omits them and gets the old behaviour.
    """
    qsql, df, error = _fetch(ds, rt, store, mgr, outputs, uploads, params)
    if error is not None:
        return DatasetResult(ds.name, None, qsql, error,
                             waiting=ds.source == "file"
                             and error.startswith("waiting for"))
    return _apply(ds, qsql, df, values)


def run_dataset_steps(ds: Dataset, rt: ResolvedTime, store, mgr, outputs: dict,
                      uploads: Optional[dict] = None,
                      values: Optional[dict] = None,
                      params: Optional[dict] = None) -> DatasetTrace:
    """Run one dataset keeping the frame after every transform.

    Same query, same transforms, same order as :func:`run_dataset` — only the
    intermediate frames are kept, so what you inspect step by step is what the
    dashboard will actually show.
    """
    qsql, df, error = _fetch(ds, rt, store, mgr, outputs, uploads, params)
    if error is not None:
        return DatasetTrace(ds.name, qsql, error)
    return DatasetTrace(ds.name, qsql, None,
                        transform_steps(df, _shaped(ds, values)))


def run_datasets(dashboard: Dashboard, store, mgr, today: date,
                 uploads: Optional[dict] = None,
                 chosen: Optional[dict] = None) -> dict[str, DatasetResult]:
    """Run every dataset in declaration order, returning results by name.

    Successful (transformed, capped) frames are fed forward as ``outputs`` so a
    later dataset can reference an earlier one with ``{{name.column}}`` — that
    query-level reference is unrelated to parameters and unaffected by them.

    Parameters are different: a ``column`` parameter's choices must come from a
    dataset's frame *as fetched*, before that dataset's own transforms run —
    otherwise the very filter the parameter drives has already narrowed the
    frame down to the one value it left in. So a second dict, ``raw``, is kept
    of fetched-not-yet-transformed frames, and it is against that dict that
    parameter values and choices are resolved, dataset by dataset as each is
    fetched. A dataset can only see the raw frame of one declared before it —
    the same ordering rule ``{{name.column}}`` already lives by, since a KDB
    dataset's query can itself depend on an earlier dataset's finished output,
    so every fetch cannot happen in a first pass before any transform runs.
    """
    dashboard_time = resolve(dashboard.time_context, today)
    outputs: dict[str, pd.DataFrame] = {}
    raw: dict[str, pd.DataFrame] = {}
    results: dict[str, DatasetResult] = {}
    for ds in dashboard.datasets:
        rt = effective_time(ds, dashboard_time, today)
        # Before the fetch, because these go *into* the query — and from the raw
        # frames known so far, which is the same ordering rule a `column`
        # parameter already lives by: a dataset can only read one declared
        # before it.
        literals = params_mod.q_values(dashboard.parameters, chosen or {}, raw)
        qsql, df, error = _fetch(ds, rt, store, mgr, outputs, uploads, literals)
        raw[ds.name] = df
        values = params_mod.resolve_values(dashboard.parameters, chosen or {}, raw)
        choices = {p.name: params_mod.choices_for(p, raw)
                  for p in dashboard.parameters}
        if error is not None:
            res = DatasetResult(ds.name, None, qsql, error,
                                waiting=ds.source == "file"
                                and error.startswith("waiting for"),
                                choices=choices)
        else:
            res = _apply(ds, qsql, df, values)
            res.choices = choices
        results[ds.name] = res
        if res.df is not None:
            outputs[ds.name] = res.df
    return results


def trace_datasets(dashboard: Dashboard, store, mgr, today: date,
                   uploads: Optional[dict] = None,
                   chosen: Optional[dict] = None) -> dict[str, DatasetTrace]:
    """Run every dataset step by step, in declaration order, results by name.

    Like :func:`run_datasets`, a dataset's finished frame is fed forward so a
    later one can still reference it with ``{{name.column}}``, and parameter
    values/choices are resolved against each dataset's raw (pre-transform)
    frame as it is fetched — see that function's docstring for why.
    """
    dashboard_time = resolve(dashboard.time_context, today)
    outputs: dict[str, pd.DataFrame] = {}
    raw: dict[str, pd.DataFrame] = {}
    traces: dict[str, DatasetTrace] = {}
    for ds in dashboard.datasets:
        rt = effective_time(ds, dashboard_time, today)
        # Before the fetch, because these go *into* the query — and from the raw
        # frames known so far, which is the same ordering rule a `column`
        # parameter already lives by: a dataset can only read one declared
        # before it.
        literals = params_mod.q_values(dashboard.parameters, chosen or {}, raw)
        qsql, df, error = _fetch(ds, rt, store, mgr, outputs, uploads, literals)
        raw[ds.name] = df
        values = params_mod.resolve_values(dashboard.parameters, chosen or {}, raw)
        choices = {p.name: params_mod.choices_for(p, raw)
                  for p in dashboard.parameters}
        if error is not None:
            trace = DatasetTrace(ds.name, qsql, error, choices=choices)
        else:
            trace = DatasetTrace(ds.name, qsql, None,
                                 transform_steps(df, _shaped(ds, values)),
                                 choices=choices)
        traces[ds.name] = trace
        if trace.df is not None:
            outputs[ds.name] = trace.df
    return traces
