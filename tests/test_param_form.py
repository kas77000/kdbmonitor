"""The form itself: it holds the query back until the values pass.

The point of a rule is that it is enforced *before* anything is sent, and that
what the reader gets told is the rule rather than an empty dashboard. These run
the real page against the demo servers.
"""
import pytest
from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Parameter, Row, Widget,
)
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.storage import Storage


def _dashboard(*parameters: Parameter, query: str) -> Dashboard:
    return Dashboard(
        id=None, name="Desk", description="", refresh_secs=0,
        datasets=[Dataset(name="rows", env="orders", table="", mode="raw",
                          raw_qsql=query)],
        rows=[Row(height_in=1.2, widgets=[
            Widget(type="table", dataset="rows", title="Rows", spec={})])],
        parameters=list(parameters))


@pytest.fixture
def db(tmp_path):
    def build(dashboard: Dashboard) -> str:
        path = str(tmp_path / "app.db")
        store = Storage(path)
        store.init_db()
        for spec in demo_connection_specs():
            store.add_connection(spec)
        store.add_dashboard(dashboard)
        return path
    return build


def _run(db_path: str, extra: str = "") -> AppTest:
    at = AppTest.from_string(f'''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboards

# AppTest cannot round-trip a pills widget across a rerun, and the dashboard's
# tab strip is one. Same stand-in the tab tests use. (ASCII only in here: the
# script is written to disk in the platform encoding and read back as UTF-8.)
st.pills = lambda label, options, **kw: st.session_state.get(kw.get("key"))

store = Storage(r"{db_path}")
store.init_db()
if not st.session_state.get("_seeded"):
    st.session_state["_seeded"] = True
    st.query_params["dash"] = "1"
{extra or "    pass"}
dashboards.render(store, ConnectionManager())
''', default_timeout=90).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _said(at) -> str:
    return " ".join([str(w.value) for w in at.warning]
                    + [str(i.value) for i in at.info]
                    + [str(m.value) for m in at.markdown])


def _sent(at) -> str:
    """The query the dashboard actually sent — the only proof that matters."""
    assert "dash_frames_1" in at.session_state, "the dashboard never ran a query"
    return at.session_state["dash_frames_1"]["results"]["rows"].qsql


# --- the gate ----------------------------------------------------------------

def test_a_required_value_left_blank_holds_the_query_back(db):
    path = db(_dashboard(
        Parameter(name="sym", kind="text", label="Instrument", required=True),
        query="select from target where sym={{param:sym}}"))
    at = _run(path)
    assert "Instrument is required" in _said(at)
    assert "has not run" in _said(at)


def test_the_form_is_shown_with_apply_and_reset(db):
    path = db(_dashboard(
        Parameter(name="sym", kind="text", required=True),
        query="select from target where sym={{param:sym}}"))
    at = _run(path)
    labels = [b.label for b in at.button]
    assert "Apply" in labels and "Reset" in labels


def test_a_value_breaking_a_rule_is_named_rather_than_left_to_kdb(db):
    path = db(_dashboard(
        Parameter(name="d", kind="date", label="As of", default="2026-08-01",
                  weekdays_only=True),
        query="select from target where date={{param:d}}"))
    at = _run(path)
    said = _said(at)
    assert "As of" in said and "Saturday" in said
    assert "has not run" in said


def test_nothing_is_drawn_while_the_form_is_blocking(db):
    """A wall of failed panels teaches the reader nothing the message above
    them has not already said."""
    path = db(_dashboard(
        Parameter(name="sym", kind="text", required=True),
        query="select from target where sym={{param:sym}}"))
    at = _run(path)
    assert not at.dataframe
    assert "Generate PDF" not in [b.label for b in at.button]


def test_a_dashboard_whose_values_pass_runs_as_normal(db):
    path = db(_dashboard(
        Parameter(name="sym", kind="text", default="AAPL"),
        query="select from target where sym={{param:sym}}"))
    at = _run(path)
    assert "has not run" not in _said(at)
    assert "Generate PDF" in [b.label for b in at.button]


