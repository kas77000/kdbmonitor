"""How a fired alert reaches the person watching.

The deliveries add up rather than replace one another: an alert can beep, put
a notification on the desktop, raise the window, open its rows in a modal and
mail the desk, or do exactly one of those. These tests hold the model and the
choosing to that, and hold the upgrade path to not changing anybody's mind for
them.
"""
from datetime import datetime, timezone

from kdbmonitor.core.models import (
    Alert, Channels, RearmPolicy, Schedule, Step, TriggerCondition, Window,
    alert_from_dict, alert_from_json, alert_to_json, channels_from_dict,
)
from kdbmonitor.core.notifiers import InAppSink, delivery_payload, dispatch
from kdbmonitor.core.summaries import channels_summary


def _alert(**kw) -> Alert:
    base = dict(
        id=None, name="AAPL bid breakout", enabled=True, poll_interval_secs=15,
        steps=[Step(server="s", table="t", mode="form", output_name="step1")],
        trigger=TriggerCondition(type="has_rows"),
        channels=Channels(), rearm=RearmPolicy())
    base.update(kw)
    return Alert(**base)


# --- the model ---------------------------------------------------------------

def test_a_new_alert_speaks_up_without_shouting():
    """The defaults: seen and heard, but nothing that moves the user's window
    or interrupts what they are doing."""
    c = Channels()
    assert (c.in_app, c.sound, c.browser) == (True, True, True)
    assert (c.focus, c.popup) == (False, False)


def test_every_delivery_survives_a_save_and_a_load():
    alert = _alert(channels=Channels(in_app=False, sound=True, browser=True,
                                     focus=True, popup=True,
                                     email_to=["desk@x.com"],
                                     webhook_urls=["http://hook"]))
    back = alert_from_json(alert_to_json(alert))
    assert back.channels == alert.channels


# --- upgrading an existing install -------------------------------------------

def test_an_alert_saved_before_this_keeps_the_notifications_it_had():
    """Browser notifications used to ride along with the in-app message. An
    alert that had them must still have them, and one that did not must not
    start getting them because the new field defaults to on."""
    assert channels_from_dict({"in_app": True, "sound": True}).browser is True
    assert channels_from_dict({"in_app": False, "sound": True}).browser is False


def test_an_alert_saved_before_this_asks_for_nothing_new():
    old = channels_from_dict({"in_app": True, "sound": False,
                              "email_to": [], "webhook_urls": []})
    assert old.focus is False and old.popup is False


def test_an_alert_saved_before_this_runs_around_the_clock():
    old = {"id": 1, "name": "n", "enabled": True, "poll_interval_secs": 30,
           "steps": [], "trigger": {"type": "has_rows"},
           "channels": {"in_app": True, "sound": True, "email_to": [],
                        "webhook_urls": []},
           "rearm": {"mode": "transition", "cooldown_secs": 0}}
    assert alert_from_dict(old).schedule.mode == "always"


def test_a_schedule_survives_a_save_and_a_load():
    alert = _alert(schedule=Schedule(mode="windows",
                                     windows=[Window("17:45", "18:00")],
                                     days=[0, 1, 2, 3, 4], tz="Europe/London"))
    back = alert_from_json(alert_to_json(alert))
    assert back.schedule == alert.schedule


# --- what the browser is asked to do -----------------------------------------

def test_the_payload_carries_each_choice_separately():
    p = delivery_payload("AAPL", Channels(browser=True, sound=True, focus=True),
                         "AAPL: TRIGGERED (3 rows)", key="1-x")
    assert (p["notify"], p["sound"], p["focus"]) == (True, True, True)
    assert p["title"] == "AAPL" and p["key"] == "1-x"


def test_sound_alone_still_reaches_the_browser():
    """Sound is the browser's job even when no notification is wanted."""
    p = delivery_payload("A", Channels(in_app=True, sound=True, browser=False),
                         "m", key="k")
    assert p is not None and p["sound"] and not p["notify"]


def test_an_alert_with_nothing_for_the_browser_sends_nothing_to_it():
    quiet = Channels(in_app=True, sound=False, browser=False, focus=False,
                     email_to=["desk@x.com"])
    assert delivery_payload("A", quiet, "m", key="k") is None


