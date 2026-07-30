"""Reaching a named database from a raw query.

An environment holds one server per kind, but a desk's environment holds many
databases — an order book, a quote store, a reference set. A query that has to
open one of them in particular needs a way to say which, and until now
{{conn:ENV}} could only name the environment and infer the side from whatever
period the dataset happened to be running under.
"""
import pytest

from kdbmonitor.core.dataset import resolve_handle, substitute_connections
from kdbmonitor.core.models import Connection
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.timectx import ResolvedTime
from datetime import date

RT = ResolvedTime("realtime", None, None)
HIST = ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 30))


@pytest.fixture()
def store(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="oms-rdb", host="oms-rdb",
                                port=1, kind="realtime", env="OMS"))
    s.add_connection(Connection(id=None, name="oms-hdb", host="oms-hdb",
                                port=2, kind="historical", env="OMS"))
    s.add_connection(Connection(id=None, name="quotes-rdb", host="quotes-rdb",
                                port=3, kind="realtime", env="QUOTES"))
    return s


# --- the form that already existed ------------------------------------------

def test_an_environment_still_resolves_by_the_period_in_force(store):
    assert resolve_handle(store, "OMS", RT).host == "oms-rdb"
    assert resolve_handle(store, "OMS", HIST).host == "oms-hdb"


# --- naming the side --------------------------------------------------------

def test_a_side_can_be_named_against_the_period_in_force(store):
    """A live query that wants yesterday's data says so plainly."""
    assert resolve_handle(store, "OMS:historical", RT).host == "oms-hdb"
    assert resolve_handle(store, "OMS:realtime", HIST).host == "oms-rdb"


def test_naming_a_side_that_is_not_there_says_which(store):
    with pytest.raises(ValueError, match="historical"):
        resolve_handle(store, "QUOTES:historical", RT)


def test_naming_a_kind_that_does_not_exist_says_so(store):
    with pytest.raises(ValueError, match="sasquatch"):
        resolve_handle(store, "OMS:sasquatch", RT)


def test_naming_a_side_of_an_unknown_environment_says_so(store):
    with pytest.raises(ValueError, match="unknown environment"):
        resolve_handle(store, "GHOST:realtime", RT)


# --- naming one database ----------------------------------------------------

def test_a_connection_can_be_named_outright(store):
    assert resolve_handle(store, "quotes-rdb", RT).port == 3


def test_an_environment_name_wins_over_a_connection_of_the_same_name(store):
    """Otherwise adding a connection could quietly redirect existing queries."""
    store.add_connection(Connection(id=None, name="OMS", host="decoy", port=9,
                                    kind="realtime", env="DECOY"))
    assert resolve_handle(store, "OMS", RT).host == "oms-rdb"


def test_an_unknown_name_lists_what_there_is(store):
    with pytest.raises(ValueError, match="OMS"):
        resolve_handle(store, "nowhere", RT)


# --- in a query -------------------------------------------------------------

def test_a_query_opens_the_handle_it_named(store):
    q = 'h: hopen {{conn:quotes-rdb}}; h "select from quotes"'
    assert substitute_connections(q, store, RT) == \
        'h: hopen `:quotes-rdb:3; h "select from quotes"'


def test_a_query_can_open_two_different_databases(store):
    q = "{{conn:OMS:historical}} {{conn:QUOTES}}"
    assert substitute_connections(q, store, RT) == "`:oms-hdb:2 `:quotes-rdb:3"


def test_whitespace_inside_a_handle_is_tolerated(store):
    assert substitute_connections("{{conn: OMS:historical }}", store, RT) \
        == "`:oms-hdb:2"
