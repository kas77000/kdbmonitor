"""Render every page through Streamlit's AppTest against a populated store.

A page can import cleanly and still blow up when Streamlit builds it — duplicate
widget keys, an argument value the version rejects, a bad index into an options
list. This walks all six pages with real connections, alerts and dashboards
present, which is the state that has actually produced those failures.
"""
import pytest
from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Row, Transform, Widget
from kdbmonitor.core.models import (
    Alert, Channels, Connection, Filter, RearmPolicy, Step, TriggerCondition,
)
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.storage import Storage

PAGES = ["monitor", "builder", "dashboards", "reports", "admin", "result"]


def _alert() -> Alert:
    return Alert(
        id=None, name="AAPL bid breakout", enabled=True, poll_interval_secs=15,
        steps=[Step(server="orders_demo", table="target", mode="form",
                    filters=[Filter(column="sym", op="=", value="AAPL",
                                    value_type="symbol")],
                    raw_qsql=None, output_name="step1")],
        trigger=TriggerCondition(type="has_rows"),
        channels=Channels(), rearm=RearmPolicy(), group="Desk")


def _dashboard() -> Dashboard:
    return Dashboard(
        id=None, name="Demo orders", description="render check", refresh_secs=0,
        datasets=[Dataset(
            name="by_algo", env="orders", table="target",
            transforms=[Transform(kind="groupby", params={
                "keys": ["algo"], "aggs": [
                    {"column": "orderId", "func": "nunique", "as": "n_orders"},
                    {"column": "qty", "func": "sum", "as": "order_qty"}]})])],
        rows=[
            Row(height_in=0.9, widgets=[
                Widget(type="kpi", dataset="by_algo", title="Orders",
                       spec={"column": "n_orders", "agg": "sum", "fmt": ",.0f"})]),
            Row(height_in=1.9, widgets=[
                Widget(type="table", dataset="by_algo", title="By algo",
                       spec={"labels": {"algo": "Algo"},
                             "formats": {"order_qty": ",.0f"}}),
                Widget(type="bar", dataset="by_algo", title="Qty",
                       spec={"x": "algo", "y": "order_qty", "orientation": "h"})]),
        ])


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("pages") / "app.db")
    s = Storage(path)
    s.init_db()
    for spec in demo_connection_specs():
        s.add_connection(spec)
    s.add_alert(_alert())
    s.add_dashboard(_dashboard())
    return path


def _script(db_path: str, page: str, extra: str = "") -> str:
    return f'''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import admin, builder, dashboards, monitor, reports, result

store = Storage(r"{db_path}")
store.init_db()
mgr = ConnectionManager()
{extra}
page = "{page}"
if page == "monitor":
    monitor.render(store, mgr)
elif page == "builder":
    builder.render(store, mgr)
elif page == "dashboards":
    dashboards.render(store, mgr)
elif page == "reports":
    reports.render(store, mgr)
elif page == "admin":
    admin.render(store, mgr)
else:
    result.render(store)
'''


def _keys(at) -> list[str]:
    keys = []
    for group in (at.selectbox, at.text_input, at.number_input, at.checkbox,
                  at.multiselect, at.text_area, at.button, at.radio):
        keys += [el.key for el in group if el.key]
    return keys


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(db, page):
    at = AppTest.from_string(_script(db, page), default_timeout=90).run()
    assert not at.exception, f"{page}: {[str(e.value) for e in at.exception]}"


@pytest.mark.parametrize("page", PAGES)
def test_page_has_no_duplicate_widget_keys(db, page):
    at = AppTest.from_string(_script(db, page), default_timeout=90).run()
    keys = _keys(at)
    duplicates = {k for k in keys if keys.count(k) > 1}
    assert not duplicates, f"{page}: duplicate keys {sorted(duplicates)}"


def test_an_open_dashboard_renders(db):
    at = AppTest.from_string(
        _script(db, "dashboards", 'st.query_params["dash"] = "1"'),
        default_timeout=90).run()
    assert not at.exception, [str(e.value) for e in at.exception]


@pytest.mark.parametrize("section", ["Data", "Layout", "Preview"])
def test_every_editor_section_renders(db, section):
    extra = ('st.session_state["dash_mode"] = "edit"\n'
             'st.session_state["dash_edit_id"] = 1\n'
             f'st.session_state["dash_edit_section"] = "{section}"')
    at = AppTest.from_string(_script(db, "dashboards", extra),
                             default_timeout=90).run()
    assert not at.exception, f"{section}: {[str(e.value) for e in at.exception]}"


def test_the_builder_renders_while_editing_an_alert(db):
    """Editing loads session state the create path never sets."""
    extra = ('from kdbmonitor.ui import builder as _b\n'
             '_b._load_edit(store.list_alerts()[0])')
    at = AppTest.from_string(_script(db, "builder", extra),
                             default_timeout=90).run()
    assert not at.exception, [str(e.value) for e in at.exception]


# --- button widths ----------------------------------------------------------

def _buttons_with_width(block, fraction, out):
    """Every button in the tree, with its share of the page width.

    Columns nest, so a button's width is the product of every column share
    between it and the page.
    """
    children = list(getattr(block, "children", {}).values())
    weights = [getattr(getattr(c, "proto", None), "weight", 0.0) for c in children]
    total = sum(weights) or 1.0
    for child, weight in zip(children, weights):
        if hasattr(child, "children"):
            _buttons_with_width(child, fraction * ((weight / total) if weight else 1.0),
                                out)
        elif getattr(child, "type", "") == "button":
            out.append((fraction, child))
    return out


PAGE_PX = 1400          # a wide-layout content area on a large monitor


@pytest.mark.parametrize("page", PAGES)
def test_no_button_is_far_wider_than_its_label(db, page):
    """A lone action button stretched across its column printed a 636px
    'Generate report'. Only a row of sibling buttons should fill its columns,
    and even then not by a mile.

    The width in question is the *container's*: a button only takes all of it
    when it asks to, which is exactly the setting under review. A content-width
    button in a wide column is fine and is not counted.
    """
    at = AppTest.from_string(_script(db, page), default_timeout=90).run()
    too_wide = []
    for fraction, el in _buttons_with_width(at._tree, 1.0, []):
        label = (el.label or "").strip()
        if not label or not getattr(el.proto, "use_container_width", False):
            continue                       # icon-only, or sized to its label
        natural = 9 * len(label) + 60      # roughly: glyphs plus padding
        if fraction * PAGE_PX > 2 * natural:
            too_wide.append(f"{label!r} stretched to {fraction * PAGE_PX:.0f}px "
                            f"for ~{natural}px of label")
    assert not too_wide, f"{page}: over-wide buttons: {too_wide}"


# --- navigation config ------------------------------------------------------

def test_the_default_page_declares_no_url_path():
    """st.navigation serves the default page at "/" and ignores its url_path, so
    declaring one advertises a URL that answers 'Page not found'."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    defaults = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Page"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        is_default = isinstance(kw.get("default"), ast.Constant) and kw["default"].value
        if is_default:
            defaults.append(kw)

    assert len(defaults) == 1, "expected exactly one default page"
    assert "url_path" not in defaults[0], \
        "the default page must not declare a url_path — it is served at '/'"


def test_every_non_default_page_has_a_url_path():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Page"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        is_default = isinstance(kw.get("default"), ast.Constant) and kw["default"].value
        if not is_default:
            assert "url_path" in kw, f"page {ast.dump(node)[:60]} has no url_path"
