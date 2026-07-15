# kdbmonitor/core/schema.py
from __future__ import annotations


def introspect(client) -> dict[str, list[str]]:
    tables_df = client.query("tables[]")
    tables = tables_df.iloc[:, 0].tolist()
    schema: dict[str, list[str]] = {}
    for t in tables:
        cols_df = client.query(f"cols `{t}")
        schema[t] = cols_df.iloc[:, 0].tolist()
    return schema
