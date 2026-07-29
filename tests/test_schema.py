# tests/test_schema.py
import pandas as pd
from kdbmonitor.core.schema import introspect

from tests.test_client import pykx_client


class ScriptedClient:
    def __init__(self, mapping):
        self.mapping = mapping
    def query(self, q):
        return self.mapping[q]


def test_introspect_builds_table_column_map():
    client = ScriptedClient({
        "tables[]": pd.DataFrame({"t": ["target", "QATT"]}),
        "cols `target": pd.DataFrame({"c": ["sym", "orderId"]}),
        "cols `QATT": pd.DataFrame({"c": ["sym", "bid", "ask"]}),
    })
    schema = introspect(client)
    assert schema == {"target": ["sym", "orderId"], "QATT": ["sym", "bid", "ask"]}


class SeriesClient:
    """Mimics pykx: q vectors come back as 1-D pandas Series, not DataFrames."""
    def __init__(self, mapping):
        self.mapping = mapping
    def query(self, q):
        return pd.Series(self.mapping[q])


def test_introspect_handles_series_results():
    client = SeriesClient({
        "tables[]": ["target", "QATT"],
        "cols `target": ["sym", "orderId"],
        "cols `QATT": ["sym", "bid", "ask"],
    })
    schema = introspect(client)
    assert schema == {"target": ["sym", "orderId"], "QATT": ["sym", "bid", "ask"]}


def test_introspect_runs_through_the_client_a_real_server_uses():
    """The client above skips everything PyKxClient.query does on the way back,
    including the kdb-null scrub — which iterated columns a q vector has none
    of, so Introspect failed on a live server while this file stayed green."""
    client = pykx_client({
        "tables[]": pd.Series(["target", "QATT"]),
        "cols `target": pd.Series(["sym", "orderId"]),
        "cols `QATT": pd.Series(["sym", "bid", "ask"]),
    })
    schema = introspect(client)
    assert schema == {"target": ["sym", "orderId"], "QATT": ["sym", "bid", "ask"]}
