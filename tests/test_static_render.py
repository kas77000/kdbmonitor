"""The pages that show a held query, rendered.

The controls for this are only drawn when something is actually being held —
the note above a dashboard, the caption under a step — so the states that carry
them are the ones no other render test reaches. A page can import cleanly and
still blow up building them: a column index off by one, a duplicate key, an
option list that no longer holds the stored value.
"""
import pytest
from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Row, Widget
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.models import (
    Alert, Channels, RearmPolicy, Step, TriggerCondition,
)
from kdbmonitor.core.storage import Storage


def _dashboard() -> Dashboard:
    return Dashboard(
        id=None, name="Held", refresh_secs=0,
        datasets=[Dataset(name="universe", env="orders", table="target",
                          static=True),
                  Dataset(name="live", env="orders", table="target")],
        rows=[Row(height_in=1.4, widgets=[
            Widget(type="table", dataset="universe", title="Universe"),
            Widget(type="table", dataset="live", title="Live")])])


def _alert() -> Alert:
    return Alert(
        id=None, name="Held step", enabled=True, poll_interval_secs=15,
        steps=[Step(server="orders_demo", table="target", mode="form",
                    output_name="step1", cache_secs=3600),
               Step(server="orders_demo", table="work_order", mode="form",
                    output_name="step2")],
        trigger=TriggerCondition(type="has_rows"), channels=Channels(),
        rearm=RearmPolicy())


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("held") / "app.db")
    s = Storage(path)
    s.init_db()
    for spec in demo_connection_specs():
        s.add_connection(spec)
    s.add_dashboard(_dashboard())
    s.add_alert(_alert())
    return path


def _script(db_path: str, page: str, extra: str = "") -> str:
    return f'''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import builder, dashboards, monitor

store = Storage(r"{db_path}")
store.init_db()
mgr = ConnectionManager()
{extra}
page = "{page}"
if page == "builder":
    builder.render(store, mgr)
elif page == "monitor":
    monitor.render(store, mgr)
else:
    dashboards.render(store, mgr)
'''


def _run(db_path: str, page: str, extra: str = "") -> AppTest:
    at = AppTest.from_string(_script(db_path, page, extra),
                             default_timeout=90).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_a_dashboard_holding_a_dataset_renders(db):
    at = _run(db, "dashboards", 'st.query_params["dash"] = "1"')
    assert any(k == "rl_static_1" for k in
               [b.key for b in at.button if b.key]), "no way to reload it"


def test_the_note_names_what_is_being_held(db):
    """Data that is not being refreshed must not look like data that is — and
    the reader is not always the author."""
    at = _run(db, "dashboards", 'st.query_params["dash"] = "1"')
    assert any("universe" in c.value and "fetched once" in c.value
               for c in at.caption), "the page does not say it is held"


def test_a_dashboard_view_holds_its_static_dataset(db):
    at = _run(db, "dashboards", 'st.query_params["dash"] = "1"')
    assert len(at.session_state["dash_static_frames"]) == 1


def test_reloading_lets_go_of_every_held_frame(db):
    """The button's own rerun is stubbed out: what is under test is that asking
    for a reload leaves nothing held and nothing drawn from it."""
    script = _script(db, "dashboards", 'st.query_params["dash"] = "1"') + '''
st.rerun = lambda *a, **k: None
st.session_state["_held"] = len(dashboards.static_cache())
dashboards.reload_static(1)
st.session_state["_left"] = len(dashboards.static_cache())
st.session_state["_frames"] = dashboards.frames_key(1) in st.session_state
'''
    at = AppTest.from_string(script, default_timeout=90).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert at.session_state["_held"] == 1
    assert at.session_state["_left"] == 0
    assert at.session_state["_frames"] is False


def test_the_editor_renders_a_static_dataset(db):
    extra = ('st.session_state["dash_mode"] = "edit"\n'
             'st.session_state["dash_edit_id"] = 1\n'
             'st.session_state["dash_edit_section"] = "Data"')
    at = _run(db, "dashboards", extra)
    assert at.checkbox(key="ds0_st").value is True


def test_the_builder_renders_a_step_with_a_ttl(db):
    extra = ('from kdbmonitor.ui import builder as _b\n'
             '_b._load_edit(store.list_alerts()[0])')
    at = _run(db, "builder", extra)
    assert at.selectbox(key="b_cache_0").value == "1 hour"
    assert at.selectbox(key="b_cache_1").value == "Not at all"


def test_the_monitor_renders_an_alert_with_a_held_step(db):
    _run(db, "monitor")
