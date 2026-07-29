"""Environments that will only ever have one side, and dashboards built for one.

An environment is normally a real-time server and its historical twin, and a
missing side reads as setup half-done. Some environments are not like that: a
date-partitioned feed with nothing live behind it is historical and nothing
else. Saying so is what stops the app asking for the other half forever — and
what lets a dashboard say, once, which periods it is actually built to offer.

The two are separate claims. A server not yet paired is somebody midway through
setting up; a server *declared* single is a decision. Only the second one makes
a dashboard that promises to switch period wrong.
"""
import pytest

from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Row, Widget, dashboard_from_dict, dashboard_to_dict,
)
from kdbmonitor.core.dataset import resolve_target, standalone_side
from kdbmonitor.core.models import Connection, connection_from_dict, connection_to_dict
from kdbmonitor.core.portability import export_connections_json, import_bundle_json
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.timectx import ResolvedTime, coerce_spec, offers
from kdbmonitor.ui import dashboard_editor as ed
from kdbmonitor.ui import dashboards
from kdbmonitor.ui.admin import _env_options

SCHEMA = {"target": ["sym", "size", "price"]}


def _store(tmp_path, *conns: Connection, name: str = "t.db") -> Storage:
    s = Storage(str(tmp_path / name))
    s.init_db()
    for c in conns:
        s.add_connection(c)
    return s


def _hdb(env="refdata", standalone=True) -> Connection:
    return Connection(id=None, name=f"{env}-hdb", host="h", port=2,
                      kind="historical", env=env, schema=SCHEMA,
                      standalone=standalone)


def _rdb(env="orders", standalone=False) -> Connection:
    return Connection(id=None, name=f"{env}-rdb", host="h", port=1,
                      kind="realtime", env=env, schema=SCHEMA,
                      standalone=standalone)


# --- the flag itself ---------------------------------------------------------

def test_standalone_survives_the_database(tmp_path):
    s = _store(tmp_path, _hdb())
    assert s.get_connection_by_name("refdata-hdb").standalone is True


def test_a_connection_is_paired_until_it_says_otherwise(tmp_path):
    s = _store(tmp_path, _rdb())
    assert s.get_connection_by_name("orders-rdb").standalone is False


def test_a_database_made_before_the_column_gains_it(tmp_path):
    """The migration path: an older kdbmonitor.db opens and reads as paired."""
    path = str(tmp_path / "old.db")
    old = Storage(path)
    old.init_db()
    old.conn.execute("ALTER TABLE connections DROP COLUMN standalone")
    old.conn.execute(
        "INSERT INTO connections(name, host, port, schema_json, kind, env) "
        "VALUES ('legacy','h',1,'{}','historical','refdata')")
    old.conn.commit()

    reopened = Storage(path)
    reopened.init_db()                       # migrates
    assert reopened.get_connection_by_name("legacy").standalone is False


def test_standalone_travels_with_an_export(tmp_path):
    conns, _ = import_bundle_json(export_connections_json([_hdb(), _rdb()]))
    assert {c.name: c.standalone for c in conns} == {"refdata-hdb": True,
                                                     "orders-rdb": False}


def test_an_export_from_before_the_flag_imports_as_paired():
    d = {"name": "old", "host": "h", "port": 1, "kind": "historical"}
    assert connection_from_dict(d).standalone is False


# --- what counts as one-sided ------------------------------------------------

def test_a_declared_single_side_is_the_environments_answer(tmp_path):
    envs = _store(tmp_path, _hdb()).list_environments()
    assert standalone_side(envs["refdata"]) == "historical"


def test_a_side_merely_missing_is_not_a_declaration(tmp_path):
    """Nobody said this environment is finished — it is half-configured, which
    is a different thing and gets the existing nag instead."""
    envs = _store(tmp_path, _hdb(standalone=False)).list_environments()
    assert standalone_side(envs["refdata"]) is None


def test_a_paired_environment_is_not_one_sided(tmp_path):
    """The box is moot once the counterpart is actually there."""
    s = _store(tmp_path, _hdb(env="orders"), _rdb(env="orders"))
    assert standalone_side(s.list_environments()["orders"]) is None


def test_market_data_needs_no_declaration(tmp_path):
    s = _store(tmp_path, Connection(id=None, name="ref", host="h", port=3,
                                    kind="marketdata", env="md"))
    assert standalone_side(s.list_environments()["md"]) is None


# --- asking one for the period it has not ------------------------------------

def test_the_run_time_error_says_the_environment_is_one_sided(tmp_path):
    s = _store(tmp_path, _hdb())
    with pytest.raises(ValueError, match="historical only"):
        resolve_target(s, "refdata", ResolvedTime("realtime", None, None))


def test_an_unfinished_environment_still_says_add_one(tmp_path):
    s = _store(tmp_path, _hdb(standalone=False))
    with pytest.raises(ValueError, match="add one in Admin"):
        resolve_target(s, "refdata", ResolvedTime("realtime", None, None))


def test_the_side_it_has_answers_as_usual(tmp_path):
    s = _store(tmp_path, _hdb())
    rt = ResolvedTime("historical", None, None)
    conn, out = resolve_target(s, "refdata", rt)
    assert conn.name == "refdata-hdb" and out is rt


