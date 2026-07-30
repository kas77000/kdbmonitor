"""Two databases registered against the same slot of one environment.

An environment holds one server per kind. A second registered against the same
pair did not collide loudly — it stopped existing: absent from every dropdown,
never queried, with nothing said anywhere.
"""
import pytest

from kdbmonitor.core.models import Connection
from kdbmonitor.core.storage import Storage


@pytest.fixture()
def store(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    return s


def _add(store, name, kind="realtime", env="PROD"):
    store.add_connection(Connection(id=None, name=name, host=name, port=1,
                                    kind=kind, env=env))


def test_a_second_server_in_one_slot_is_reported(store):
    _add(store, "orders-rdb")
    _add(store, "quotes-rdb")
    assert store.duplicate_slots() == [("PROD", "realtime",
                                        ["orders-rdb", "quotes-rdb"])]


def test_a_tidy_setup_reports_nothing(store):
    _add(store, "orders-rdb")
    _add(store, "orders-hdb", kind="historical")
    _add(store, "quotes-rdb", env="QUOTES")
    assert store.duplicate_slots() == []


def test_the_survivor_is_the_same_one_every_time(store):
    """It used to be whichever the database returned last, so which server was
    reachable could change when an unrelated one was renamed."""
    _add(store, "zeta")
    _add(store, "alpha")
    assert store.list_environments()["PROD"]["realtime"].name == "alpha"


def test_a_duplicate_does_not_take_the_other_kinds_with_it(store):
    _add(store, "orders-rdb")
    _add(store, "quotes-rdb")
    _add(store, "orders-hdb", kind="historical")
    assert store.list_environments()["PROD"]["historical"].name == "orders-hdb"


def test_several_environments_each_report_their_own(store):
    _add(store, "a"), _add(store, "b")
    _add(store, "c", env="UAT"), _add(store, "d", env="UAT")
    assert [e for e, _, _ in store.duplicate_slots()] == ["PROD", "UAT"]
