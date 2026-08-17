"""Dashboards bucketed by group, the way alerts already are.

A gallery nobody has grouped is drawn exactly as it was before the feature
existed — flat, with no headers and no filter. That is asserted here rather
than assumed, because it is the promise made to everyone who never uses this.
"""
import pytest
from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import Dashboard
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboards


def _d(did: int, name: str, group: str = "") -> Dashboard:
    return Dashboard(id=did, name=name, group=group)


# --- which buckets, and whether to draw them --------------------------------

def test_an_empty_gallery_is_drawn_flat():
    assert dashboards.shows_groups(dashboards.group_buckets([])) is False


def test_a_gallery_nobody_grouped_is_drawn_flat():
    saved = [_d(1, "a"), _d(2, "b")]
    assert dashboards.shows_groups(dashboards.group_buckets(saved)) is False


def test_one_grouped_dashboard_turns_the_headers_on():
    saved = [_d(1, "a", "Equities"), _d(2, "b")]
    assert dashboards.shows_groups(dashboards.group_buckets(saved)) is True


def test_buckets_are_alphabetical_with_ungrouped_last():
    saved = [_d(1, "a"), _d(2, "b", "Ops"), _d(3, "c", "Equities")]
    assert [g for g, _ in dashboards.group_buckets(saved)] == [
        "Equities", "Ops", "Ungrouped"]


def test_a_bucket_keeps_the_order_it_was_given():
    saved = [_d(1, "alpha", "Ops"), _d(2, "bravo", "Ops")]
    names = [d.name for d in dashboards.group_buckets(saved)[0][1]]
    assert names == ["alpha", "bravo"]


def test_a_blank_group_is_ungrouped_however_it_is_spelled():
    saved = [_d(1, "a", "   "), _d(2, "b", "")]
    assert [g for g, _ in dashboards.group_buckets(saved)] == ["Ungrouped"]


# --- the filter --------------------------------------------------------------
#
# The filter is read as "everything except what you unticked", not as "only
# what you ticked": `known` is the groups that existed when it was last drawn,
# so what the reader excluded is `known - held`. Renaming a group, or moving a
# dashboard into a new one, invents a name nobody has had the chance to tick —
# reading the filter the other way round hid the very dashboards the reader had
# just re-labelled.

def test_the_filter_shows_everything_when_it_holds_nothing():
    assert dashboards.kept_groups(None, ["A", "B"], None) == ["A", "B"]
    assert dashboards.kept_groups([], ["A", "B"], ["A", "B"]) == ["A", "B"]


def test_the_filter_drops_a_group_that_no_longer_exists():
    """Rename or dissolve a group and its name is left behind in session
    state; Streamlit refuses to draw a multiselect holding a dead option."""
    assert dashboards.kept_groups(["A", "gone"], ["A", "B"],
                                 ["A", "B", "gone"]) == ["A"]


def test_a_filter_left_holding_only_dead_groups_shows_everything():
    assert dashboards.kept_groups(["gone"], ["A", "B"], ["gone"]) == ["A", "B"]


def test_a_group_the_reader_unticked_stays_hidden():
    assert dashboards.kept_groups(["Ops"], ["Equities", "Ops"],
                                 ["Equities", "Ops"]) == ["Ops"]


def test_a_group_renamed_under_the_filter_is_still_shown():
    """The defect this reading fixes: rename Equities and its two dashboards
    used to disappear until you went and ticked the new name."""
    assert dashboards.kept_groups(["Equities", "Ops"], ["Ops", "US Equities"],
                                  ["Equities", "Ops"]) == ["Ops", "US Equities"]


def test_a_dashboard_moved_into_a_brand_new_group_is_still_shown():
    assert dashboards.kept_groups(["Ops"], ["Ops", "Rates"],
                                  ["Ops"]) == ["Ops", "Rates"]


def test_with_nothing_known_yet_everything_is_shown():
    """First run after an upgrade: session state holds a selection but no
    record of what existed beside it."""
    assert dashboards.kept_groups(["Ops"], ["Equities", "Ops"], None) == [
        "Equities", "Ops"]


# --- through the page --------------------------------------------------------

def _script(db_path: str) -> str:
    return f'''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboards

store = Storage(r"{db_path}")
store.init_db()
dashboards.render(store, ConnectionManager())
'''


def _run(db_path: str) -> AppTest:
    at = AppTest.from_string(_script(db_path), default_timeout=90).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _seeded(tmp_path, groups) -> str:
    """A db holding one dashboard per (name, group) pair given."""
    path = str(tmp_path / "app.db")
    store = Storage(path)
    store.init_db()
    for name, group in groups:
        store.add_dashboard(Dashboard(id=None, name=name, group=group))
    return path


