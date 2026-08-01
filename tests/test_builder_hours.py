"""The Active hours controls, and the crash they used to cause.

Streamlit refuses to have a widget's state assigned once that widget has been
created in the same run. Every control in this form is created *before* the
button that rearranges it, because the button sits beside what it acts on — so
"At a moment" filling in the To time, and Remove shuffling the rows below it
up by one, replaced the whole form with a red traceback. The values are queued
now and written at the top of the next run.
"""
from datetime import time

import pytest
from streamlit.testing.v1 import AppTest

from kdbmonitor.core import zones
from kdbmonitor.ui import builder


def _run(body: str) -> AppTest:
    """The Active hours block, drawn the way the page draws it."""
    at = AppTest.from_string(f'''
import streamlit as st
from datetime import time
from kdbmonitor.ui import builder

builder._apply_pending()
{body}
st.session_state.setdefault("b_sched_on", True)
st.session_state.setdefault("b_sched_n", 1)
schedule = builder._schedule_block()
st.session_state["_windows"] = [(w.start, w.end) for w in schedule.windows]
st.session_state["_tz"] = schedule.tz
''', default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _click(at: AppTest, key: str) -> AppTest:
    [b for b in at.button if b.key == key][0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


# --- the crash ---------------------------------------------------------------

def test_at_a_moment_fills_the_to_time_instead_of_crashing():
    """The reported bug: a red traceback where the form had been."""
    at = _run('st.session_state.setdefault("b_sched_s_0", time(16, 30))')
    _click(at, "b_sched_mom_0")
    assert at.session_state["b_sched_e_0"] == time(16, 31)
    assert at.session_state["_windows"] == [("16:30", "16:31")]


def test_at_a_moment_before_a_from_time_says_so_rather_than_failing():
    at = _run("")
    _click(at, "b_sched_mom_0")
    assert any("From" in str(t.value) for t in at.toast)


def test_a_moment_at_the_end_of_the_day_wraps_to_midnight():
    at = _run('st.session_state.setdefault("b_sched_s_0", time(23, 59))')
    _click(at, "b_sched_mom_0")
    assert at.session_state["b_sched_e_0"] == time(0, 0)


def test_removing_a_window_shuffles_the_rest_up_without_crashing():
    at = _run('st.session_state.setdefault("b_sched_n", 3)\n'
              'st.session_state.setdefault("b_sched_s_0", time(9, 0))\n'
              'st.session_state.setdefault("b_sched_e_0", time(10, 0))\n'
              'st.session_state.setdefault("b_sched_s_1", time(16, 30))\n'
              'st.session_state.setdefault("b_sched_e_1", time(16, 31))\n'
              'st.session_state.setdefault("b_sched_s_2", time(17, 45))\n'
              'st.session_state.setdefault("b_sched_e_2", time(18, 0))')
    _click(at, "b_sched_rm_0")           # drop the first
    assert at.session_state["_windows"] == [("16:30", "16:31"), ("17:45", "18:00")]


def test_a_queued_value_is_written_once_and_not_again():
    at = _run('st.session_state.setdefault("b_sched_s_0", time(16, 30))')
    _click(at, "b_sched_mom_0")
    assert "b_pending" not in at.session_state or not at.session_state["b_pending"]


# --- the controls ------------------------------------------------------------

def test_the_times_are_pickers_rather_than_text_boxes():
    at = _run("")
    keys = [el.key for el in at.time_input]
    assert "b_sched_s_0" in keys and "b_sched_e_0" in keys
    assert not [el for el in at.text_input if el.key == "b_sched_s_0"]


def test_the_pickers_step_by_the_minute_so_17_45_can_be_said():
    at = _run("")
    picker = [el for el in at.time_input if el.key == "b_sched_s_0"][0]
    assert picker.step == 60


def test_the_timezone_is_a_list_of_iana_ids_and_nothing_else():
    at = _run("")
    box = [el for el in at.selectbox if el.key == "b_sched_tz"][0]
    assert "Europe/London" in box.options and "Asia/Kolkata" in box.options
    # The spellings core.zones accepts from a *file* are not offered here.
    assert "GMT Standard Time" not in box.options
    assert "IST" not in box.options
    assert "UTC+05:30" not in box.options


def test_the_timezone_starts_on_this_machine_s_own():
    at = _run("")
    assert at.session_state["_tz"] == zones.local_iana()
    assert at.session_state["_tz"] in zones.iana_names()


def test_an_unset_window_asks_for_a_time_rather_than_complaining_about_one():
    at = _run("")
    said = " ".join(str(w.value) for w in at.warning)
    assert "set both a From and a To time" in said


# --- what a saved alert loads as ---------------------------------------------

def test_a_stored_zone_in_any_spelling_loads_as_an_iana_id():
    """An alert saved before the picker existed still runs — core.zones
    resolves every spelling — but only an IANA id can be selected."""
    assert builder._iana_or_local("Europe/London") == "Europe/London"
    assert builder._iana_or_local("GMT Standard Time") == "Europe/London"
    assert builder._iana_or_local("IST") == "Asia/Kolkata"


@pytest.mark.parametrize("stored", ["", "local", "UTC+05:30", "Middle Earth"])
def test_a_zone_that_cannot_be_selected_falls_back_to_this_machine(stored):
    """Including a bare offset, which is not a zone: it has no rulebook, so it
    cannot know its own daylight saving."""
    assert builder._iana_or_local(stored) == zones.local_iana()


def test_the_machine_s_own_zone_is_always_a_real_iana_id():
    assert zones.local_iana() in zones.iana_names()
