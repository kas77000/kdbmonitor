# tests/test_storage.py
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.models import Connection


def test_connection_crud():
    store = Storage(":memory:")
    store.init_db()

    cid = store.add_connection(Connection(id=None, name="orders", host="h", port=5010))
    assert isinstance(cid, int)

    conns = store.list_connections()
    assert len(conns) == 1 and conns[0].name == "orders" and conns[0].id == cid

    got = store.get_connection(cid)
    got.schema = {"target": ["sym", "orderId"]}
    got.last_introspected_at = "2026-07-15T10:00:00"
    store.update_connection(got)
    assert store.get_connection(cid).schema == {"target": ["sym", "orderId"]}

    store.delete_connection(cid)
    assert store.list_connections() == []


def test_connection_name_unique():
    store = Storage(":memory:")
    store.init_db()
    store.add_connection(Connection(id=None, name="dup", host="h", port=1))
    import pytest
    with pytest.raises(Exception):
        store.add_connection(Connection(id=None, name="dup", host="h", port=2))