@pytest.fixture()
def grouped_db(tmp_path):
    return _seeded(tmp_path, [("Order flow", "Equities"),
                              ("Fill quality", "Equities"),
                              ("Feed latency", "Ops"),
                              ("Scratch", "")])


@pytest.fixture()
def flat_db(tmp_path):
    return _seeded(tmp_path, [("Order flow", ""), ("Scratch", "")])


def _filter(at):
    return [m for m in at.multiselect if m.key == "gal_group_filter"]


def test_a_grouped_gallery_offers_a_filter_over_its_groups(grouped_db):
    at = _run(grouped_db)
    assert _filter(at), "expected a group filter"
    assert _filter(at)[0].value == ["Equities", "Ops", "Ungrouped"]


def test_an_ungrouped_gallery_is_the_page_it_always_was(flat_db):
    at = _run(flat_db)
    assert not _filter(at)
    assert sum(1 for c in at.checkbox if c.key.startswith("gal_pick_")) == 2


def test_every_dashboard_is_still_drawn_once_when_grouped(grouped_db):
    at = _run(grouped_db)
    ticks = [c.key for c in at.checkbox if c.key.startswith("gal_pick_")]
    assert len(ticks) == 4 and len(set(ticks)) == 4


def test_filtering_a_group_out_stops_drawing_its_dashboards(grouped_db):
    at = _run(grouped_db)
    _filter(at)[0].set_value(["Ops"]).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert len([c for c in at.checkbox if c.key.startswith("gal_pick_")]) == 1


def test_a_ticked_dashboard_that_is_filtered_out_is_not_offered_to_open(grouped_db):
    at = _run(grouped_db)
    for checkbox in at.checkbox:
        if checkbox.key.startswith("gal_pick_"):
            checkbox.set_value(True)
    at.run()
    assert [b for b in at.button if b.label == "Open selected (4)"]
    _filter(at)[0].set_value(["Ops"]).run()
    at.run()
    assert [b for b in at.button if b.label == "Open selected (1)"]


def test_the_gallery_renders_with_no_dashboards_at_all(tmp_path):
    at = _run(_seeded(tmp_path, []))
    assert not _filter(at)


# --- what a group picker offers ----------------------------------------------

def test_the_picker_offers_none_the_groups_in_use_and_a_new_one():
    assert dashboards.group_options(["Ops", "Equities"], "") == [
        "(no group)", "Equities", "Ops", "➕ New group…"]


def test_a_group_only_this_draft_uses_is_still_selectable():
    """Type a new group in the editor and the rerun that follows must find it
    in the options, or the picker jumps off the choice just made."""
    assert dashboards.group_options([], "Ops") == [
        "(no group)", "Ops", "➕ New group…"]


def test_a_group_already_in_use_is_not_offered_twice():
    assert dashboards.group_options(["Ops"], "Ops") == [
        "(no group)", "Ops", "➕ New group…"]


def test_a_blank_current_group_adds_nothing():
    assert dashboards.group_options([], "   ") == ["(no group)", "➕ New group…"]


def test_the_groups_in_use_are_the_ones_dashboards_wear(tmp_path):
    path = _seeded(tmp_path, [("a", "Ops"), ("b", "  Ops  "), ("c", ""),
                              ("d", "Equities")])
    store = Storage(path)
    assert dashboards.existing_groups(store) == ["Equities", "Ops"]


# --- the picker in the editor ------------------------------------------------

def _editor_script(db_path: str, dash_id: int) -> str:
    """The editor, on whichever dashboard `_want` names.

    `_draft` reloads whenever the id it holds is not the one asked for, so
    switching dashboards is one session-state write and a rerun — which is
    exactly what the Edit button does from the gallery.

    The Section control is stubbed for the same reason the tab strip's pills
    are elsewhere in this suite: AppTest cannot round-trip a segmented_control
    across a rerun, and every test here reruns. conftest puts it back.
    """
    return f'''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboard_editor

st.segmented_control = lambda *a, **k: "Data"
store = Storage(r"{db_path}")
store.init_db()
st.session_state["dash_mode"] = "edit"
st.session_state["dash_edit_id"] = st.session_state.get("_want", {dash_id})
dashboard_editor.render(store, ConnectionManager())
'''


def _run_editor(db_path: str, dash_id: int) -> AppTest:
    at = AppTest.from_string(_editor_script(db_path, dash_id),
                             default_timeout=90).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _group_box(at):
    return next(el for el in at.selectbox if el.label == "Group")


