# tests/test_schema.py
import pandas as pd
from kdbmonitor.core.schema import introspect


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
