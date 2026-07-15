from kdbmonitor.core.mock import MockKdbClient, demo_connection_specs
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.models import Connection


def test_tables_and_cols():
    c = MockKdbClient()
    tables = list(c.query("tables[]")["t"])
    assert {"QATT", "target", "work_order", "target_state"} <= set(tables)
    assert "bid" in list(c.query("cols `QATT")["c"])
    assert list(c.query("cols `nope")["c"]) == []


def test_select_returns_rows():
    c = MockKdbClient()
    df = c.query("select from QATT")
    assert {"sym", "bid", "ask", "volume"} <= set(df.columns)
    assert len(df) == 5
    assert c.query("select from unknown_table").empty


def test_sym_filter():
    c = MockKdbClient()
    df = c.query("select from QATT where sym in `AAPL`MSFT")
    assert set(df["sym"]) == {"AAPL", "MSFT"}
    one = c.query("select from target where sym in enlist `AAPL")
    assert set(one["sym"]) == {"AAPL"}


def test_manager_routes_demo_host_to_mock():
    def boom(host, port):
        raise AssertionError("real client should not be built for demo host")

    mgr = ConnectionManager(client_factory=boom)
    client = mgr.get(Connection(id=1, name="d", host="demo", port=1))
    assert "QATT" in list(client.query("tables[]")["t"])
    # cached: second get returns same instance
    assert mgr.get(Connection(id=1, name="d", host="demo", port=1)) is client


def test_demo_connection_specs():
    specs = demo_connection_specs()
    names = {s.name for s in specs}
    assert names == {"kdp_demo", "orders_demo"}
    assert all(s.host == "demo" for s in specs)
    kdp = next(s for s in specs if s.name == "kdp_demo")
    assert "QATT" in kdp.schema