def test_the_message_is_the_notification_body():
    p = delivery_payload("A", Channels(), "A: TRIGGERED (3 rows)", key="k")
    assert p["body"] == "A: TRIGGERED (3 rows)"


# --- the channels that leave the machine, unchanged --------------------------

def test_email_and_webhooks_still_go_out_together():
    sink, mails, hooks = InAppSink(), [], []
    dispatch(Channels(in_app=True, email_to=["me@x.com"],
                      webhook_urls=["http://hook"]),
             message="m", in_app_sink=sink,
             email_fn=lambda to, msg: mails.append(to),
             webhook_fn=lambda url, msg: hooks.append(url))
    assert sink.messages == ["m"] and mails == [["me@x.com"]] and hooks == ["http://hook"]


# --- saying it in one line ---------------------------------------------------

def test_the_summary_names_what_was_chosen():
    assert channels_summary(Channels(in_app=True, sound=True, browser=False)) == (
        "in-app · sound")
    assert channels_summary(Channels(in_app=False, sound=False, browser=True,
                                     focus=True, popup=True)) == (
        "notification · window to front · result pop-up")


def test_the_summary_counts_the_addresses_rather_than_listing_them():
    c = Channels(in_app=False, sound=False, browser=False,
                 email_to=["a@x.com", "b@x.com"], webhook_urls=["http://h"])
    assert channels_summary(c) == "2 emails · 1 webhook"


def test_an_alert_that_notifies_nobody_says_so():
    assert channels_summary(Channels(in_app=False, sound=False,
                                     browser=False)) == "nothing"


# --- the pop-up queue --------------------------------------------------------

_POPUP_PRELUDE = '''
import streamlit as st
from datetime import datetime, timezone
from kdbmonitor.core.models import (
    Alert, Channels, RearmPolicy, TriggerCondition)
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import popup

store = Storage(r"{db}")
store.init_db()
a = Alert(id=7, name="AAPL", enabled=True, poll_interval_secs=15, steps=[],
          trigger=TriggerCondition(type="has_rows"),
          channels=Channels(popup=True), rearm=RearmPolicy())
noon = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
'''


def _run(tmp_path, body: str):
    from streamlit.testing.v1 import AppTest
    db = str(tmp_path / "popup.db").replace("\\", "\\\\")
    at = AppTest.from_string(_POPUP_PRELUDE.format(db=db) + body,
                             default_timeout=30).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_a_fired_alert_is_queued_once_and_stays_dismissed(tmp_path):
    """The monitoring loop reruns every few seconds. A queue that forgot what
    it had already shown would reopen the modal the user just closed, which is
    a modal nobody can close."""
    at = _run(tmp_path, '''
first = popup.queue(a, "AAPL: TRIGGERED (3 rows)", noon, 3)
again = popup.queue(a, "AAPL: TRIGGERED (3 rows)", noon, 3)
waiting = len(popup.pending())
popup.render_pending(store)                # puts it on screen
after_showing = len(popup.pending())
popup.dismiss_all()
requeued = popup.queue(a, "AAPL: TRIGGERED (3 rows)", noon, 3)
st.text(f"{first}|{again}|{waiting}|{after_showing}|{requeued}")
''')
    assert at.text[0].value == "True|False|1|0|False"


def test_a_later_trigger_of_the_same_alert_pops_up_again(tmp_path):
    at = _run(tmp_path, '''
popup.queue(a, "m", noon, 3)
popup.render_pending(store)
popup.dismiss_all()
later = popup.queue(a, "m", noon.replace(minute=5), 4)
st.text(f"{later}|{len(popup.pending())}")
''')
    assert at.text[0].value == "True|1"


def test_the_pop_up_names_the_alert_and_offers_the_full_result(tmp_path):
    at = _run(tmp_path, '''
popup.queue(a, "AAPL: TRIGGERED (3 rows)", noon, 3)
popup.render_pending(store)
''')
    labels = [b.label for b in at.button]
    assert "Open the full result" in labels and "Dismiss" in labels
    assert any("AAPL" in str(m.value) for m in at.markdown)


