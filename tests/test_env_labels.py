"""How an environment is named where somebody has to pick one.

An environment name is a label somebody chose. A dashboard only ever showed
that, so a connection registered as EQUITY DATA under an environment called MD
appeared in the picker as "MD" — an alias standing for an alias, and picking
the right one meant remembering which was which.
"""
from kdbmonitor.core.models import Connection
from kdbmonitor.ui.dashboard_editor import env_label


def _conn(name, kind="realtime", env="MD"):
    return Connection(id=None, name=name, host=name, port=1, kind=kind, env=env)


def test_an_environment_is_named_by_the_server_it_holds():
    pair = {"realtime": None, "historical": _conn("EQUITY DATA", "historical"),
            "marketdata": None}
    assert env_label("MD", pair) == "MD — EQUITY DATA"


def test_a_pair_names_both_sides():
    pair = {"realtime": _conn("order-rdb"),
            "historical": _conn("order-hdb", "historical"),
            "marketdata": None}
    assert env_label("orders", pair) == "orders — order-rdb / order-hdb"


def test_the_sides_are_named_in_a_settled_order():
    """Real-time, then historical, then market data — not whatever the
    dictionary happened to iterate."""
    pair = {"marketdata": _conn("ref", "marketdata"),
            "historical": _conn("hdb", "historical"),
            "realtime": _conn("rdb")}
    assert env_label("e", pair) == "e — rdb / hdb / ref"


def test_an_environment_named_after_its_only_server_is_not_repeated():
    """A connection with no environment forms one named after itself, and
    "refdata — refdata" says nothing twice."""
    pair = {"realtime": _conn("refdata"), "historical": None,
            "marketdata": None}
    assert env_label("refdata", pair) == "refdata"


def test_an_empty_environment_is_just_its_name():
    assert env_label("ghost", {}) == "ghost"
    assert env_label("ghost", {"realtime": None, "historical": None,
                               "marketdata": None}) == "ghost"


def test_a_missing_pair_does_not_raise():
    assert env_label("ghost", None) == "ghost"
