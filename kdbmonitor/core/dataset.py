"""Running a dashboard's datasets.

One dataset -> one DataFrame. Failures are *captured*, never raised: a dead
server must degrade one panel, not blank the page. The historical date clause is
built here rather than stored in the dataset's filters, which is what makes
flipping a dataset between environments lossless.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

from kdbmonitor.core.chain import filter_clause, substitute_refs
from kdbmonitor.core.dashboard_models import Dashboard, Dataset
from kdbmonitor.core.models import Connection
from kdbmonitor.core.timectx import (
    ResolvedTime, date_clause, has_date_constraint, resolve, substitute_dates,
    unresolved_date_refs,
)
from kdbmonitor.core.transform import apply_transforms


@dataclass
class DatasetResult:
    name: str
    df: Optional[pd.DataFrame]      # None when the dataset failed
    qsql: str                       # the query that was (or would have been) sent
    error: Optional[str]
    row_count: int = 0              # true size before max_rows capping
    truncated: bool = False


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


def substitute_connections(qsql: str, store, rt: ResolvedTime) -> str:
    r"""Resolve every ``{{conn:ENV}}`` to that environment's ``\`:host:port``.

    The environment is resolved under the same time context as the dataset, so a
    historical dashboard federates to the historical twin and a market-data env
    to its (always real-time) server. An unknown env or a missing side raises,
    which ``run_dataset`` captures as the panel's error.
    """
    def repl(m: re.Match) -> str:
        env = m.group(1).strip()
        conn, _ = resolve_target(store, env, rt)
        return f"`:{conn.host}:{conn.port}"

    return _CONN_REF.sub(repl, qsql)


def build_qsql(ds: Dataset, rt: ResolvedTime, outputs: dict, store=None) -> str:
    """The query for this dataset, with dates, dataset refs and cross-process
    connection handles resolved. ``store`` is needed only to resolve
    ``{{conn:ENV}}`` handles; omit it and those tokens are left untouched."""
    if ds.mode == "raw":
        q = substitute_refs(substitute_dates(ds.raw_qsql or "", rt), outputs)
        return substitute_connections(q, store, rt) if store is not None else q

    clauses: list[str] = []
    if rt.mode == "historical":
        clauses.append(date_clause(rt))       # first, so kdb+ prunes partitions
    clauses += [filter_clause(f) for f in ds.filters]
    base = f"select from {ds.table}"
    q = base if not clauses else f"{base} where {', '.join(clauses)}"
    return substitute_refs(q, outputs)


def run_dataset(ds: Dataset, rt: ResolvedTime, store, mgr,
                outputs: dict) -> DatasetResult:
    """Run one dataset, capturing any failure as an error on the result."""
    # Resolve first: the date guard must apply to the server actually queried,
    # and a market-data environment is never historical.
    try:
        conn, effective = resolve_target(store, ds.env, rt)
    except Exception as exc:      # noqa: BLE001 - a broken panel, not a page
        return DatasetResult(ds.name, None, "", str(exc))

    if effective.mode == "historical" and ds.mode == "raw" \
            and not has_date_constraint(ds.raw_qsql or ""):
        return DatasetResult(
            ds.name, None, ds.raw_qsql or "",
            "historical query must constrain 'date' — add a "
            "date within ({{date_from}};{{date_to}}) clause")

    qsql = ""
    try:
        qsql = build_qsql(ds, effective, outputs, store)
        if unresolved_date_refs(qsql):
            # Only reachable in real-time, where nothing fills them. Sending
            # '{{date_from}}' to KDB would be a baffling parse error.
            return DatasetResult(
                ds.name, None, qsql,
                "this query uses {{date_from}}/{{date_to}} outside a "
                "{{#historical}}…{{/historical}} block, so it cannot run in "
                "real-time — wrap the date predicate in that block")
        df = mgr.get(conn).query(qsql)
        df = apply_transforms(df, ds.transforms)
    except Exception as exc:      # noqa: BLE001 - a broken panel, not a broken page
        return DatasetResult(ds.name, None, qsql, str(exc))

    total = len(df)
    capped = df.head(ds.max_rows).reset_index(drop=True)
    return DatasetResult(ds.name, capped, qsql, None,
                         row_count=total, truncated=total > len(capped))


def run_datasets(dashboard: Dashboard, store, mgr,
                 today: date) -> dict[str, DatasetResult]:
    """Run every dataset in declaration order, returning results by name.

    Successful frames are fed forward so a later dataset can reference an earlier
    one with ``{{name.column}}``.
    """
    dashboard_time = resolve(dashboard.time_context, today)
    outputs: dict[str, pd.DataFrame] = {}
    results: dict[str, DatasetResult] = {}
    for ds in dashboard.datasets:
        rt = effective_time(ds, dashboard_time, today)
        res = run_dataset(ds, rt, store, mgr, outputs)
        results[ds.name] = res
        if res.df is not None:
            outputs[ds.name] = res.df
    return results
