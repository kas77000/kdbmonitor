"""An alert step that holds its rows for a while.

The same problem as a static dataset, with one difference that changes the
answer: an alert runs unattended all day. A dashboard is looked at, so "held
until somebody reloads it" is safe there — somebody is there. Nobody is watching
an alert, so a step says how long its rows may stand, and after that it goes and
asks. Held forever would mean an alert still checking against yesterday's
universe tomorrow morning, with nothing on screen to say so.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd

from kdbmonitor.core.chain import preview_chain, run_chain
from kdbmonitor.core.client import FakeClient
from kdbmonitor.core.evaluate import evaluate_alert
from kdbmonitor.core.models import (
    Alert, Channels, RearmPolicy, Step, TriggerCondition, alert_from_dict,
    alert_to_dict,
)
from kdbmonitor.core.qcache import QueryCache
from kdbmonitor.core.summaries import cache_summary, step_summary

NOW = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)
UNIVERSE = "select from universe"
ORDERS = "select from target"


def _client() -> FakeClient:
    return FakeClient({
        UNIVERSE: pd.DataFrame({"sym": ["AAPL", "MSFT"]}),
        ORDERS: pd.DataFrame({"sym": ["AAPL"], "qty": [10]}),
    })


def _alert(*steps: Step) -> Alert:
    return Alert(id=1, name="a", enabled=True, poll_interval_secs=15,
                 steps=list(steps),
                 trigger=TriggerCondition(type="has_rows"),
                 channels=Channels(), rearm=RearmPolicy())


def _step(qsql: str, name="step1", cache_secs=0) -> Step:
    return Step(server="s", table="", mode="raw", raw_qsql=qsql,
                output_name=name, cache_secs=cache_secs)


def _resolver(client):
    return lambda server: client


# --- the default is unchanged ----------------------------------------------

def test_a_step_with_no_ttl_queries_every_tick():
    client, cache = _client(), QueryCache()
    alert = _alert(_step(UNIVERSE))
    for _ in range(3):
        run_chain(alert, _resolver(client), cache=cache, now=NOW)
    assert client.calls == [UNIVERSE] * 3


def test_without_a_cache_a_ttl_changes_nothing():
    client = _client()
    alert = _alert(_step(UNIVERSE, cache_secs=3600))
    run_chain(alert, _resolver(client), now=NOW)
    run_chain(alert, _resolver(client), now=NOW)
    assert client.calls == [UNIVERSE] * 2


# --- held for as long as it says --------------------------------------------

def test_inside_its_ttl_the_step_does_not_go_back():
    client, cache = _client(), QueryCache()
    alert = _alert(_step(UNIVERSE, cache_secs=3600))
    run_chain(alert, _resolver(client), cache=cache, now=NOW)
    run_chain(alert, _resolver(client), cache=cache,
              now=NOW + timedelta(minutes=59))
    assert client.calls == [UNIVERSE]


def test_past_its_ttl_it_asks_again():
    client, cache = _client(), QueryCache()
    alert = _alert(_step(UNIVERSE, cache_secs=3600))
    run_chain(alert, _resolver(client), cache=cache, now=NOW)
    run_chain(alert, _resolver(client), cache=cache,
              now=NOW + timedelta(hours=1, seconds=1))
    assert client.calls == [UNIVERSE] * 2


def test_the_rows_handed_back_are_the_ones_that_were_fetched():
    client, cache = _client(), QueryCache()
    alert = _alert(_step(UNIVERSE, cache_secs=3600))
    first = run_chain(alert, _resolver(client), cache=cache, now=NOW)
    again = run_chain(alert, _resolver(client), cache=cache,
                      now=NOW + timedelta(minutes=1))
    assert again.equals(first)


def test_the_step_that_matters_still_runs_every_tick():
    """The point of holding step 1 is that step 2 can keep checking."""
    client, cache = _client(), QueryCache()
    alert = _alert(_step(UNIVERSE, cache_secs=3600),
                   _step(ORDERS, name="step2"))
    run_chain(alert, _resolver(client), cache=cache, now=NOW)
    run_chain(alert, _resolver(client), cache=cache,
              now=NOW + timedelta(minutes=1))
    assert client.calls == [UNIVERSE, ORDERS, ORDERS]


def test_a_step_whose_query_changed_is_not_answered_from_the_old_one():
    """A held step whose text depends on an earlier result stops matching the
    moment that result changes, TTL or no TTL."""
    client = FakeClient({
        "select from t where sym in `AAPL": pd.DataFrame({"sym": ["AAPL"]}),
        "select from t where sym in `MSFT": pd.DataFrame({"sym": ["MSFT"]}),
    })
    cache = QueryCache()
    for sym in ("AAPL", "MSFT"):
        alert = _alert(_step(f"select from t where sym in `{sym}",
                             cache_secs=3600))
        run_chain(alert, _resolver(client), cache=cache, now=NOW)
    assert client.calls == ["select from t where sym in `AAPL",
                            "select from t where sym in `MSFT"]


def test_two_alerts_reading_the_same_thing_fetch_it_once_between_them():
    """The cache is keyed by the query, not by who asked for it."""
    client, cache = _client(), QueryCache()
    for _ in range(2):
        run_chain(_alert(_step(UNIVERSE, cache_secs=3600)), _resolver(client),
                  cache=cache, now=NOW)
    assert client.calls == [UNIVERSE]


# --- evaluation and preview -------------------------------------------------

def test_an_evaluated_alert_holds_what_its_steps_asked_to_hold():
    client, cache = _client(), QueryCache()
    alert = _alert(_step(UNIVERSE, cache_secs=3600))
    for _ in range(2):
        res = evaluate_alert(alert, _resolver(client), prev_run=None, now=NOW,
                             cache=cache)
    assert res.status == "triggered"
    assert client.calls == [UNIVERSE]


def test_an_evaluation_without_a_cache_still_works():
    client = _client()
    res = evaluate_alert(_alert(_step(UNIVERSE, cache_secs=3600)),
                         _resolver(client), prev_run=None, now=NOW)
    assert res.row_count == 2


def test_preview_always_goes_to_the_server():
    """Preview is somebody asking for this query to be run. Answering from a
    held frame would show them what the alert last saw."""
    client = _client()
    alert = _alert(_step(UNIVERSE, cache_secs=86400))
    preview_chain(alert, _resolver(client))
    preview_chain(alert, _resolver(client))
    assert client.calls == [UNIVERSE] * 2


# --- what it is saved and described as --------------------------------------

def test_the_ttl_round_trips_through_json():
    alert = _alert(_step(UNIVERSE, cache_secs=3600), _step(ORDERS, "step2"))
    back = alert_from_dict(alert_to_dict(alert))
    assert [s.cache_secs for s in back.steps] == [3600, 0]


def test_an_alert_saved_before_this_existed_queries_every_tick():
    stored = alert_to_dict(_alert(_step(UNIVERSE)))
    for step in stored["steps"]:
        step.pop("cache_secs")
    assert alert_from_dict(stored).steps[0].cache_secs == 0


def test_a_hand_edited_ttl_that_is_not_a_number_falls_back():
    stored = alert_to_dict(_alert(_step(UNIVERSE)))
    stored["steps"][0]["cache_secs"] = "a while"
    assert alert_from_dict(stored).steps[0].cache_secs == 0


def test_a_held_step_says_so_in_its_summary():
    assert cache_summary(3600) == "cached 1 hour"
    assert "cached 1 hour" in step_summary(_step(UNIVERSE, cache_secs=3600))


def test_an_ordinary_step_reads_as_it_always_did():
    assert cache_summary(0) == ""
    assert step_summary(_step(UNIVERSE)) == "s · raw qSQL"


def test_a_duration_off_the_menu_is_still_described():
    assert cache_summary(45) == "cached 45s"


# --- the control that sets it -----------------------------------------------

def test_every_preset_the_builder_offers_maps_to_a_duration():
    from kdbmonitor.core.models import STEP_CACHE_PRESETS
    from kdbmonitor.ui.builder import _cache_label
    for label, secs in STEP_CACHE_PRESETS.items():
        assert _cache_label(secs) == label


def test_the_first_preset_is_the_one_that_changes_nothing():
    from kdbmonitor.core.models import STEP_CACHE_PRESETS
    assert list(STEP_CACHE_PRESETS.values())[0] == 0


def test_a_ttl_the_control_cannot_show_falls_back_to_a_selectable_one():
    """A hand-edited bundle can hold a duration nobody can pick. It still runs;
    the control has to show something rather than raise."""
    from kdbmonitor.ui.builder import _cache_label
    assert _cache_label(45) == "Not at all"
