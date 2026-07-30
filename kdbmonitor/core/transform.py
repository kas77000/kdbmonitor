"""Guided post-query shaping, applied in order to a dataset's frame.

Deliberately a small closed catalogue rather than arbitrary code: dashboards are
stored in the DB and shared between users, so a transform must be data, not a
Python snippet.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from kdbmonitor.core.summaries import transform_summary

AGG_FUNCS = ("count", "nunique", "sum", "mean", "min", "max")

_OPS: dict[str, Callable[[Any, Any], Any]] = {
    "=": operator.eq, "==": operator.eq, "!=": operator.ne, "<>": operator.ne,
    "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}


def _need(df: pd.DataFrame, column: str, kind: str) -> None:
    if column not in df.columns:
        raise ValueError(f"{kind}: no column '{column}' (have: "
                         f"{', '.join(map(str, df.columns))})")


def _no_infinities(value: Any) -> Any:
    """Turn ±inf into a null, because an infinity is not a figure to report.

    Dividing by zero is the ordinary way to get one — a completion percentage
    against a total that came back zero — and from there pandas will format it,
    colour it against a threshold and plot it like any other number. "inf%"
    reads as a measurement rather than as arithmetic that had no answer, and it
    poisons every aggregate downstream: one infinite market makes the mean over
    all of them infinite too.

    A quantity we could not compute is a gap, which is the same answer a kdb
    null already gets. Note this only removes results that are *not numbers* — a
    negative is a number, and a wrong-looking one is left alone to be seen.
    """
    if isinstance(value, pd.Series) and value.dtype.kind == "f":
        return value.mask(np.isinf(value))
    return value


def _derive(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    column, kind = p["column"], p.get("kind", "arithmetic")
    if kind == "arithmetic":
        # pandas' own expression engine: column names only, no attribute access.
        df[column] = _no_infinities(df.eval(p["expr"])) if len(df) \
            else pd.Series(dtype="float64")
        return df
    if kind == "suffix_map":
        source, mapping = p["source"], p.get("mapping", {})
        default = p.get("default", "Unknown")
        # How many trailing characters make the suffix. Without one, fall back to
        # the older rule of splitting on the last dot, so maps configured before
        # the length existed keep working.
        length = int(p.get("length") or 0)
        _need(df, source, "derive")

        def to_label(v: Any) -> str:
            if not isinstance(v, str):
                return default
            if length > 0:
                return mapping.get(v[-length:], default) if len(v) >= length \
                    else default
            if "." in v:
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


# Row-over-row arithmetic, and the reason it is a transform of its own rather
# than an expression: it has to be able to partition. A volume profile stacks
# every instrument in one frame, so the difference between consecutive rows
# walks out of one instrument and into the next at every boundary — which does
# not raise, it just reports a share of -1.0 and makes that instrument's shares
# sum to nothing like one.
_WINDOW_OPS = ("diff", "cumsum", "shift", "rolling_mean", "rolling_sum",
               "row_number", "pct_of_total", "rank")


def _window(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    op = p.get("op", "diff")
    if "as" not in p:
        raise ValueError("window: no 'as' column name given")
    target = p["as"]
    keys = list(p.get("partition_by") or [])
    # `p.get("periods") or 1` would silently turn an explicit 0 into 1 — 0 is
    # falsy — which is wrong for shift/diff, where periods=0 has a real,
    # different meaning (compare a row with itself) from periods=1.
    periods = int(p["periods"]) if p.get("periods") is not None else 1
    if op not in _WINDOW_OPS:
        raise ValueError(f"unknown window op: {op} "
                         f"(have: {', '.join(_WINDOW_OPS)})")
    for k in keys:
        _need(df, k, "window")

    if op == "row_number":
        df[target] = (pd.Series(dtype="int64") if df.empty
                      else (df.groupby(keys, dropna=False).cumcount() if keys
                            else pd.Series(range(len(df)), index=df.index)))
        return df

    column = p["column"]
    _need(df, column, "window")
    if df.empty:
        df[target] = pd.Series(dtype=df[column].dtype)
        return df

    def run(series: pd.Series) -> pd.Series:
        if op == "diff":
            return series.diff(periods)
        if op == "cumsum":
            return series.cumsum()
        if op == "shift":
            return series.shift(periods)
        if op == "rolling_mean":
            return series.rolling(periods).mean()
        if op == "rolling_sum":
            return series.rolling(periods).sum()
        if op == "pct_of_total":
            total = series.sum()
            # A total of zero is how an infinity gets into a report — the same
            # divide-by-zero _no_infinities exists for. A share of nothing is
            # a gap, not a number, so it gets the null the codebase already
            # uses for that rather than inf.
            return series / total if total else series * float("nan")
        return series.rank(method="min")

    # transform() keeps every value where it was: these files are in session
    # order, and a profile resorted around midnight is wrong.
    df[target] = (df.groupby(keys, dropna=False)[column].transform(run) if keys
                  else run(df[column]))
    return df


_KINDS: dict[str, Callable[[pd.DataFrame, dict], pd.DataFrame]] = {
    "derive": _derive, "filter": _filter, "groupby": _groupby,
    "sort": _sort, "limit": _limit, "rename": _rename, "window": _window,
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


# --- step-by-step evaluation ------------------------------------------------

@dataclass
class Step:
    """The state of a dataset's frame at one stage of its pipeline.

    Step 0 is what the query returned; step N is the frame after the Nth
    transform. Keeping every stage lets the editor show what each configured
    action actually did, rather than only the end result.
    """
    index: int                   # 0 = the query result
    kind: str                    # "query", or the transform's kind
    label: str                   # human description of this stage
    df: Optional[pd.DataFrame]   # None when this step failed
    error: Optional[str] = None
    rows_before: Optional[int] = None      # None for the query step
    added: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return 0 if self.df is None else len(self.df)

    @property
    def row_delta(self) -> Optional[int]:
        """Rows gained (+) or lost (-) at this step, None where it means nothing."""
        if self.df is None or self.rows_before is None:
            return None
        return self.rows - self.rows_before

    @property
    def columns(self) -> list[str]:
        return [] if self.df is None else [str(c) for c in self.df.columns]


def transform_steps(df: pd.DataFrame, transforms) -> list[Step]:
    """Apply transforms one at a time, keeping the frame after each.

    Stops at the first failure and records it on that step: the steps before it
    are exactly the ones that succeeded, which is what makes a broken pipeline
    diagnosable — you can see the frame the failing transform was handed.
    """
    steps = [Step(index=0, kind="query", label="Query result", df=df.copy())]
    current = df
    for i, t in enumerate(transforms, start=1):
        before_columns, before_rows = list(current.columns), len(current)
        label = f"{i}. {transform_summary(t)}"
        try:
            nxt = apply_transforms(current, [t])
        except Exception as exc:      # noqa: BLE001 - reported, not raised
            steps.append(Step(index=i, kind=t.kind, label=label, df=None,
                              error=str(exc), rows_before=before_rows))
            break
        steps.append(Step(
            index=i, kind=t.kind, label=label, df=nxt, rows_before=before_rows,
            added=[str(c) for c in nxt.columns if c not in before_columns],
            dropped=[str(c) for c in before_columns if c not in nxt.columns]))
        current = nxt
    return steps
