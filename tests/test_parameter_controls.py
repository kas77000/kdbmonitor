"""A parameter's control has to survive being used.

The controls are drawn before the datasets run, from what a run reported — and
changing a parameter drops that run on purpose, so the frames already in hand
can be reshaped without going back to the server. With nowhere to remember what
was on offer, picking an instrument emptied the list it had just been picked
from and the control came back disabled: the only way out of a choice was a
choice that could no longer be made.
"""
import pandas as pd
import pytest
import streamlit as st

from kdbmonitor.core.dashboard_models import Dashboard, Parameter
from kdbmonitor.core.dataset import DatasetResult
from kdbmonitor.ui.dashboards import (
    _parameter_choices, choices_key, drop_derived, frames_key, remember_choices,
)


@pytest.fixture(autouse=True)
def clean_session():
    """A bare dict to stand in for the session, put back afterwards.

    Replacing it and leaving it replaced breaks every test that drives the app
    through Streamlit's own harness, which expects the real proxy object — so
    the original goes back even when a test fails.
    """
    original = st.session_state
    st.session_state = {}
    try:
        yield
    finally:
        st.session_state = original


def _dash() -> Dashboard:
    return Dashboard(id=7, name="VP", source="file", parameters=[
        Parameter(name="instrument", kind="column", dataset="profile",
                  column="sym", default="A")])


def _uploaded() -> dict:
    return {"profile": pd.DataFrame({"sym": ["A", "B", "C"],
                                     "cum": [0.3, 0.6, 1.0]})}


def _results(options) -> dict:
    return {"profile": DatasetResult("profile", None, "", None,
                                     choices={"instrument": options})}


# --- straight from the file, without waiting for a run ----------------------

def test_a_picker_fills_the_moment_the_file_lands():
    """It used to wait for a completed run, so the control was disabled on the
    pass that followed the upload and nothing triggered another."""
    assert _parameter_choices(_dash(), _uploaded()) == {
        "instrument": ["A", "B", "C"]}


def test_a_picker_with_no_file_yet_offers_nothing_rather_than_raising():
    assert _parameter_choices(_dash(), {}) == {}


def test_the_options_come_from_the_frame_as_uploaded():
    """Before any transform narrows it — after the filter this parameter
    drives, exactly one instrument is left."""
    assert len(_parameter_choices(_dash(), _uploaded())["instrument"]) == 3


# --- remembered across a change ---------------------------------------------

def test_what_a_run_offered_is_remembered():
    dash = _dash()
    remember_choices(dash.id, _results(["A", "B"]))
    assert st.session_state[choices_key(7)] == {"instrument": ["A", "B"]}


def test_changing_a_parameter_does_not_empty_its_own_list():
    """The bug: drop_derived cleared the payload the control was read from."""
    dash = _dash()
    remember_choices(dash.id, _results(["A", "B", "C"]))
    drop_derived(dash.id)
    assert _parameter_choices(dash, {}) == {"instrument": ["A", "B", "C"]}


def test_dropping_the_derived_frames_keeps_the_choices():
    dash = _dash()
    remember_choices(dash.id, _results(["A"]))
    st.session_state[frames_key(7)] = {"results": {}}
    drop_derived(dash.id)
    assert frames_key(7) not in st.session_state
    assert choices_key(7) in st.session_state


def test_a_failed_run_does_not_wipe_what_was_known():
    """A dead server should not also take away the picker."""
    dash = _dash()
    remember_choices(dash.id, _results(["A", "B"]))
    remember_choices(dash.id, _results([]))
    assert _parameter_choices(dash, {})["instrument"] == ["A", "B"]


def test_a_fresh_upload_wins_over_what_was_remembered():
    """Somebody's second file is a different set of instruments."""
    dash = _dash()
    remember_choices(dash.id, _results(["OLD"]))
    assert _parameter_choices(dash, _uploaded())["instrument"] == ["A", "B", "C"]


def test_two_dashboards_remember_separately():
    remember_choices(7, _results(["A"]))
    remember_choices(9, _results(["Z"]))
    assert st.session_state[choices_key(7)] != st.session_state[choices_key(9)]


def test_a_dashboard_with_no_parameters_asks_for_nothing():
    plain = Dashboard(id=7, name="P")
    assert _parameter_choices(plain, _uploaded()) == {}
