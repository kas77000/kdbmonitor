"""Pure helpers for exporting/copying a result table. Streamlit-free, testable."""
from __future__ import annotations

import io
import re
from datetime import datetime

import pandas as pd

from kdbmonitor.core.qfmt import format_q_list


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# Matches core.dashpdf.pdf_filename's own inline slug: lowercase, every run of
# non-alphanumerics becomes one underscore, and the ends are trimmed rather
# than left to print a leading or trailing one. Kept here rather than shared,
# because reaching into dashpdf for one regex would pull in its plotting and
# rendering imports for a filename builder that needs neither.
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


# A parameter value this long has stopped being a filename component and
# started being the whole path — Windows alone caps a full path at 260
# characters, and a dashboard can have several parameters each feeding one.
_MAX_CHOSEN_CHARS = 24


def export_filename(dashboard_name: str, dataset_name: str, chosen: dict,
                    as_of: datetime, suffix: str) -> str:
    """The filename for one dataset's frame, exported as its widgets see it.

    Two exports of the same dataset taken at different parameter selections
    must not overwrite each other on disk — the reader who compares yesterday's
    download against today's has no way to tell which is which once the name
    is identical. So every chosen value is folded in alongside the dashboard,
    the dataset and the moment the frame was taken, each slugged the same way
    :func:`~kdbmonitor.core.dashpdf.pdf_filename` slugs a dashboard's name: a
    value carrying a path separator or a quote is not a filename component
    that can escape into the name, only underscores where those characters
    were. Values are sorted by parameter name first, so the same selection
    always produces the same name regardless of dict order.
    """
    parts = [_slug(dashboard_name) or "dashboard", _slug(dataset_name) or "dataset"]
    for key in sorted(chosen or {}):
        raw = chosen[key]
        if raw in (None, ""):
            continue
        value = _slug(str(raw))[:_MAX_CHOSEN_CHARS].strip("_")
        if value:
            parts.append(value)
    parts.append(f"{as_of:%Y-%m-%d_%H%M}")
    return "_".join(parts) + f".{suffix}"


def column_as_text(values: list, fmt: str = "lines", distinct: bool = False) -> str:
    """Render a column's values for copying.

    fmt: 'lines' (one per line), 'comma' (comma-separated),
         'q' (a q list literal, e.g. `AAPL`MSFT or 1 2 3).
    """
    vals = list(values)
    if distinct:
        vals = list(dict.fromkeys(vals))  # order-preserving dedupe
    if not vals:
        return ""
    if fmt == "lines":
        return "\n".join(str(v) for v in vals)
    if fmt == "comma":
        return ", ".join(str(v) for v in vals)
    if fmt == "q":
        vtype = "number" if all(_is_number(v) for v in vals) else "symbol"
        return format_q_list(vals, vtype)
    raise ValueError(f"unknown format: {fmt}")


def df_to_excel_bytes(df: pd.DataFrame, sheet_name: str = "result") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return buf.getvalue()


def df_to_csv(df: pd.DataFrame) -> str:
    return df.to_csv(index=False)


def df_to_tsv(df: pd.DataFrame) -> str:
    return df.to_csv(sep="\t", index=False)
