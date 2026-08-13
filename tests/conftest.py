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


@pytest.fixture(autouse=True)
def no_scheduler_spawn(monkeypatch):
    monkeypatch.setenv("KDBMONITOR_NO_AUTOSTART", "1")
