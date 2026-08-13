"""One scheduler, and only one.

Two schedulers on one database means every recipient gets every report twice.
These tests hold that shut against two tabs loading at once, a UI restarted
while one is already running, and somebody starting one by hand.
"""
from datetime import datetime, timedelta, timezone

import pytest

from kdbmonitor.core.storage import Storage
from kdbmonitor.scheduler import supervisor as sup

NOW = datetime(2026, 8, 13, 7, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store() -> Storage:
    s = Storage(":memory:")
    s.init_db()
    return s


# --- the lock ----------------------------------------------------------------

def test_the_first_process_to_ask_gets_the_lock(store):
    assert sup.claim(store, NOW, pid=100, host="A") is True


def test_the_second_one_does_not(store):
    assert sup.claim(store, NOW, pid=100, host="A") is True
    assert sup.claim(store, NOW, pid=200, host="B") is False


def test_the_holder_is_named(store):
    sup.claim(store, NOW, pid=8412, host="THISBOX")
    beat, pid, host = sup.holder(store)
    assert (pid, host) == (8412, "THISBOX")
    assert beat == "2026-08-13T07:00:00Z"


def test_a_stale_lock_can_be_taken_over(store):
    """A crashed scheduler leaves its lock behind; nothing else would ever run
    again if that were permanent."""
    sup.claim(store, NOW, pid=100, host="A")
    later = NOW + timedelta(seconds=sup.STALE_SECS + 1)
    assert sup.claim(store, later, pid=200, host="B") is True
    assert sup.holder(store)[1] == 200


def test_a_lock_still_being_beaten_cannot_be_taken(store):
    sup.claim(store, NOW, pid=100, host="A")
    almost = NOW + timedelta(seconds=sup.STALE_SECS - 5)
    assert sup.claim(store, almost, pid=200, host="B") is False


def test_a_heartbeat_keeps_the_lock_alive(store):
    sup.claim(store, NOW, pid=100, host="A")
    sup.heartbeat(store, NOW + timedelta(seconds=50), pid=100, host="A")
    # 70s after the original claim, but only 20s after the beat.
    assert sup.claim(store, NOW + timedelta(seconds=70), pid=200, host="B") is False


def test_releasing_hands_it_straight_over(store):
    """A clean stop must not make the next start wait out the stale window."""
    sup.claim(store, NOW, pid=100, host="A")
    sup.release(store)
    assert sup.holder(store) is None
    assert sup.claim(store, NOW, pid=200, host="B") is True


def test_a_timestamp_is_always_twenty_characters(store):
    """The staleness check is substr(value, 1, 20). Any other width breaks it."""
    assert len(sup.stamp(NOW)) == 20
    assert len(sup.stamp(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc))) == 20


# --- the desired state -------------------------------------------------------

def test_a_fresh_database_wants_a_scheduler_running(store):
    assert sup.desired(store) == "running"


def test_stop_and_start_are_remembered(store):
    sup.request_stop(store, NOW)
    assert sup.desired(store) == "stopped"
    sup.request_start(store)
    assert sup.desired(store) == "running"


# --- status ------------------------------------------------------------------

def test_a_beating_scheduler_reads_as_running(store):
    sup.claim(store, NOW, pid=8412, host="THISBOX")
    st = sup.status(store, NOW + timedelta(seconds=4))
    assert st.state == "running"
    assert (st.pid, st.host) == (8412, "THISBOX")
    assert 3 <= st.beat_age_secs <= 5


def test_nothing_running_and_nothing_wanted_reads_as_stopped(store):
    sup.request_stop(store, NOW)
    assert sup.status(store, NOW).state == "stopped"


def test_nothing_running_but_one_wanted_reads_as_stale(store):
    """The state the UI reconciles by spawning."""
    assert sup.status(store, NOW).state == "stale"


def test_a_scheduler_asked_to_stop_reads_as_stopping_at_first(store):
    sup.claim(store, NOW, pid=1, host="A")
    sup.request_stop(store, NOW)
    assert sup.status(store, NOW + timedelta(seconds=2)).state == "stopping"


def test_one_that_has_not_gone_after_the_grace_is_not_responding(store):
    sup.claim(store, NOW, pid=1, host="A")
    sup.request_stop(store, NOW)
    later = NOW + timedelta(seconds=sup.STOP_GRACE_SECS + 1)
    sup.heartbeat(store, later, pid=1, host="A")     # still alive, still there
    assert sup.status(store, later).state == "not_responding"


# --- autostart ---------------------------------------------------------------

def test_a_stale_scheduler_that_is_wanted_gets_spawned(store, monkeypatch):
    monkeypatch.delenv("KDBMONITOR_NO_AUTOSTART", raising=False)
    calls = []
    assert sup.ensure_running(store, NOW, spawn=lambda: calls.append(1)) == "starting"
    assert calls == [1]


def test_one_already_running_is_left_alone(store, monkeypatch):
    monkeypatch.delenv("KDBMONITOR_NO_AUTOSTART", raising=False)
    sup.claim(store, NOW, pid=1, host="A")
    calls = []
    assert sup.ensure_running(store, NOW, spawn=lambda: calls.append(1)) == "running"
    assert calls == []


def test_one_that_was_stopped_is_not_respawned(store, monkeypatch):
    """The bug this design exists to avoid: Stop, then the next page load puts
    it straight back."""
    monkeypatch.delenv("KDBMONITOR_NO_AUTOSTART", raising=False)
    sup.request_stop(store, NOW)
    calls = []
    assert sup.ensure_running(store, NOW, spawn=lambda: calls.append(1)) == "stopped"
    assert calls == []


def test_the_environment_can_forbid_autostart(store, monkeypatch):
    monkeypatch.setenv("KDBMONITOR_NO_AUTOSTART", "1")
    calls = []
    got = sup.ensure_running(store, NOW, spawn=lambda: calls.append(1))
    assert calls == []
    assert "autostart" in got


def test_a_spawn_that_blows_up_is_reported_rather_than_swallowed(store,
                                                                monkeypatch):
    """A scheduler that silently declines to start is the worst outcome:
    everything looks normal until a report does not arrive."""
    monkeypatch.delenv("KDBMONITOR_NO_AUTOSTART", raising=False)

    def boom():
        raise OSError("python not found")

    assert sup.ensure_running(store, NOW, spawn=boom) == "failed"
    assert "python not found" in sup.status(store, NOW).spawn_error
