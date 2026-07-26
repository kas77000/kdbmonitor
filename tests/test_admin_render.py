"""Render the Admin page for real with Streamlit's AppTest.

Every connection row now carries an inline edit form, so the number of widget
keys scales with the number of servers — exactly the shape that produced a
duplicate-key crash in the dashboard editor.
"""
import pytest
from streamlit.testing.v1 import AppTest

from kdbmonitor.core.models import CONNECTION_KINDS, Connection
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui.admin import _env_options, _partner_hint


def _script(db_path: str) -> str:
    return f'''
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import admin

store = Storage(r"{db_path}")
store.init_db()
admin.render(store, ConnectionManager())
'''


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    s = Storage(path)
    s.init_db()
    s.add_connection(Connection(id=None, name="order-rdb", host="h", port=1,
                                kind="realtime", env="orders",
                                schema={"target": ["sym"]}))
    s.add_connection(Connection(id=None, name="order-hdb", host="h", port=2,
                                kind="historical", env="orders"))
    s.add_connection(Connection(id=None, name="refdata", host="h", port=3,
                                kind="marketdata", env="marketdata"))
    return path


def test_admin_renders_with_every_kind_present(db):
    at = AppTest.from_string(_script(db), default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]


def test_admin_widget_keys_are_unique(db):
    at = AppTest.from_string(_script(db), default_timeout=60).run()
    keys = []
    for group in (at.selectbox, at.text_input, at.number_input, at.button,
                  at.checkbox, at.multiselect):
        keys += [el.key for el in group if el.key]
    duplicates = {k for k in keys if keys.count(k) > 1}
    assert not duplicates, f"duplicate keys: {sorted(duplicates)}"


def test_the_kind_picker_offers_all_three_kinds(db):
    at = AppTest.from_string(_script(db), default_timeout=60).run()
    kind_boxes = [b for b in at.selectbox if b.key and b.key.endswith("kind")]
    assert kind_boxes, "no Kind selectbox rendered"
    for box in kind_boxes:
        assert list(box.options) == [
            "Real-time", "Historical", "Market data"], box.options


def test_an_edit_form_exists_for_every_connection(db):
    at = AppTest.from_string(_script(db), default_timeout=60).run()
    saves = [b for b in at.button if b.key and b.key.endswith("_save")]
    assert len(saves) == 3


# --- environment option rules ----------------------------------------------

def test_a_second_realtime_server_cannot_join_a_paired_env(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="rdb", host="h", port=1,
                                kind="realtime", env="orders"))
    assert "orders" not in _env_options(s, "realtime")
    assert "orders" in _env_options(s, "historical")


def test_market_data_cannot_join_a_timeseries_env(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="rdb", host="h", port=1,
                                kind="realtime", env="orders"))
    assert "orders" not in _env_options(s, "marketdata")


def test_a_timeseries_server_cannot_join_a_marketdata_env(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="ref", host="h", port=1,
                                kind="marketdata", env="marketdata"))
    assert "marketdata" not in _env_options(s, "realtime")
    assert "marketdata" not in _env_options(s, "historical")


def test_the_partner_hint_names_the_server_you_would_link_to(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="order-rdb", host="h", port=1,
                                kind="realtime", env="orders"))
    hint = _partner_hint(s, "orders", "historical")
    assert "order-rdb" in hint and "real-time" in hint


def test_the_partner_hint_says_when_there_is_nothing_to_link_yet(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="order-rdb", host="h", port=1,
                                kind="realtime", env="orders"))
    assert "first server" in _partner_hint(s, "orders", "realtime")


# --- renaming ---------------------------------------------------------------

def test_renaming_onto_an_existing_name_is_rejected(tmp_path):
    """The edit form relies on this failing rather than silently merging."""
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="a", host="h", port=1))
    b_id = s.add_connection(Connection(id=None, name="b", host="h", port=2))

    clash = s.get_connection(b_id)
    clash.name = "a"
    with pytest.raises(Exception):
        s.update_connection(clash)


def test_editing_other_fields_persists(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    cid = s.add_connection(Connection(id=None, name="a", host="old", port=1,
                                      kind="realtime", env="orders"))
    c = s.get_connection(cid)
    c.host, c.port, c.kind, c.env = "new", 9, "marketdata", "marketdata"
    s.update_connection(c)

    back = s.get_connection(cid)
    assert (back.host, back.port, back.kind, back.env) == \
        ("new", 9, "marketdata", "marketdata")
