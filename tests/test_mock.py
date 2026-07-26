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
    assert names == {"kdp_demo", "orders_demo", "orders_hdb_demo",
                     "refdata_demo"}
    assert all(s.host == "demo" for s in specs)
    kdp = next(s for s in specs if s.name == "kdp_demo")
    assert "QATT" in kdp.schema


# --- historical demo server -------------------------------------------------

def test_hdb_tables_have_a_date_column():
    from kdbmonitor.core.mock import MockHdbClient
    assert "date" in MockHdbClient().query("select from target").columns


def test_hdb_honours_a_date_within_filter():
    from datetime import date, timedelta
    from kdbmonitor.core.mock import MockHdbClient
    today = date.today()
    lo = (today - timedelta(days=2)).strftime("%Y.%m.%d")
    hi = today.strftime("%Y.%m.%d")
    df = MockHdbClient().query(f"select from target where date within ({lo};{hi})")
    assert not df.empty
    assert df["date"].min() >= today - timedelta(days=2)
    assert df["date"].max() <= today


def test_hdb_range_outside_the_generated_window_is_empty():
    from kdbmonitor.core.mock import MockHdbClient
    assert MockHdbClient().query(
        "select from target where date within (1999.01.01;1999.01.31)").empty


def test_hdb_cols_query_includes_date():
    from kdbmonitor.core.mock import MockHdbClient
    assert MockHdbClient().query("cols `target")["c"].tolist()[0] == "date"


def test_demo_specs_include_a_paired_historical_server():
    from kdbmonitor.core.mock import demo_connection_specs
    by_name = {c.name: c for c in demo_connection_specs()}
    assert by_name["orders_demo"].env == "orders"
    assert by_name["orders_demo"].kind == "realtime"
    assert by_name["orders_hdb_demo"].env == "orders"
    assert by_name["orders_hdb_demo"].kind == "historical"


def test_connection_manager_routes_by_kind():
    from kdbmonitor.core.client import ConnectionManager
    from kdbmonitor.core.mock import MockHdbClient, MockKdbClient, demo_connection_specs
    mgr = ConnectionManager()
    by_name = {c.name: c for c in demo_connection_specs()}
    assert isinstance(mgr.get(by_name["orders_demo"]), MockKdbClient)
    assert isinstance(mgr.get(by_name["orders_hdb_demo"]), MockHdbClient)


def test_the_demo_set_covers_all_three_connection_kinds():
    from kdbmonitor.core.models import CONNECTION_KINDS
    kinds = {c.kind for c in demo_connection_specs()}
    assert kinds == set(CONNECTION_KINDS)


def test_the_market_data_demo_server_serves_instruments():
    by_name = {c.name: c for c in demo_connection_specs()}
    ref = by_name["refdata_demo"]
    assert ref.kind == "marketdata"
    assert "instrument" in ref.schema


def test_reference_data_does_not_change_with_the_clock():
    from kdbmonitor.core.mock import MockKdbClient
    first = MockKdbClient().query("select from instrument")
    second = MockKdbClient().query("select from instrument")
    assert first.equals(second)
    assert "sector" in first.columns


def test_the_linked_pair_shares_one_environment():
    by_name = {c.name: c for c in demo_connection_specs()}
    assert by_name["orders_demo"].env == by_name["orders_hdb_demo"].env
    assert by_name["refdata_demo"].env != by_name["orders_demo"].env
