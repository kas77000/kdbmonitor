# kdbmonitor/core/schema.py
from __future__ import annotations


def _first_col(result) -> list:
    """Return the first column of a query result as a plain list.

    A q vector (e.g. ``tables[]`` or ``cols `t``) comes back from pykx's
    ``.pd()`` as a 1-D pandas Series, while a q table comes back as a
    DataFrame. ``Series.iloc[:, 0]`` raises "Too many indexers", so pick the
    accessor by dimensionality and fall back to plain iterables.
    """
    ndim = getattr(result, "ndim", None)
    if ndim == 2:  # DataFrame
        return result.iloc[:, 0].tolist()
    if hasattr(result, "tolist"):  # Series / ndarray
        return result.tolist()
    return list(result)


def introspect(client) -> dict[str, list[str]]:
    tables = _first_col(client.query("tables[]"))
    schema: dict[str, list[str]] = {}
    for t in tables:
        schema[t] = _first_col(client.query(f"cols `{t}"))
    return schema
