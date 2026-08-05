"""Frames kept back from the server, for queries whose author says they hold still.

Some of what a dashboard or an alert asks for does not change: the instrument
list, the desk's book mapping, a static universe loaded at start of day. Asking
for it on every refresh costs a round trip to be told the same thing again.
Marking such a query keeps its frame here, and every later run reads it from
here instead.

Keyed by the **resolved query** — the text actually sent, and the server it was
sent to — rather than by the dataset or step that asked for it. That is what
makes the cache self-invalidating: edit the query, change a parameter that goes
into it, switch the period, and the key changes with it, so a held frame is
never handed back as the answer to a question nobody asked. It also means two
datasets sending the same query to the same server share one fetch.

Lifetime belongs to the caller, because the two callers are not alike. A
dashboard passes no TTL: it is being looked at, its frames last as long as the
browser session, and there is a control on the page to drop them. An alert
passes its step's own TTL, because an alert runs unattended all day — "once,
forever" there means quietly checking yesterday's universe until somebody
restarts the app.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

# How many frames one cache keeps. A query that varies (a parameter in its text,
# a rolling date) makes a new key every time it changes, so without a bound a
# long session would hold every frame it ever fetched. The oldest goes first:
# a held frame is only ever an optimisation, and losing one costs a re-query.
MAX_ENTRIES = 64


@dataclass
class Entry:
    """One held frame, and when it was fetched."""
    df: pd.DataFrame
    at: datetime


class QueryCache:
    """Held frames by query key, oldest evicted once :data:`MAX_ENTRIES` is passed."""

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self.entries: dict[tuple, Entry] = {}
        self.max_entries = max_entries

    def get(self, key: tuple, now: Optional[datetime] = None,
            ttl: Optional[int] = None) -> Optional[Entry]:
        """The frame held for ``key``, or None if there is none or it has aged out.

        ``ttl`` is in seconds and only means anything with a ``now`` to measure
        against; without one — the dashboard's case — a held frame stands until
        something drops it.
        """
        entry = self.entries.get(key)
        if entry is None:
            return None
        if not ttl or now is None:
            return entry
        try:
            age = (now - entry.at).total_seconds()
        except TypeError:
            # One side tz-aware and the other not: not comparable, so the age is
            # unknown. Treat it as expired — going back to the server is the
            # safe way to be wrong about a cache.
            return None
        return None if age >= ttl else entry

    def put(self, key: tuple, df: pd.DataFrame,
            at: Optional[datetime] = None) -> Entry:
        """Hold ``df`` under ``key``, stamped ``at`` (default: now)."""
        entry = Entry(df=df, at=at or datetime.now())
        self.entries[key] = entry
        self._evict()
        return entry

    def drop(self, key: tuple) -> None:
        self.entries.pop(key, None)

    def clear(self) -> None:
        self.entries.clear()

    def __len__(self) -> int:
        return len(self.entries)

    def _evict(self) -> None:
        while len(self.entries) > self.max_entries:
            oldest = min(self.entries, key=lambda k: self.entries[k].at)
            del self.entries[oldest]
