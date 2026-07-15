# tests/test_client.py
import pandas as pd
from kdbmonitor.core.client import FakeClient, ConnectionManager
from kdbmonitor.core.models import Connection


def test_fake_client_returns_canned():
    df = pd.DataFrame({"sym": ["AAPL"]})
    client = FakeClient({"select from target": df})
    assert client.query("select from target").equals(df)


def test_connection_manager_caches_client(monkeypatch):
    created = []

    class DummyClient:
        def __init__(self, host, port):
            created.append((host, port))
        def query(self, q):
            return pd.DataFrame()

    mgr = ConnectionManager(client_factory=DummyClient)
    conn = Connection(id=1, name="orders", host="h", port=5010)
    c1 = mgr.get(conn)
    c2 = mgr.get(conn)
    assert c1 is c2
    assert created == [("h", 5010)]