# --- what a dashboard offers -------------------------------------------------

def test_offers():
    assert offers("both", "realtime") and offers("both", "historical")
    assert offers("historical", "historical")
    assert not offers("historical", "realtime")
    assert not offers("realtime", "historical")


def test_a_period_it_no_longer_offers_lands_on_one_it_does():
    """Declared historical-only after being built real-time: the stored period
    outlives the declaration, and must not resolve to a server that is gone."""
    spec = coerce_spec({"mode": "realtime"}, "historical")
    assert spec == {"mode": "historical",
                    "range": {"kind": "preset", "name": "today"}}


def test_a_period_it_does_offer_is_left_alone():
    spec = {"mode": "historical", "range": {"kind": "preset", "name": "last_7d"}}
    assert coerce_spec(spec, "historical") == spec
    assert coerce_spec({"mode": "realtime"}, "both") == {"mode": "realtime"}


def test_a_historical_only_dashboard_keeps_every_range_but_real_time():
    options = dashboards.time_options("historical")
    assert "Real-time" not in options
    assert "Last 7 days" in options and "Custom range…" in options


def test_a_real_time_only_dashboard_offers_nothing_to_choose():
    assert list(dashboards.time_options("realtime")) == ["Real-time"]


def test_by_default_a_dashboard_offers_both():
    assert dashboards.time_options("both") == dashboards.TIME_OPTIONS
    assert Dashboard(id=1, name="d").periods == "both"


def test_periods_survives_the_json_round_trip():
    d = Dashboard(id=1, name="d", periods="historical")
    assert dashboard_from_dict(dashboard_to_dict(d)).periods == "historical"


def test_a_dashboard_saved_before_periods_existed_offers_both():
    assert dashboard_from_dict({"name": "old"}).periods == "both"


# --- validation --------------------------------------------------------------

def _dash(env="refdata", periods="both") -> Dashboard:
    return Dashboard(
        id=1, name="D", periods=periods,
        datasets=[Dataset(name="o", env=env, table="target")],
        rows=[Row(widgets=[Widget(type="kpi", dataset="o", title="T",
                                  spec={"column": "size", "agg": "sum"})])])


def test_offering_both_over_a_one_sided_environment_is_a_problem(tmp_path):
    problems = ed.validate(_dash(), _store(tmp_path, _hdb()))
    assert any("is historical only" in p and "Historical only" in p
               for p in problems)


def test_offering_the_side_it_has_is_fine(tmp_path):
    assert ed.validate(_dash(periods="historical"),
                       _store(tmp_path, _hdb())) == []


def test_offering_the_side_it_does_not_have_is_a_problem(tmp_path):
    problems = ed.validate(_dash(periods="realtime"), _store(tmp_path, _hdb()))
    assert any("is historical only" in p for p in problems)


def test_an_unfinished_environment_does_not_make_the_dashboard_wrong(tmp_path):
    """The regression this must not cause: every dashboard over an environment
    whose twin has not been added yet would otherwise refuse to save."""
    problems = ed.validate(_dash(env="orders"),
                           _store(tmp_path, _rdb()))
    assert problems == []


def test_the_declared_period_decides_what_the_datasets_are_checked_against(tmp_path):
    """Historical-only, and the stored period says real-time. The datasets are
    validated against what it will actually run, not what it was left on."""
    draft = _dash(periods="historical")
    draft.time_context = {"mode": "realtime"}
    assert ed.validate(draft, _store(tmp_path, _hdb())) == []
    assert draft.time_context["mode"] == "historical"


# --- the admin page ----------------------------------------------------------

def _admin(db_path: str) -> AppTest:
    at = AppTest.from_string(f'''
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import admin

store = Storage(r"{db_path}")
store.init_db()
admin.render(store, ConnectionManager())
''', default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _text(at: AppTest) -> str:
    return " ".join(el.value for el in list(at.markdown) + list(at.caption))


def test_admin_offers_the_box_on_a_new_connection(tmp_path):
    _store(tmp_path, _rdb())
    at = _admin(str(tmp_path / "t.db"))
    assert any(c.key == "ac_solo" for c in at.checkbox)


def test_a_declared_environment_is_not_reported_as_half_configured(tmp_path):
    _store(tmp_path, _hdb())
    text = _text(_admin(str(tmp_path / "t.db")))
    assert "historical only, by design" in text
    assert "has no real-time server" not in text


def test_an_undeclared_one_still_is(tmp_path):
    _store(tmp_path, _hdb(standalone=False))
    text = _text(_admin(str(tmp_path / "t.db")))
    assert "has no real-time server" in text


def test_a_declared_environment_is_not_offered_to_its_counterpart(tmp_path):
    """It said it will never have one; offering to add it would contradict that."""
    assert "refdata" not in _env_options(_store(tmp_path, _hdb()), "realtime")


def test_an_undeclared_environment_is_still_offered(tmp_path):
    s = _store(tmp_path, _hdb(standalone=False))
    assert "refdata" in _env_options(s, "realtime")