@pytest.fixture()
def editable_db(tmp_path):
    """Dashboard 1 is in Equities, dashboard 2 is in no group."""
    return _seeded(tmp_path, [("Order flow", "Equities"), ("Scratch", "")])


def test_the_editor_shows_the_group_a_dashboard_is_in(editable_db):
    assert _group_box(_run_editor(editable_db, 1)).value == "Equities"


def test_a_dashboard_with_no_group_shows_no_group(editable_db):
    assert _group_box(_run_editor(editable_db, 2)).value == "(no group)"


def test_opening_a_second_dashboard_does_not_carry_the_firsts_group(editable_db):
    """The picker reads the draft rather than a session key. A keyed one would
    still say 'Equities' here and write it into the dashboard that has none."""
    at = _run_editor(editable_db, 1)
    assert _group_box(at).value == "Equities"
    at.session_state["_want"] = 2
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert _group_box(at).value == "(no group)"
    assert at.session_state["dash_draft"].group == ""


def test_choosing_a_group_writes_it_into_the_draft(editable_db):
    at = _run_editor(editable_db, 2)
    _group_box(at).set_value("Equities").run()
    assert at.session_state["dash_draft"].group == "Equities"


def test_choosing_no_group_clears_the_draft_group(editable_db):
    at = _run_editor(editable_db, 1)
    _group_box(at).set_value("(no group)").run()
    assert at.session_state["dash_draft"].group == ""


def test_a_new_group_is_typed_and_lands_in_the_draft(editable_db):
    at = _run_editor(editable_db, 2)
    _group_box(at).set_value("➕ New group…").run()
    box = next(el for el in at.text_input if el.label == "New group name")
    box.set_value("  Rates  ").run()
    assert at.session_state["dash_draft"].group == "Rates"


def test_the_choice_the_editor_makes_is_what_save_would_store(editable_db):
    """Save is `store.update_dashboard(draft)` and nothing else, so the draft
    holding the group is the whole of the guarantee. Asserted this way rather
    than by clicking Save, which a dashboard with no datasets cannot reach —
    validate() disables it until there is something to draw."""
    at = _run_editor(editable_db, 2)
    _group_box(at).set_value("Equities").run()
    store = Storage(editable_db)
    store.update_dashboard(at.session_state["dash_draft"])
    assert store.get_dashboard(2).group == "Equities"


# --- moving one dashboard ----------------------------------------------------

def test_a_card_offers_to_move_its_dashboard(grouped_db):
    at = _run(grouped_db)
    assert sum(1 for b in at.button if b.key and b.key.startswith("gmv_go_")) == 4


def test_moving_a_dashboard_into_another_group(grouped_db):
    at = _run(grouped_db)
    store = Storage(grouped_db)
    scratch = next(d for d in store.list_dashboards() if d.name == "Scratch")
    at.selectbox(key=f"gmv_sel_{scratch.id}").set_value("Ops").run()
    at.button(key=f"gmv_go_{scratch.id}").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert Storage(grouped_db).get_dashboard(scratch.id).group == "Ops"


def test_moving_a_dashboard_out_of_its_group(grouped_db):
    at = _run(grouped_db)
    store = Storage(grouped_db)
    flow = next(d for d in store.list_dashboards() if d.name == "Order flow")
    at.selectbox(key=f"gmv_sel_{flow.id}").set_value("(no group)").run()
    at.button(key=f"gmv_go_{flow.id}").click().run()
    assert Storage(grouped_db).get_dashboard(flow.id).group == ""


def test_moving_a_dashboard_into_a_group_that_does_not_exist_yet(grouped_db):
    at = _run(grouped_db)
    store = Storage(grouped_db)
    scratch = next(d for d in store.list_dashboards() if d.name == "Scratch")
    at.selectbox(key=f"gmv_sel_{scratch.id}").set_value("➕ New group…").run()
    at.text_input(key=f"gmv_new_{scratch.id}").set_value("Rates").run()
    at.button(key=f"gmv_go_{scratch.id}").click().run()
    assert Storage(grouped_db).get_dashboard(scratch.id).group == "Rates"


def test_a_move_picker_holding_a_group_that_has_gone_does_not_break_the_page(
        grouped_db):
    """Pick a group in one card's popover, dissolve it elsewhere, and the page
    still draws: Streamlit refuses a selectbox whose stored value is not an
    option any more."""
    at = _run(grouped_db)
    store = Storage(grouped_db)
    scratch = next(d for d in store.list_dashboards() if d.name == "Scratch")
    at.selectbox(key=f"gmv_sel_{scratch.id}").set_value("Ops").run()
    store.rename_dashboard_group("Ops", "")          # dissolved behind its back
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]


# --- renaming and dissolving whole groups ------------------------------------

