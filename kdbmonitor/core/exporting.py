"""Pure helpers for exporting/copying a result table. Streamlit-free, testable."""
from __future__ import annotations

import io

import pandas as pd

from kdbmonitor.core.qfmt import format_q_list


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


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
