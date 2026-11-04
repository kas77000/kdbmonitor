"""The all-clear mail.

A silent inbox means either "nothing is wrong" or "nothing is checking"; this
mail says the first one. These tests hold an interval mail to certifying the
whole interval, hold the text to what the user wrote (or a sensible default),
and hold old alerts to mailing nobody new.
"""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from kdbmonitor.core import successmail
from kdbmonitor.core.evaluate import evaluate_alert
from kdbmonitor.core.models import (
    Alert, Channels, RearmPolicy, Schedule, Step, SuccessMail, TriggerCondition,
    alert_from_dict, alert_from_json, alert_to_json,
)
from kdbmonitor.core.portability import export_bundle_json, import_bundle_json
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.successmail import Period, SuccessState, render, step
from kdbmonitor.core.summaries import channels_summary

T0 = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
HOURLY = SuccessMail(enabled=True, mode="interval", interval_secs=3600)


def _alert(**kw) -> Alert:
    base = dict(
        id=None, name="Stuck orders", enabled=True, poll_interval_secs=900,
        steps=[Step(server="s", table="t", mode="form", output_name="step1")],
        trigger=TriggerCondition(type="has_rows"),
        channels=Channels(email_to=["desk@x.com"]), rearm=RearmPolicy(),
        schedule=Schedule(tz="UTC"), success=HOURLY)
    base.update(kw)
    return Alert(**base)


def _run(cfg, checks, start=T0, every=timedelta(minutes=15)):
    """Feed ``checks`` (True = clean) one ``every`` apart; return the periods."""
    state, mailed = None, []
    for i, clean in enumerate(checks):
        state, period = step(cfg, state, start + every * (i + 1), clean)
        if period:
            mailed.append(period)
    return mailed


# --- the period ---------------------------------------------------------------

def test_a_clean_hour_is_mailed_once():
    mailed = _run(HOURLY, [True] * 5)          # 09:15 opens, 10:15 closes
    assert len(mailed) == 1
    p = mailed[0]
    assert (p.since, p.until) == (T0 + timedelta(minutes=15),
                                  T0 + timedelta(minutes=75))
    assert p.checks == 5


def test_one_trigger_in_the_hour_and_no_mail_for_it():
    assert _run(HOURLY, [True, True, False, True, True]) == []


def test_the_hour_after_a_trigger_starts_clean():
    mailed = _run(HOURLY, [True, False, True, True, True] + [True] * 4)
    assert len(mailed) == 1
    assert mailed[0].since == T0 + timedelta(minutes=75)


def test_an_interval_shorter_than_the_check_mails_every_clean_check():
    cfg = SuccessMail(enabled=True, mode="interval", interval_secs=60)
    assert len(_run(cfg, [True, True, False, True])) == 2


def test_with_every_check_each_clean_check_mails():
    cfg = SuccessMail(enabled=True, mode="every_check")
    mailed = _run(cfg, [True, False, True])
    assert [p.checks for p in mailed] == [1, 1]


def test_a_period_with_no_clean_check_sends_nothing():
    state = SuccessState(period_start=T0, clean_checks=0, dirty=False)
    _, period = step(HOURLY, state, T0 + timedelta(hours=2), clean=False)
    assert period is None


# --- what counts as clean -----------------------------------------------------

class _Client:
    def __init__(self, df):
        self.df = df

    def query(self, q):
        return self.df


def test_an_on_change_repeat_is_not_all_clear():
    """Recorded as armed — it does not re-fire — but the condition still holds."""
    df = pd.DataFrame({"x": [1]})
    alert = _alert(rearm=RearmPolicy(mode="on_change"))
    first = evaluate_alert(alert, lambda s: _Client(df), None, T0)
    again = evaluate_alert(alert, lambda s: _Client(df), {"triggered": 1}, T0,
                           last_triggered_hash=first.result_hash)
    assert again.status == "armed" and again.condition_met is True


def test_a_quiet_check_is_condition_not_met():
    res = evaluate_alert(_alert(), lambda s: _Client(pd.DataFrame({"x": []})),
                         None, T0)
    assert res.condition_met is False and res.status == "armed"


# --- the text -----------------------------------------------------------------

PERIOD = Period(since=T0, until=T0 + timedelta(hours=1), checks=4)


def test_the_default_mail_says_what_was_checked():
    subject, body = render(_alert(), PERIOD, rows=0)
    assert subject == "KdbMonitor: Stuck orders all clear"
    assert body == ("Stuck orders did not trigger between 2026-11-04 09:00 and "
                    "2026-11-04 10:00.\nClean checks: 4. Rows in the last check: 0.")


def test_the_default_for_every_check_names_the_moment():
    alert = _alert(success=SuccessMail(enabled=True, mode="every_check"))
    _, body = render(alert, Period(since=T0, until=T0, checks=1), rows=2)
    assert body == "Stuck orders did not trigger at 2026-11-04 09:00.\nRows returned: 2."


def test_a_written_subject_and_body_are_filled_in():
    alert = _alert(group="Equities", success=SuccessMail(
        enabled=True, subject="OK: {alert} [{group}]",
        body="{checks} checks to {until}, {rows} rows"))
    assert render(alert, PERIOD, rows=3) == (
        "OK: Stuck orders [Equities]", "4 checks to 2026-11-04 10:00, 3 rows")


def test_times_are_written_in_the_alerts_own_zone():
    alert = _alert(schedule=Schedule(tz="Europe/Paris"),
                   success=SuccessMail(enabled=True, body="{since}"))
    assert render(alert, PERIOD, rows=0)[1] == "2026-11-04 10:00"


