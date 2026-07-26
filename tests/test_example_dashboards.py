"""The shipped example bundles must import, and the demo one must actually run.

Guards a mistake that is easy to make and invisible in unit tests: shipping an
example whose columns do not exist on the servers the README tells you to load,
so a new user's first dashboard is a page of error panels.
"""
from datetime import date, datetime
from pathlib import Path

import pytest

from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.dashpdf import dashboard_to_pdf_bytes
from kdbmonitor.core.dataset import run_datasets
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.portability import import_dashboards_json
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.timectx import resolve

EXAMPLES = Path(__file__).resolve().parent.parent / "docs" / "examples"
DEMO = EXAMPLES / "demo_orders_dashboard.json"
SHORT_SELL = EXAMPLES / "short_sell_dashboard.json"


def _load(path: Path):
    return import_dashboards_json(path.read_text(encoding="utf-8"))[0]


@pytest.fixture()
def demo_store(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    for spec in demo_connection_specs():
        s.add_connection(spec)
    return s


def test_both_examples_exist_and_import():
    assert _load(DEMO).name == "Demo orders"
    assert _load(SHORT_SELL).name == "Short sell"


def test_examples_import_without_ids_so_they_create_fresh_rows():
    assert _load(DEMO).id is None
    assert _load(SHORT_SELL).id is None


def test_the_demo_example_runs_clean_against_the_demo_servers(demo_store):
    dash = _load(DEMO)
    results = run_datasets(dash, demo_store, ConnectionManager(), date.today())
    for name, res in results.items():
        assert res.error is None, f"dataset '{name}': {res.error}"
        assert res.row_count > 0


def test_no_widget_in_the_demo_example_renders_as_an_error(demo_store):
    """The README tells a new user to import this one — it must not show a page
    of red panels."""
    dash = _load(DEMO)
    results = run_datasets(dash, demo_store, ConnectionManager(), date.today())
    broken = [build_plot_model(w, results)
              for row in dash.rows for w in row.widgets]
    assert [pm.error for pm in broken if pm.kind == "error"] == []


def test_the_demo_example_also_works_historically(demo_store):
    dash = _load(DEMO)
    dash.time_context = {"mode": "historical",
                         "range": {"kind": "preset", "name": "last_7d"}}
    results = run_datasets(dash, demo_store, ConnectionManager(), date.today())
    res = results["by_algo"]
    assert res.error is None
    assert "date within" in res.qsql          # routed to the historical server


def test_the_demo_example_renders_a_pdf(demo_store):
    dash = _load(DEMO)
    results = run_datasets(dash, demo_store, ConnectionManager(), date.today())
    out = dashboard_to_pdf_bytes(dash, results,
                                 resolve(dash.time_context, date.today()),
                                 datetime(2026, 7, 26, 9, 15))
    assert out.startswith(b"%PDF")


def test_the_short_sell_example_matches_the_acceptance_definition():
    """The shipped bundle must not drift from the tested dashboard."""
    from tests.test_short_sell_acceptance import short_sell_dashboard
    shipped, built = _load(SHORT_SELL), short_sell_dashboard()
    shipped.id = built.id = None
    assert shipped == built


# --- the shipped examples must pass the editor's own validation -------------

def test_the_demo_example_reports_no_problems(demo_store):
    """It ships with the app; if the editor flags it, the editor is wrong."""
    from kdbmonitor.ui.dashboard_editor import validate
    assert validate(_load(DEMO), demo_store) == []


def test_the_short_sell_example_reports_no_problems(tmp_path):
    from kdbmonitor.core.models import Connection
    from kdbmonitor.ui.dashboard_editor import validate
    from tests.test_short_sell_acceptance import RAW

    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    real_schema = {"target": list(RAW.columns)}
    s.add_connection(Connection(id=None, name="rdb", host="h", port=1,
                                kind="realtime", env="orders",
                                schema=real_schema))
    s.add_connection(Connection(id=None, name="hdb", host="h", port=2,
                                kind="historical", env="orders",
                                schema={"target": ["date"] + list(RAW.columns)}))
    assert validate(_load(SHORT_SELL), s) == []


def test_a_text_widgets_markdown_is_not_mistaken_for_a_column(demo_store):
    """Regression: 'markdown' is required but is prose, so the column-existence
    check flagged the whole sentence as a missing column."""
    from kdbmonitor.ui.dashboard_editor import validate
    dash = _load(DEMO)
    assert any(w.type == "text" for r in dash.rows for w in r.widgets), \
        "the demo example should still contain a text widget"
    assert not [m for m in validate(dash, demo_store) if "is not produced by" in m]
