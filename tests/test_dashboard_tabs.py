"""The dashboard tab strip: the ones you opened, not the ones that exist.

Every saved dashboard used to get a pill, so a desk with fifteen of them got a
wall of pills several rows deep and no way to tell which mattered. A dashboard
is in the strip now because somebody opened it, and leaves when they close it.
Which ones are open lives in the URL, so a reload — or a link sent to somebody
— brings the same tabs back.
"""
import pytest
from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import Dashboard
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui.dashboards import next_active, parse_tabs, tab_label


# --- reading the URL ---------------------------------------------------------

def test_the_tabs_parameter_is_read_in_order():
    assert parse_tabs("3,1,7", known=[1, 3, 7]) == [3, 1, 7]


def test_a_dashboard_that_no_longer_exists_is_dropped_rather_than_argued_with():
    """A deleted dashboard, or a hand-edited link: open the tabs that do exist
    instead of an error page."""
    assert parse_tabs("3,99,7", known=[3, 7]) == [3, 7]


def test_a_repeated_id_opens_one_tab():
    assert parse_tabs("3,3,1", known=[1, 3]) == [3, 1]


def test_rubbish_in_the_parameter_opens_nothing():
    for raw in ("", None, "   ", "abc", ",,,"):
        assert parse_tabs(raw, known=[1, 2]) == []


# --- closing ----------------------------------------------------------------

def test_closing_a_tab_lands_on_the_one_to_its_left():
    assert next_active([1, 2, 3], closing=3) == 2
    assert next_active([1, 2, 3], closing=2) == 1


def test_closing_the_first_tab_lands_on_the_one_to_its_right():
    assert next_active([1, 2, 3], closing=1) == 2


def test_closing_the_last_tab_open_lands_nowhere():
    """Nowhere is the gallery — the browser's new-tab page."""
    assert next_active([1], closing=1) is None


# --- labels -----------------------------------------------------------------

def test_a_long_name_is_shortened_to_tab_width():
    label = tab_label("Equity active orders by algo and venue", set())
    assert len(label) <= 22 and label.endswith("…")


def test_a_short_name_is_left_alone():
    assert tab_label("Volume profile", set()) == "Volume profile"


def test_two_dashboards_that_shorten_the_same_stay_tellable_apart():
    first = tab_label("Equity active orders by algo", set())
    second = tab_label("Equity active orders by venue", {first})
    assert first != second


def test_a_nameless_dashboard_still_gets_a_tab():
    assert tab_label("   ", set()) == "Untitled"


# --- through the page --------------------------------------------------------

@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("tabs") / "app.db")
    store = Storage(path)
    store.init_db()
    for spec in demo_connection_specs():
        store.add_connection(spec)
    for name in ("Alpha desk", "Bravo desk", "Charlie desk"):
        store.add_dashboard(Dashboard(id=None, name=name))
    return path


# AppTest cannot round-trip a pills widget across a rerun (see
# test_pdf_preview_toggle.py, which stubs it for the same reason), so anything
# that clicks its way through the page swaps in a stand-in that answers with
# whatever the strip put in session state — which is what the real widget does
# when nobody has clicked it.
_STUB_PILLS = ('st.pills = lambda label, options, **kw: '
               'st.session_state.get(kw.get("key"))\n')


def _script(db_path: str, seed: str = "", stub_pills: bool = False) -> str:
    """The Dashboards page, with the URL seeded on the *first* run only.

    Seeding it every run would put the query params back after each click, so
    every close button in these tests would look as if it had done nothing.
    """
    return f'''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboards

store = Storage(r"{db_path}")
store.init_db()
{_STUB_PILLS if stub_pills else ""}
if not st.session_state.get("_seeded"):
    st.session_state["_seeded"] = True
{seed or "    pass"}
dashboards.render(store, ConnectionManager())
'''


def _seed(dash: str | None = None, tabs: str | None = None,
          extra: str = "") -> str:
    lines = []
    if dash is not None:
        lines.append(f'    st.query_params["dash"] = "{dash}"')
    if tabs is not None:
        lines.append(f'    st.query_params["tabs"] = "{tabs}"')
    if extra:
        lines.append(f"    {extra}")
    return "\n".join(lines)


def _run(db_path: str, seed: str = "", stub_pills: bool = False) -> AppTest:
    at = AppTest.from_string(_script(db_path, seed, stub_pills),
                             default_timeout=90).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _tabs(at) -> list[str]:
    groups = [g for g in at.button_group if g.key == "dash_tabs"]
    return [o.content for o in groups[0].options] if groups else []