def _demo_alert_script(db, channels_kw: str, schedule_kw: str = "") -> str:
    """A tick of the real engine against the demo mock, with one alert that
    triggers on the first check.

    The store is a file, as it is in the app (where it is cached across
    reruns): an in-memory one is rebuilt on every rerun, so the alert would be
    due again every time and a pop-up would rerun the script for ever.
    """
    return f'''
from kdbmonitor.ui import engine
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.models import (
    Alert, Channels, Filter, RearmPolicy, Schedule, Step, TriggerCondition,
    Window)

store = Storage(r"{str(db).replace(chr(92), chr(92) * 2)}")
store.init_db()
if not store.list_connections():
    for spec in demo_connection_specs():
        store.add_connection(spec)
aid = (store.list_alerts()[0].id if store.list_alerts() else store.add_alert(Alert(
    id=None, name="demo bid", enabled=True, poll_interval_secs=30,
    steps=[Step(server="kdp_demo", table="QATT", mode="form",
                filters=[Filter("sym", "in", ["AAPL"], "symbol")],
                output_name="step1")],
    trigger=TriggerCondition(type="has_rows"),
    channels=Channels({channels_kw}), rearm=RearmPolicy(){schedule_kw})))
engine.set_monitoring(store, True)
engine.run_tick(store, ConnectionManager())
'''


def test_a_triggered_alert_queues_its_pop_up_through_the_engine(tmp_path):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string(
        _demo_alert_script(tmp_path / "e1.db", "popup=True"),
        default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert len(at.session_state["popup_queue"]) == 1
    assert at.session_state["popup_queue"][0]["name"] == "demo bid"


def test_the_browser_payload_carries_the_choices_through_the_engine(tmp_path):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string(
        _demo_alert_script(tmp_path / "e2.db",
                           "in_app=False, sound=True, browser=True, focus=True"),
        default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    payloads = at.session_state["notify_payloads"]
    assert len(payloads) == 1
    assert payloads[0]["focus"] and payloads[0]["notify"] and payloads[0]["sound"]


# The engine reads the clock itself, so an hours test has to hold it still —
# otherwise the assertion is about what time it happens to be when the suite
# runs. Monday 2026-08-03, noon UTC.
_FROZEN_NOON = '''
import datetime as _d
from kdbmonitor.ui import engine as _e
class _Noon(_d.datetime):
    @classmethod
    def now(cls, tz=None):
        return _d.datetime(2026, 8, 3, 12, 0, tzinfo=_d.timezone.utc)
_e.datetime = _Noon
'''


def test_an_alert_outside_its_hours_is_not_evaluated_at_all(tmp_path):
    """Parked, not merely silenced: no query goes to KDB at all, so there is
    nothing for it to be a false positive about."""
    from streamlit.testing.v1 import AppTest

    schedule = (', schedule=Schedule(mode="windows", '
                'windows=[Window("17:45", "18:00")], tz="UTC")')
    at = AppTest.from_string(
        _FROZEN_NOON + _demo_alert_script(tmp_path / "e3.db", "popup=True",
                                          schedule) + '''
import streamlit as st
latest = store.latest_run(aid)
queued = st.session_state.get("popup_queue", [])
st.text(f"{latest['status']}|{len(store.list_runs(aid))}|{len(queued)}")
''', default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert at.text[0].value == "off_hours|1|0"


def test_an_alert_inside_its_hours_runs_as_normal(tmp_path):
    from streamlit.testing.v1 import AppTest

    schedule = (', schedule=Schedule(mode="windows", '
                'windows=[Window("11:00", "13:00")], tz="UTC")')
    at = AppTest.from_string(
        _FROZEN_NOON + _demo_alert_script(tmp_path / "e4.db", "in_app=True",
                                          schedule) + '''
import streamlit as st
st.text(store.latest_run(aid)["status"])
''', default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert at.text[0].value == "triggered"


def test_nothing_pops_up_when_nothing_fired(tmp_path):
    at = _run(tmp_path, '''
popup.render_pending(store)
st.text(f"{len(popup.pending())}")
''')
    assert at.text[0].value == "0" and not at.button
