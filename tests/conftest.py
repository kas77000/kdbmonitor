"""Test-wide guards.

Nothing in the suite may start a real scheduler. ``app.py`` starts one when the
UI launches, and tests/test_app_smoke.py launches the UI — without this, a test
run leaves detached processes behind, pointed at whatever KDBMONITOR_DB the
developer's shell happens to name, and does it again on every run and in CI.

Tests that exercise the autostart rule itself unset this for themselves and
pass their own ``spawn`` callable, so they assert on a stand-in rather than on
a process.
"""
import pytest
import streamlit as st


@pytest.fixture(autouse=True)
def no_scheduler_spawn(monkeypatch):
    monkeypatch.setenv("KDBMONITOR_NO_AUTOSTART", "1")


# Streamlit functions that AppTest scripts in this suite replace with stubs, to
# pin which section renders or to stop a rerun looping. The scripts run in this
# same process, so the assignment lands on the shared `streamlit` module.
_STUBBED = ("segmented_control", "pills", "rerun")


@pytest.fixture(autouse=True)
def restore_streamlit_stubs():
    """Put the stubbed Streamlit functions back after every test.

    Several test scripts do ``st.segmented_control = lambda *a, **k: "Data"``
    to choose which editor section renders. That assignment outlives the test,
    so without this the *rest of the process* silently gets whichever section
    the last such script asked for — and a test that only asserts "no
    exception" keeps passing while quietly rendering something else entirely.

    Restoring centrally rather than in each file: it is one rule, it covers the
    files that do this today, and it covers the next one that does it without
    that author having to know this was ever a problem.
    """
    saved = {name: getattr(st, name) for name in _STUBBED}
    yield
    for name, original in saved.items():
        setattr(st, name, original)