# --- applying ----------------------------------------------------------------

def test_a_typed_value_reaches_the_query_only_once_applied(db):
    """A query per keystroke would be a query per keystroke."""
    path = db(_dashboard(
        Parameter(name="sym", kind="text", default="AAPL"),
        query="select from target where sym={{param:sym}}"))
    at = _run(path)
    assert _sent(at) == "select from target where sym=`AAPL"

    at.text_input[0].set_value("MSFT").run()
    assert not at.exception, [str(e.value) for e in at.exception]
    # Typing alone changes nothing: still running on what was applied.
    assert _sent(at) == "select from target where sym=`AAPL"

    [b for b in at.button if b.label == "Apply"][0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert _sent(at) == "select from target where sym=`MSFT"


def test_applying_a_bad_value_leaves_the_dashboard_on_the_good_one(db):
    path = db(_dashboard(
        Parameter(name="sym", kind="text", default="AAPL",
                  pattern="^[A-Z]+$",
                  pattern_message="Use an uppercase ticker"),
        query="select from target where sym={{param:sym}}"))
    at = _run(path)
    at.text_input[0].set_value("not a ticker").run()
    [b for b in at.button if b.label == "Apply"][0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]

    assert "Use an uppercase ticker" in _said(at)
    # Refused, so nothing was sent with it: the dashboard is still on AAPL.
    assert _sent(at) == "select from target where sym=`AAPL"


def test_reset_puts_every_control_back(db):
    path = db(_dashboard(
        Parameter(name="sym", kind="text", default="AAPL"),
        query="select from target where sym={{param:sym}}"))
    at = _run(path)
    at.text_input[0].set_value("MSFT").run()
    [b for b in at.button if b.label == "Apply"][0].click().run()

    [b for b in at.button if b.label == "Reset"][0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert at.text_input[0].value == "AAPL"

    from kdbmonitor.ui.parameters import applied_key
    assert at.session_state[applied_key(1)]["sym"] == "AAPL"


# --- the other shape ---------------------------------------------------------

def test_the_editor_draws_the_rules_for_a_parameter(db):
    """The author sets them in the same card they name the parameter in."""
    path = db(_dashboard(
        Parameter(name="d", kind="date", label="As of", default="2026-07-31",
                  minimum="today-90d", maximum="today", weekdays_only=True),
        query="select from target where date={{param:d}}"))
    at = _run(path, '    st.session_state["dash_mode"] = "edit"\n'
                    '    st.session_state["dash_edit_id"] = 1\n'
                    '    st.session_state["dash_edit_section"] = "Data"')
    keys = [el.key for el in at.text_input] + [el.key for el in at.checkbox]
    assert "pm0_min" in keys and "pm0_max" in keys and "pm0_wd" in keys
    assert "pm0_req" in keys
    printed = " ".join(str(m.value) for m in at.markdown)
    assert "reaches the query as" in printed


def test_the_editor_shows_what_a_value_becomes_in_q(db):
    path = db(_dashboard(
        Parameter(name="sym", kind="text", default="AAPL"),
        query="select from target where sym={{param:sym}}"))
    at = _run(path, '    st.session_state["dash_mode"] = "edit"\n'
                    '    st.session_state["dash_edit_id"] = 1\n'
                    '    st.session_state["dash_edit_section"] = "Data"')
    printed = " ".join(str(m.value) for m in at.markdown)
    assert "`AAPL`" in printed          # the q literal, in a code span


def test_a_dashboard_whose_parameters_stop_at_the_transforms_keeps_live_controls(db):
    """No round trip to wait for, so no button to press."""
    from kdbmonitor.core.dashboard_models import Transform

    board = _dashboard(Parameter(name="sym", kind="text", default="AAPL"),
                       query="select from target")
    board.datasets[0].transforms = [Transform(kind="filter", params={
        "column": "sym", "op": "=", "value": "{{param:sym}}"})]
    at = _run(db(board))
    labels = [b.label for b in at.button]
    assert "Apply" not in labels and "Reset" not in labels
    assert at.text_input[0].value == "AAPL"
