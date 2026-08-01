"""Narrowing a table by its columns, the way a spreadsheet does.

Separate from the search box beside it because they answer different questions.
A search is a lookup: somebody has an order number in front of them and does not
yet know which column it sits in, so one box across everything is right. A
filter is a narrowing: side is BUY *and* quantity is over a hundred thousand,
two conditions on two named columns at once, which one box cannot express at
all.

Nothing here imports Streamlit. What a control looks like is the UI's business;
which rows survive is decided here, where it can be tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

# Above this many distinct values a tick-list stops being a way to choose and
# starts being a second table to read, so the column offers a contains box
# instead. A spreadsheet does the same thing from the other direction: its list
# grows a search box once it is too long to scan.
MAX_PICKABLE = 200


@dataclass
class ColumnFilter:
    """One column's condition. Every field empty means the column is not filtered."""
    values: list = field(default_factory=list)   # keep only these
    contains: str = ""                           # or: text mentioning this
    minimum: Optional[Any] = None                # numbers and dates
    maximum: Optional[Any] = None

    @property
    def active(self) -> bool:
        return bool(self.values or self.contains.strip()
                    or self.minimum is not None or self.maximum is not None)


def kind_of(column: pd.Series) -> str:
    """How this column should be narrowed: ``pick``, ``range`` or ``contains``.

    Decided from what is in it rather than from its declared type, because a
    column of twelve venue names and a column of nine thousand order ids are
    both text and want quite different controls.
    """
    if pd.api.types.is_numeric_dtype(column) and not \
            pd.api.types.is_bool_dtype(column):
        return "range"
    if pd.api.types.is_datetime64_any_dtype(column):
        return "range"
    return "pick" if column.nunique(dropna=True) <= MAX_PICKABLE else "contains"


def options_for(column: pd.Series) -> list:
    """What a tick-list should offer, in the order the column presents them."""
    return list(dict.fromkeys(column.dropna().tolist()))


def bounds_of(column: pd.Series) -> tuple[Any, Any]:
    """The span a range control should cover, or ``(None, None)`` if empty."""
    finite = column.dropna()
    if finite.empty:
        return None, None
    return finite.min(), finite.max()


def apply(frame: pd.DataFrame,
          filters: dict[str, ColumnFilter]) -> pd.DataFrame:
    """The rows meeting every active condition.

    Conditions combine with *and*, which is what a reader building two of them
    means by it: narrowing twice should narrow, not widen. A condition naming a
    column that is no longer there is ignored rather than raising — a dashboard
    can be edited under a filter somebody left set.

    Nulls never satisfy a condition. A row with no side is not a BUY, and
    including it in a filtered view would put rows in front of somebody that
    they explicitly asked to be rid of.
    """
    if frame is None or frame.empty or not filters:
        return frame

    keep = pd.Series(True, index=frame.index)
    for name, spec in filters.items():
        if name not in frame.columns or not spec.active:
            continue
        column = frame[name]

        if spec.values:
            keep &= column.isin(spec.values)
        if spec.contains.strip():
            keep &= column.astype(str).str.casefold().str.contains(
                spec.contains.strip().casefold(), regex=False, na=False)
        if spec.minimum is not None:
            keep &= column.notna() & (column >= spec.minimum)
        if spec.maximum is not None:
            keep &= column.notna() & (column <= spec.maximum)
    return frame[keep]


def summary(frame: pd.DataFrame, filters: dict[str, ColumnFilter]) -> str:
    """What is currently being narrowed on, for a reader who cannot see the
    controls — a filtered table that does not say so is a table telling a lie
    by omission.
    """
    parts: list[str] = []
    for name, spec in filters.items():
        if frame is not None and name not in getattr(frame, "columns", []):
            continue
        if not spec.active:
            continue
        if spec.values:
            shown = ", ".join(str(v) for v in spec.values[:3])
            more = f" +{len(spec.values) - 3}" if len(spec.values) > 3 else ""
            parts.append(f"{name}: {shown}{more}")
        elif spec.contains.strip():
            parts.append(f"{name} contains '{spec.contains.strip()}'")
        else:
            low = "" if spec.minimum is None else str(spec.minimum)
            high = "" if spec.maximum is None else str(spec.maximum)
            parts.append(f"{name}: {low}–{high}".replace(": –", ": up to ")
                         if low == "" else f"{name}: {low}–{high}")
    return " · ".join(parts)


def active_count(filters: dict[str, ColumnFilter]) -> int:
    return sum(1 for f in (filters or {}).values() if f.active)