def _qp(at, key: str) -> str | None:
    """One query parameter — AppTest hands them back as lists."""
    if key not in at.query_params:
        return None
    value = at.query_params[key]
    return value[0] if isinstance(value, list) else value


def _click(at, key: str) -> AppTest:
    [b for b in at.button if b.key == key][0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_the_strip_holds_only_the_dashboards_that_are_open(db):
    at = _run(db, _seed(dash="2", tabs="2,3"))
    assert _tabs(at) == ["Bravo desk", "Charlie desk"]     # not Alpha desk


def test_opening_one_from_a_link_gives_it_a_tab(db):
    """A bookmark of ?dash=1 alone still has to show something."""
    assert _tabs(_run(db, _seed(dash="1"))) == ["Alpha desk"]


def test_the_gallery_offers_to_open_the_ticked_ones(db):
    at = _run(db)
    assert "Open selected" in [b.label for b in at.button]
    assert sum(1 for c in at.checkbox if c.key.startswith("gal_pick_")) == 3


def test_ticking_two_and_opening_them_gives_two_tabs(db):
    at = AppTest.from_string(_script(db, stub_pills=True),
                             default_timeout=90).run()
    for checkbox in at.checkbox:
        if checkbox.key in ("gal_pick_1", "gal_pick_3"):
            checkbox.set_value(True)
    at.run()
    selected = [b for b in at.button if b.label.startswith("Open selected")]
    assert selected[0].label == "Open selected (2)"
    selected[0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert _qp(at, "tabs") == "1,3"
    assert _qp(at, "dash") == "1"          # lands on the first one asked for


def test_the_gallery_says_which_dashboards_are_already_open(db):
    at = _run(db, _seed(tabs="2"))
    printed = " ".join(str(m.value) for m in at.markdown)
    assert "Open]" in printed              # the badge on Bravo desk's card


def test_closing_a_tab_drops_what_it_was_holding(db):
    """Closing means closing: the frames, the built PDF and any uploaded file
    go with the tab, rather than being held for a tab nobody has open."""
    at = _run(db, _seed(dash="2", tabs="2,3",
                        extra='st.session_state["dash_frames_3"] = {"as_of": None}'),
              stub_pills=True)
    _click(at, "tabclose_3")
    assert _qp(at, "tabs") == "2"
    assert "dash_frames_3" not in at.session_state


def test_closing_the_showing_tab_moves_to_a_neighbour(db):
    at = _run(db, _seed(dash="3", tabs="2,3"), stub_pills=True)
    _click(at, "tabclose_3")
    assert _qp(at, "dash") == "2"


def test_closing_the_last_tab_returns_to_the_gallery(db):
    at = _run(db, _seed(dash="2", tabs="2"), stub_pills=True)
    _click(at, "tabclose_2")
    assert _qp(at, "dash") is None
    assert any("Open selected" in b.label for b in at.button)   # the gallery


def test_going_back_to_the_gallery_keeps_the_tabs(db):
    """The gallery is the new-tab page, not closing the window."""
    at = _run(db, _seed(dash="2", tabs="2,3"), stub_pills=True)
    [b for b in at.button if b.label == "All dashboards"][0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert _qp(at, "dash") is None and _qp(at, "tabs") == "2,3"


def test_close_others_leaves_the_one_you_are_looking_at(db):
    at = _run(db, _seed(dash="2", tabs="1,2,3"), stub_pills=True)
    _click(at, "tabclose_others")
    assert _qp(at, "tabs") == "2" and _qp(at, "dash") == "2"


def test_close_all_empties_the_strip_and_the_url(db):
    at = _run(db, _seed(dash="2", tabs="1,2,3"), stub_pills=True)
    _click(at, "tabclose_all")
    assert _qp(at, "dash") is None and _qp(at, "tabs") is None


def test_a_long_strip_stays_one_row(db):
    """Not something a test can see, so it checks the two things that make it
    so: the strip is styled through the widget's own key, and every label is
    short enough that fifteen of them are still fifteen tabs."""
    from kdbmonitor.ui.dashboards import TABS_KEY, _TAB_CSS

    assert f".st-key-{TABS_KEY}" in _TAB_CSS
    assert "flex-wrap: nowrap" in _TAB_CSS and "overflow-x: auto" in _TAB_CSS
    at = _run(db, _seed(dash="1", tabs="1,2,3"))
    assert all(len(label) <= 22 for label in _tabs(at))
    assert any(f".st-key-{TABS_KEY}" in str(m.value) for m in at.markdown)