def test_an_ungrouped_gallery_offers_nothing_to_manage(flat_db):
    at = _run(flat_db)
    assert not [t for t in at.text_input if t.key and
                t.key.startswith("dgrp_ren_")]


def test_a_grouped_gallery_lists_its_groups_to_manage(grouped_db):
    at = _run(grouped_db)
    keys = sorted(t.key for t in at.text_input if t.key and
                  t.key.startswith("dgrp_ren_"))
    assert keys == ["dgrp_ren_Equities", "dgrp_ren_Ops"]


def test_renaming_a_group_relabels_every_dashboard_in_it(grouped_db):
    at = _run(grouped_db)
    at.text_input(key="dgrp_ren_Equities").set_value("US Equities").run()
    at.button(key="dgrp_renbtn_Equities").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    groups = {d.name: d.group for d in Storage(grouped_db).list_dashboards()}
    assert groups == {"Order flow": "US Equities", "Fill quality": "US Equities",
                      "Feed latency": "Ops", "Scratch": ""}


def test_dissolving_a_group_sends_its_dashboards_back_to_ungrouped(grouped_db):
    at = _run(grouped_db)
    at.button(key="dgrp_dis_Ops").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    latency = next(d for d in Storage(grouped_db).list_dashboards()
                   if d.name == "Feed latency")
    assert latency.group == ""


def test_renaming_a_group_onto_another_merges_them(grouped_db):
    at = _run(grouped_db)
    at.text_input(key="dgrp_ren_Ops").set_value("Equities").run()
    at.button(key="dgrp_renbtn_Ops").click().run()
    groups = {d.group for d in Storage(grouped_db).list_dashboards()}
    assert groups == {"Equities", ""}


def _group_headers(at) -> list[str]:
    """The group expander labels on the gallery, Manage groups excluded.

    An expander carrying an ``icon`` arrives in the AppTest tree as a "status"
    node, not an "expander" one — ``at.get("expander")`` finds none of these.
    """
    return [e.proto.label for e in at.get("status")
            if not e.proto.label.startswith("Manage groups")]


def test_renaming_a_group_does_not_hide_the_dashboards_it_renamed(grouped_db):
    """Found by driving the real page: the filter held 'Equities', the rename
    made it 'US Equities', and two dashboards left the screen."""
    at = _run(grouped_db)
    assert _group_headers(at) == ["**Equities** · 2", "**Ops** · 1",
                                  "**Ungrouped** · 1"]
    at.text_input(key="dgrp_ren_Equities").set_value("US Equities").run()
    at.button(key="dgrp_renbtn_Equities").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    # Alphabetical, with Ungrouped last however the alphabet falls.
    assert _group_headers(at) == ["**Ops** · 1", "**US Equities** · 2",
                                  "**Ungrouped** · 1"]
    assert _filter(at)[0].value == ["Ops", "US Equities", "Ungrouped"]


def test_moving_a_dashboard_into_a_new_group_does_not_hide_it(grouped_db):
    at = _run(grouped_db)
    store = Storage(grouped_db)
    scratch = next(d for d in store.list_dashboards() if d.name == "Scratch")
    at.selectbox(key=f"gmv_sel_{scratch.id}").set_value("➕ New group…").run()
    at.text_input(key=f"gmv_new_{scratch.id}").set_value("Rates").run()
    at.button(key=f"gmv_go_{scratch.id}").click().run()
    assert "**Rates** · 1" in _group_headers(at)


def test_a_group_the_reader_filtered_out_stays_out_across_a_rename(grouped_db):
    """Widening on a rename must not undo a filter the reader chose."""
    at = _run(grouped_db)
    _filter(at)[0].set_value(["Equities"]).run()
    assert _group_headers(at) == ["**Equities** · 2"]
    at.text_input(key="dgrp_ren_Equities").set_value("US Equities").run()
    at.button(key="dgrp_renbtn_Equities").click().run()
    assert _group_headers(at) == ["**US Equities** · 2"]


def test_the_gallery_puts_no_streamlit_complaint_on_the_page(grouped_db):
    """Streamlit warns on the page itself when a widget is given both a default
    and a session-state value. It is not an exception, so nothing else here
    would have noticed the yellow box a reader was left looking at.

    The delete confirmations are st.warning too, hence the narrow assertion.
    """
    at = _run(grouped_db)
    complaints = [w.value for w in at.warning if "Session State" in w.value]
    assert complaints == []
    at.text_input(key="dgrp_ren_Equities").set_value("US Equities").run()
    at.button(key="dgrp_renbtn_Equities").click().run()
    assert [w.value for w in at.warning if "Session State" in w.value] == []
