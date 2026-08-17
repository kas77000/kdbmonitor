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

def test_the_filter_shows_everything_when_it_holds_nothing():
    assert dashboards.kept_groups(None, ["A", "B"]) == ["A", "B"]
    assert dashboards.kept_groups([], ["A", "B"]) == ["A", "B"]


def test_the_filter_drops_a_group_that_no_longer_exists():
    """Rename or dissolve a group and its name is left behind in session
    state; Streamlit refuses to draw a multiselect holding a dead option."""
    assert dashboards.kept_groups(["A", "gone"], ["A", "B"]) == ["A"]


def test_a_filter_left_holding_only_dead_groups_shows_everything():
    assert dashboards.kept_groups(["gone"], ["A", "B"]) == ["A", "B"]


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
