"""Guided post-query shaping, applied in order to a dataset's frame.

Deliberately a small closed catalogue rather than arbitrary code: dashboards are
stored in the DB and shared between users, so a transform must be data, not a
Python snippet.
"""
from __future__ import annotations

import operator
from typing import Any, Callable

import pandas as pd

AGG_FUNCS = ("count", "nunique", "sum", "mean", "min", "max")

_OPS: dict[str, Callable[[Any, Any], Any]] = {
    "=": operator.eq, "==": operator.eq, "!=": operator.ne, "<>": operator.ne,
    "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}


def _need(df: pd.DataFrame, column: str, kind: str) -> None:
    if column not in df.columns:
        raise ValueError(f"{kind}: no column '{column}' (have: "
                         f"{', '.join(map(str, df.columns))})")


def _derive(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    column, kind = p["column"], p.get("kind", "arithmetic")
    if kind == "arithmetic":
        # pandas' own expression engine: column names only, no attribute access.
        df[column] = df.eval(p["expr"]) if len(df) else pd.Series(dtype="float64")
        return df
    if kind == "suffix_map":
        source, mapping = p["source"], p.get("mapping", {})
        default = p.get("default", "Unknown")
        _need(df, source, "derive")

        def to_label(v: Any) -> str:
            if isinstance(v, str) and "." in v:
                return mapping.get("." + v.rsplit(".", 1)[1], default)
            return default

        df[column] = df[source].map(to_label) if len(df) else pd.Series(dtype=object)
        return df
    raise ValueError(f"unknown derive kind: {kind}")


def _filter(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    column, op, value = p["column"], p["op"], p.get("value")
    _need(df, column, "filter")
    if op == "in":
        return df[df[column].isin(value)].reset_index(drop=True)
    if op not in _OPS:
        raise ValueError(f"unknown filter op: {op}")
    return df[_OPS[op](df[column], value)].reset_index(drop=True)


def _groupby(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    keys, aggs = p["keys"], p["aggs"]
    for k in keys:
        _need(df, k, "groupby")
    for a in aggs:
        _need(df, a["column"], "groupby")
        if a["func"] not in AGG_FUNCS:
            raise ValueError(f"unknown agg func: {a['func']}")
    if df.empty:
        return pd.DataFrame(columns=list(keys) + [a["as"] for a in aggs])
    named = {a["as"]: (a["column"], a["func"]) for a in aggs}
    return df.groupby(list(keys), as_index=False, dropna=False).agg(**named)


def _sort(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    columns = p["columns"]
    for c in columns:
        _need(df, c, "sort")
    return df.sort_values(columns, ascending=p.get("ascending", True)) \
             .reset_index(drop=True)


def _limit(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    return df.head(int(p["n"])).reset_index(drop=True)


def _rename(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    return df.rename(columns=p["mapping"])


_KINDS: dict[str, Callable[[pd.DataFrame, dict], pd.DataFrame]] = {
    "derive": _derive, "filter": _filter, "groupby": _groupby,
    "sort": _sort, "limit": _limit, "rename": _rename,
}


def apply_transforms(df: pd.DataFrame, transforms) -> pd.DataFrame:
    """Apply transforms in order, returning a new frame. Never mutates ``df``."""
    out = df.copy()
    for t in transforms:
        fn = _KINDS.get(t.kind)
        if fn is None:
            raise ValueError(f"unknown transform: {t.kind}")
        out = fn(out, t.params)
    return out
