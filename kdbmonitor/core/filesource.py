"""An uploaded file -> a validated DataFrame, or a refusal saying why.

Nothing here knows about Streamlit or about dashboards: it takes bytes and a
``FileShape`` and gives back a ``FileLoad``. Every assumption about the file
format lives in :func:`read_grid` and nowhere else, so widening the format later
is a change to one function.

The file's structure is never guessed. The header line is declared by whoever
built the dashboard, and a file whose header is somewhere else is refused rather
than searched for: an app that quietly decides a file is close enough does not
fail when it is wrong, it reports the wrong thing.
"""
from __future__ import annotations

import csv
import io


def read_grid(data: bytes) -> list[list[str]]:
    """The file as a rectangular grid of strings, exactly as written.

    Padded to the widest row, because everything downstream addresses a cell by
    ``(row, col)`` and a ragged grid makes that address a lie. ``utf-8-sig``
    strips the byte-order mark Excel writes, which otherwise glues itself to the
    first header and makes that column unmatchable by name.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("this file is not UTF-8 text") from exc
    rows = [row for row in csv.reader(io.StringIO(text))]
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]