def test_an_unknown_placeholder_is_left_as_typed():
    alert = _alert(success=SuccessMail(enabled=True, subject="{alert} {desk}"))
    assert render(alert, PERIOD, rows=0)[0] == "Stuck orders {desk}"


def test_a_stray_brace_sends_the_text_as_typed():
    alert = _alert(success=SuccessMail(enabled=True, body="all {good"))
    assert render(alert, PERIOD, rows=0)[1] == "all {good"


def test_a_subject_cannot_carry_a_newline():
    alert = _alert(success=SuccessMail(enabled=True, subject="all\nclear"))
    assert render(alert, PERIOD, rows=0)[0] == "all clear"


# --- the model ----------------------------------------------------------------

def test_the_setting_survives_a_save_and_a_load():
    alert = _alert(success=SuccessMail(enabled=True, mode="every_check",
                                       subject="s", body="b"))
    assert alert_from_json(alert_to_json(alert)).success == alert.success


def test_an_alert_saved_before_this_mails_nobody_new():
    d = asdict(_alert())
    del d["success"]
    assert alert_from_dict(d).success == SuccessMail()
    assert SuccessMail().enabled is False


def test_a_hand_edited_mode_falls_back_to_an_interval():
    d = asdict(_alert())
    d["success"] = {"enabled": True, "mode": "weekly", "interval_secs": "x"}
    s = alert_from_dict(d).success
    assert (s.mode, s.interval_secs) == ("interval", 3600)


def test_it_travels_with_an_export():
    _, alerts = import_bundle_json(export_bundle_json([], [_alert()]))
    assert alerts[0].success == HOURLY


def test_the_summary_mentions_it():
    alert = _alert()
    assert channels_summary(alert.channels, alert.success) == (
        "in-app · sound · notification · 1 email · all-clear mail every 1 hour")
    assert "all-clear" not in channels_summary(alert.channels)


# --- the engine's call --------------------------------------------------------

@pytest.fixture
def store():
    s = Storage(":memory:")
    s.init_db()
    return s


def _saved(store, **kw) -> Alert:
    alert = _alert(**kw)
    alert.id = store.add_alert(alert)
    return alert


def test_an_hour_of_clean_checks_mails_the_alerts_recipients_once(store):
    alert = _saved(store)
    sent = []
    for i in range(1, 6):
        err = successmail.after_check(store, alert, T0 + timedelta(minutes=15 * i),
                                      clean=True, rows=0,
                                      send=lambda *a: sent.append(a))
        assert err is None
    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == ["desk@x.com"] and "all clear" in subject


def test_the_period_is_kept_between_ticks(store):
    alert = _saved(store)
    successmail.after_check(store, alert, T0, clean=True, rows=0, send=None)
    successmail.after_check(store, alert, T0 + timedelta(minutes=5),
                            clean=False, rows=1, send=None)
    assert store.get_success_state(alert.id) == {
        "period_start": T0.isoformat(), "clean_checks": 1, "dirty": True}


def test_a_failing_mailer_is_reported_not_raised(store):
    alert = _saved(store, success=SuccessMail(enabled=True, mode="every_check"))

    def boom(*a):
        raise OSError("smtp down")

    err = successmail.after_check(store, alert, T0, clean=True, rows=0, send=boom)
    assert err == "Stuck orders: all-clear mail failed - smtp down"


def test_nothing_happens_when_it_is_off_or_there_is_nobody_to_mail(store):
    sent = []
    for alert in (_saved(store, name="a", success=SuccessMail()),
                  _saved(store, name="b", channels=Channels(),
                         success=SuccessMail(enabled=True, mode="every_check"))):
        successmail.after_check(store, alert, T0, clean=True, rows=0,
                                send=lambda *a: sent.append(a))
        assert store.get_success_state(alert.id) is None
    assert sent == []


def test_switching_it_off_forgets_the_open_period(store):
    alert = _saved(store)
    successmail.after_check(store, alert, T0, clean=True, rows=0, send=None)
    alert.success = SuccessMail(enabled=False)
    store.update_alert(alert)
    assert store.get_success_state(alert.id) is None


def test_deleting_the_alert_forgets_it_too(store):
    alert = _saved(store)
    successmail.after_check(store, alert, T0, clean=True, rows=0, send=None)
    store.delete_alert(alert.id)
    assert store.get_success_state(alert.id) is None


# --- the Builder --------------------------------------------------------------

def _notify(state: str) -> AppTest:
    at = AppTest.from_string(f'''
import streamlit as st
from kdbmonitor.ui import builder
st.session_state.update({state})
channels, rearm, success = builder._notify_block()
st.session_state["_success"] = success
''', default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_the_builder_offers_it_only_with_email():
    at = _notify('{"b_delivery": ["in_app"]}')
    assert not [c for c in at.checkbox if c.key == "b_succ_on"]
    assert at.session_state["_success"].enabled is False


def test_the_builder_produces_what_was_chosen():
    at = _notify('{"b_delivery": ["email"], "b_email": "desk@x.com", '
                 '"b_succ_on": True, "b_succ_when": "2 hours", '
                 '"b_succ_subj": "OK {alert}", "b_succ_body": ""}')
    assert at.session_state["_success"] == SuccessMail(
        enabled=True, mode="interval", interval_secs=7200, subject="OK {alert}",
        body="")


def test_the_builder_says_when_the_interval_is_not_longer_than_the_check():
    at = _notify('{"b_delivery": ["email"], "b_email": "desk@x.com", '
                 '"b_interval": 3600, "b_succ_on": True, '
                 '"b_succ_when": "15 minutes"}')
    assert any("after every clean check" in c.value for c in at.caption)
