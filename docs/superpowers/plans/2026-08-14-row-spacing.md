# Row Spacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a dashboard row ask for extra air above it and between its widgets, honoured identically on screen and in the PDF.

**Architecture:** Two float fields on `Row`, both in inches and both defaulting to `0.0` so nothing changes until asked. `core/dashpdf.py` adds the vertical gap in `paginate` and widens the per-row gutter; `ui/dashboards.py` draws the vertical gap as an empty fixed-height container and the horizontal gap as an empty spacer column, both from the same stored number.

**Tech Stack:** Python 3.11+, Streamlit 1.45 (`st.container(height=…)`, `st.columns` relative weights), matplotlib, pytest.

**Spec:** `docs/superpowers/specs/2026-08-14-row-spacing-design.md` — read §4 and §5 before Tasks 2 and 3.

## Global Constraints

- **No new dependencies.** `st.columns(gap=…)` takes only `"small" | "medium" | "large"` and is deliberately not used; the horizontal gap is an empty spacer column instead. No CSS.
- **Inches everywhere.** Both fields are `float` inches, matching `height_in` and the page geometry. Never pixels or points in the model; `row_height_px` converts at the screen boundary.
- **Both default to `0.0`, and both are additive** to today's spacing. A dashboard saved before this feature must render identically, to the page break.
- **Bounds `0.0`–`4.0`.** Negative gaps are not supported.
- **`kdbmonitor/core/` must never import Streamlit.**
- `from __future__ import annotations` at the top of any new module.
- Comments explain *why*. Match the surrounding house style.
- Full suite with `python -m pytest` from the repo root, green at every commit.

---

## File Structure

**Modified:**

| File | Change |
| --- | --- |
| `kdbmonitor/core/dashboard_models.py` | `Row.gap_above_in`, `Row.col_gap_in`, a `_float` coercion, and `row_from_dict` reading both. |
| `kdbmonitor/core/dashpdf.py` | `row_gutter(row)`; `paginate` leads with the gap; `_widget_width_in`, `_render_page` and `fits_width` use the row's gutter. |
| `kdbmonitor/ui/common.py` | `column_spec(row)` — pure weight arithmetic, shared by the live view and the editor preview. Its home because that module already declares itself "deliberately Streamlit-free and pure so they can be unit-tested without a running app", which is exactly what this arithmetic wants. |
| `kdbmonitor/ui/dashboards.py` | `render_rows` draws the vertical spacer and uses `column_spec`. |
| `kdbmonitor/ui/dashboard_editor.py` | Two controls in the row header; the preview uses `column_spec`. |

**Created:** `tests/test_row_spacing.py`

**Task order:** 1 → 2 → 3 → 4. Strictly sequential; each depends on the one before.

---

## Task 1: The model

**Files:**
- Modify: `kdbmonitor/core/dashboard_models.py` (`Row` ~line 133, `row_from_dict` ~line 390)
- Test: `tests/test_row_spacing.py`

**Interfaces:**
- Produces:
  - `Row(widgets, height_in=2.5, gap_above_in=0.0, col_gap_in=0.0)`
  - `_float(value, default: float) -> float` — module-private coercion

- [ ] **Step 1: Write the failing test**

Create `tests/test_row_spacing.py`:

```python
"""Air above a row, and between the widgets inside it.

A dashboard built in sections reads as one undifferentiated column: the second
heading sits hard against the first table, closer to the section it ends than
to the one it introduces. These tests hold the two fields that fix it, and hold
them to costing nothing until somebody asks.
"""
from dataclasses import replace

import pytest

from kdbmonitor.core.dashboard_models import (
    Dashboard, Row, Widget, dashboard_from_json, dashboard_to_json,
    row_from_dict,
)


def _row(**kw) -> Row:
    base = dict(widgets=[Widget(type="table", dataset="ds", title="T")],
                height_in=2.0)
    base.update(kw)
    return Row(**base)


# --- the model ---------------------------------------------------------------

def test_a_new_row_asks_for_no_extra_space():
    """0.0 means "exactly what this app did yesterday". Every existing
    dashboard depends on it."""
    row = Row()
    assert row.gap_above_in == 0.0
    assert row.col_gap_in == 0.0


def test_both_gaps_survive_a_save_and_a_load():
    board = Dashboard(id=None, name="d", rows=[
        _row(gap_above_in=0.6, col_gap_in=0.25)])
    back = dashboard_from_json(dashboard_to_json(board))
    assert back.rows[0].gap_above_in == 0.6
    assert back.rows[0].col_gap_in == 0.25


def test_a_row_stored_before_these_existed_loads_with_no_gaps():
    """No migration: an absent key reads as 0.0, like every other."""
    row = row_from_dict({"widgets": [], "height_in": 3.0})
    assert (row.gap_above_in, row.col_gap_in) == (0.0, 0.0)
    assert row.height_in == 3.0


@pytest.mark.parametrize("stored", ["", None, "wide", [], {}])
def test_a_hand_edited_gap_that_is_not_a_number_falls_back(stored):
    """A bundle can be edited by hand before it is imported. A bad value
    should cost that field its value, not cost pagination a TypeError."""
    row = row_from_dict({"widgets": [], "gap_above_in": stored,
                         "col_gap_in": stored, "height_in": stored})
    assert row.gap_above_in == 0.0
    assert row.col_gap_in == 0.0
    assert row.height_in == 2.5


def test_a_gap_written_as_text_is_still_read_as_a_number():
    row = row_from_dict({"widgets": [], "gap_above_in": "0.5"})
    assert row.gap_above_in == 0.5
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_row_spacing.py -q`
Expected: FAIL — `TypeError: Row.__init__() got an unexpected keyword argument 'gap_above_in'`

- [ ] **Step 3: Add the fields**

In `kdbmonitor/core/dashboard_models.py`, replace the `Row` dataclass:

```python
@dataclass
class Row:
    widgets: list[Widget] = field(default_factory=list)
    height_in: float = 2.5       # printed height; screen height derives from it
    # Air above this row, and between its widgets. Both in inches, like every
    # other measurement here, and both *additional* to the spacing that already
    # exists — 0.0 is what this app has always done, which is what lets a
    # dashboard saved before they existed print to the same page breaks.
    gap_above_in: float = 0.0
    col_gap_in: float = 0.0
```

- [ ] **Step 4: Add the coercion and read the fields**

Add beside the existing `_int` helper in the same file:

```python
def _float(value, default: float) -> float:
    """A stored measurement, or the default if it is not one.

    Same reasoning as :func:`_int`: a bundle can be hand-edited before it is
    imported, and a bad number in it should cost that one field its value
    rather than cost pagination a TypeError halfway down a page.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
```

Then replace `row_from_dict`'s return:

```python
    return Row(
        widgets=[widget_from_dict(w) for w in _dict_list(d.get("widgets"))],
        height_in=_float(d.get("height_in"), 2.5),
        gap_above_in=_float(d.get("gap_above_in"), 0.0),
        col_gap_in=_float(d.get("col_gap_in"), 0.0),
    )
```

`height_in` is coerced too. It is the same measurement read the same way, and leaving one of the three able to arrive as a string would be an odd thing to have decided on purpose.

`dashboard_to_dict` is `asdict`, so the fields serialise with no change.

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/test_row_spacing.py -q`
Expected: PASS

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest -q`
Expected: PASS — `row_from_dict` is on the load path of every dashboard, so a regression shows up everywhere at once.

```bash
git add kdbmonitor/core/dashboard_models.py tests/test_row_spacing.py
git commit -m "feat: a row can ask for air, and asks for none by default"
```

---

## Task 2: On paper

**Files:**
- Modify: `kdbmonitor/core/dashpdf.py` (`paginate` ~line 207, `_widget_width_in` ~line 314, `_render_page` ~line 488)
- Test: `tests/test_row_spacing.py`

**Interfaces:**
- Consumes: `Row.gap_above_in`, `Row.col_gap_in` from Task 1.
- Produces: `row_gutter(row: Row) -> float` in `kdbmonitor/core/dashpdf.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_row_spacing.py`:

```python
# --- on paper ----------------------------------------------------------------

from kdbmonitor.core.dashpdf import (  # noqa: E402
    GUTTER, LANDSCAPE, MARGIN, PORTRAIT, header_height, page_count, paginate,
    plan_rows, row_gutter,
)


def _board(rows) -> Dashboard:
    return Dashboard(id=None, name="d", rows=rows)


def test_with_no_gaps_a_row_starts_exactly_where_it_always_did():
    """The claim the whole feature rests on, checked against arithmetic rather
    than against a remembered number: y is the header plus every earlier row's
    height and one gutter each."""
    rows = [_row(height_in=1.0), _row(height_in=2.0), _row(height_in=1.5)]
    pages = paginate(rows)
    placed = pages[0]

    y = MARGIN + header_height()
    for (part, y_top), row in zip(placed, rows):
        assert y_top == pytest.approx(y)
        y += row.height_in + GUTTER


def test_a_gap_pushes_everything_below_it_down_by_exactly_its_size():
    without = paginate([_row(height_in=1.0), _row(height_in=2.0)])
    with_gap = paginate([_row(height_in=1.0),
                         _row(height_in=2.0, gap_above_in=0.75)])
    assert (with_gap[0][1][1] - without[0][1][1]) == pytest.approx(0.75)


def test_a_gap_on_the_first_row_of_a_page_is_swallowed():
    """Leading space separates a heading from what precedes it. At the top of a
    page nothing does, and printing it would open a hole in the margin."""
    plain = paginate([_row(height_in=1.0)])
    lead = paginate([_row(height_in=1.0, gap_above_in=1.5)])
    assert lead[0][0][1] == plain[0][0][1]


def test_a_gap_mid_page_is_not_swallowed():
    pages = paginate([_row(height_in=1.0),
                      _row(height_in=1.0, gap_above_in=1.5)])
    assert len(pages) == 1
    assert pages[0][1][1] - pages[0][0][1] == pytest.approx(1.0 + GUTTER + 1.5)


def test_a_big_enough_gap_pushes_a_row_onto_the_next_page():
    tall = _row(height_in=4.0)
    assert len(paginate([tall, _row(height_in=4.0)])) == 1
    assert len(paginate([tall, _row(height_in=4.0, gap_above_in=3.0)])) == 2


def test_the_editor_page_break_preview_agrees_with_the_pdf():
    """plan_rows derives from paginate, so it must account for a gap without
    being told — or the preview promises a page break that does not happen."""
    rows = [_row(height_in=4.0), _row(height_in=4.0, gap_above_in=3.0)]
    assert [p.page for p in plan_rows(rows)] == [1, 2]
    assert plan_rows(rows)[1].starts_page


# --- the gutter between widgets ---------------------------------------------

def test_a_row_with_no_column_gap_keeps_the_standard_gutter():
    assert row_gutter(_row()) == GUTTER


def test_a_column_gap_adds_to_the_gutter_rather_than_replacing_it():
    assert row_gutter(_row(col_gap_in=0.5)) == pytest.approx(GUTTER + 0.5)


def test_a_column_gap_narrows_each_widget_by_its_share():
    from kdbmonitor.core.dashpdf import _widget_width_in

    widgets = [Widget(type="table", dataset="ds", title="A"),
               Widget(type="table", dataset="ds", title="B")]
    plain = Row(widgets=widgets, height_in=2.0)
    spaced = Row(widgets=widgets, height_in=2.0, col_gap_in=0.6)

    narrower = (_widget_width_in(plain, widgets[0], PORTRAIT.content_w)
                - _widget_width_in(spaced, widgets[0], PORTRAIT.content_w))
    # One gap between two widgets, shared evenly by the two of them.
    assert narrower == pytest.approx(0.3)


def test_a_single_widget_row_is_unaffected_by_a_column_gap():
    """There is nothing to separate, so the widget keeps the full width."""
    from kdbmonitor.core.dashpdf import _widget_width_in

    widget = Widget(type="table", dataset="ds", title="A")
    plain = Row(widgets=[widget], height_in=2.0)
    spaced = Row(widgets=[widget], height_in=2.0, col_gap_in=2.0)
    assert (_widget_width_in(plain, widget, PORTRAIT.content_w)
            == _widget_width_in(spaced, widget, PORTRAIT.content_w))
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_row_spacing.py -q`
Expected: FAIL — `ImportError: cannot import name 'row_gutter'`

- [ ] **Step 3: Add `row_gutter`**

In `kdbmonitor/core/dashpdf.py`, immediately after the `GUTTER` constant:

```python
def row_gutter(row: Row) -> float:
    """The space between this row's widgets.

    One function rather than three readings of ``GUTTER``, because the width a
    widget is *drawn* at, the width a table is *measured* against, and the
    width that decides whether the paper turns must never disagree — that
    disagreement is what prints a table's columns into each other.
    """
    return GUTTER + max(getattr(row, "col_gap_in", 0.0), 0.0)
```

- [ ] **Step 4: Lead each row with its gap in `paginate`**

Replace the loop body in `paginate`:

```python
    for part in split_rows(rows, results, cache):
        # The gap belongs to the row, not to each chunk of a long table: a
        # table continuing over five parts asked for air before it started,
        # not before every continuation of itself.
        lead = part.row.gap_above_in if part.part == 0 else 0.0
        if page and y + lead + part.height_in > limit:
            pages.append(page)
            page = []
            y = MARGIN + HEADER_H_CONT
            # Swallowed at the top of a page: leading space separates a row
            # from what precedes it, and here nothing does.
            lead = 0.0
        elif page and _joins(page[-1][0], part):
            page[-1] = (_join(page[-1][0], part), page[-1][1])
            y += part.height_in + GUTTER
            continue
        elif not page:
            lead = 0.0            # the first row of page 1, for the same reason
        y += lead
        page.append((part, y))
        y += part.height_in + GUTTER
```

- [ ] **Step 5: Use the row's gutter where widths are computed**

In `_widget_width_in`, replace the `usable` line:

```python
    total = sum(max(w.width, 0.01) for w in widgets)
    usable = content_w - row_gutter(row) * (len(widgets) - 1)
```

In `_render_page`, replace the matching line inside the `for part, y_top in page:` loop:

```python
        total_w = sum(max(w.width, 0.01) for w in widgets)
        gutter = row_gutter(part.row)
        usable = sheet.content_w - gutter * (len(widgets) - 1)
```

and, in the same loop, the two places that advance `x` by `w_in + GUTTER` become
`x += w_in + gutter`.

`fits_width` calls `_widget_width_in`, so it follows with no change of its own —
which is the point of the helper.

- [ ] **Step 6: Run to verify they pass**

Run: `python -m pytest tests/test_row_spacing.py -q`
Expected: PASS

- [ ] **Step 7: Prove the examples are untouched**

Append to `tests/test_row_spacing.py`:

```python
# --- nothing changes until asked --------------------------------------------

import json           # noqa: E402
import pathlib        # noqa: E402

EXAMPLES = sorted(pathlib.Path("docs/examples").glob("*.json"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_a_shipped_example_asks_for_no_extra_space(path):
    """These were built before the fields existed. If loading one produced a
    gap, every dashboard in the wild would have shifted on upgrade."""
    from kdbmonitor.core.dashboard_models import dashboard_from_dict

    board = dashboard_from_dict(json.loads(path.read_text(encoding="utf-8")))
    for row in board.rows:
        assert row.gap_above_in == 0.0
        assert row.col_gap_in == 0.0


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_a_shipped_example_paginates_by_height_alone(path):
    """With no gaps, every row still lands at the header plus the heights and
    gutters before it — the same arithmetic as before this feature."""
    from kdbmonitor.core.dashboard_models import dashboard_from_dict

    board = dashboard_from_dict(json.loads(path.read_text(encoding="utf-8")))
    if not board.rows:
        pytest.skip("no rows to place")

    pages = paginate(board.rows)
    y = MARGIN + header_height()
    for part, y_top in pages[0]:
        assert y_top == pytest.approx(y)
        y += part.height_in + GUTTER
```

If an example's rows overflow page 1, the loop only checks the rows that landed
on it, which is the part the arithmetic above describes.

- [ ] **Step 8: Run and commit**

Run: `python -m pytest tests/test_row_spacing.py tests/test_dashpdf.py tests/test_example_dashboards.py -q`
Expected: PASS

Then the full suite:

Run: `python -m pytest -q`
Expected: PASS

```bash
git add kdbmonitor/core/dashpdf.py tests/test_row_spacing.py
git commit -m "feat: the page leaves the air a row asked for, and none at a page top"
```

---

## Task 3: On screen

**Files:**
- Modify: `kdbmonitor/ui/common.py`, `kdbmonitor/ui/dashboards.py` (`render_rows` ~line 164)
- Test: `tests/test_row_spacing.py`

**Interfaces:**
- Consumes: `Row.gap_above_in`, `Row.col_gap_in`; `dashboards.row_height_px(height_in) -> int`.
- Produces: `column_spec(row: Row) -> tuple[list[float], list[int]]` in `kdbmonitor/ui/common.py` — the weights to hand `st.columns`, and the column index each widget occupies.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_row_spacing.py`:

```python
# --- on screen ---------------------------------------------------------------

from kdbmonitor.ui.common import column_spec           # noqa: E402


def _w(width: float) -> Widget:
    return Widget(type="table", dataset="ds", title="T", width=width)


def test_without_a_column_gap_the_weights_are_the_widths():
    row = Row(widgets=[_w(2.0), _w(3.0)], height_in=2.0)
    weights, slots = column_spec(row)
    assert weights == [2.0, 3.0]
    assert slots == [0, 1]


def test_a_column_gap_puts_a_spacer_between_the_widgets():
    row = Row(widgets=[_w(2.0), _w(3.0)], height_in=2.0, col_gap_in=0.5)
    weights, slots = column_spec(row)
    assert len(weights) == 3
    assert slots == [0, 2]                    # widgets sit either side of it


def test_the_spacer_is_the_right_share_of_the_row():
    """The weight is the gap's share of the row's printed width, so the screen
    and the page open the same amount of space."""
    from kdbmonitor.core.dashpdf import PORTRAIT

    row = Row(widgets=[_w(1.0), _w(1.0)], height_in=2.0, col_gap_in=0.5)
    weights, _ = column_spec(row)
    share = weights[1] / sum(weights)
    assert share == pytest.approx(0.5 / PORTRAIT.content_w, rel=0.01)


def test_two_gaps_for_three_widgets_and_none_on_the_ends():
    row = Row(widgets=[_w(1.0), _w(1.0), _w(1.0)], height_in=2.0,
              col_gap_in=0.4)
    weights, slots = column_spec(row)
    assert len(weights) == 5
    assert slots == [0, 2, 4]


def test_a_single_widget_row_gets_no_spacer():
    """There is nothing to separate, and a trailing empty column would shrink
    the only widget for nothing."""
    row = Row(widgets=[_w(1.0)], height_in=2.0, col_gap_in=2.0)
    weights, slots = column_spec(row)
    assert weights == [1.0]
    assert slots == [0]


def test_a_row_with_no_widgets_has_nothing_to_lay_out():
    weights, slots = column_spec(Row(widgets=[], height_in=2.0))
    assert weights == [] and slots == []


def test_a_zero_width_widget_still_gets_a_column():
    """st.columns rejects a weight of 0, and the page already floors widths."""
    weights, _ = column_spec(Row(widgets=[_w(0.0), _w(1.0)], height_in=2.0))
    assert all(w > 0 for w in weights)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_row_spacing.py -q`
Expected: FAIL — `ImportError: cannot import name 'column_spec'`

- [ ] **Step 3: Implement `column_spec`**

Append to `kdbmonitor/ui/common.py`:

```python
def column_spec(row) -> tuple[list[float], list[int]]:
    """``st.columns`` weights for a row, and where each widget sits in them.

    ``st.columns`` takes a gap of "small", "medium" or "large" — a different
    unit from the inches this document is measured in, and a coarser one. It
    does not need to be used: the weights are *relative*, so an inch of gap can
    be expressed as its share of the row's printed width and drawn as an empty
    spacer column. One stored number then drives the screen and the page, and
    they cannot drift apart.

    Returns ``(weights, slots)`` — hand ``weights`` to ``st.columns`` and read
    widget *i* out of ``columns[slots[i]]``.
    """
    from kdbmonitor.core.dashpdf import PORTRAIT

    widgets = row.widgets or []
    widths = [max(w.width, 0.01) for w in widgets]
    gap = max(getattr(row, "col_gap_in", 0.0), 0.0)
    if len(widgets) < 2 or not gap:
        # Nothing to separate: no spacer, and the row is built exactly as it
        # was before this existed.
        return widths, list(range(len(widgets)))

    # The gap as a weight: its share of the width this row occupies on the
    # page, scaled into the same units the widths are already in.
    weight = sum(widths) * (gap / max(PORTRAIT.content_w - gap * (len(widgets) - 1),
                                      0.01))

    weights: list[float] = []
    slots: list[int] = []
    for i, width in enumerate(widths):
        if i:
            weights.append(weight)
        slots.append(len(weights))
        weights.append(width)
    return weights, slots
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_row_spacing.py -q`
Expected: PASS

- [ ] **Step 5: Draw both gaps in the live view**

In `kdbmonitor/ui/dashboards.py`, add to the imports:

```python
from kdbmonitor.ui.common import column_spec
```

(if `ui/common` is already imported there, extend that import rather than adding a second one)

Replace the body of `render_rows`'s loop:

```python
    for r_i, row in enumerate(dashboard.rows):
        if not row.widgets:
            continue
        # Empty, fixed-height, and measured through the same conversion the
        # row's own height uses — so the air on screen is the air on the page.
        if row.gap_above_in:
            st.container(height=row_height_px(row.gap_above_in), border=False)

        weights, slots = column_spec(row)
        cols = st.columns(weights, vertical_alignment="top")
        for c_i, widget in enumerate(row.widgets):
            with cols[slots[c_i]]:
                waiting = getattr(results.get(widget.dataset), "waiting", False)
                render_widget(build_plot_model(widget, results),
                              row_height_px(row.height_in),
                              key=f"{key_prefix}_{r_i}_{c_i}",
                              waiting=waiting)
```

The widget's key still uses `c_i`, the widget's own index — not its column slot
— so adding a gap to a row does not rename its widgets' keys and reset their
table filters.

- [ ] **Step 6: Render it through AppTest**

Append to `tests/test_row_spacing.py`:

```python
# --- the page draws ----------------------------------------------------------

from streamlit.testing.v1 import AppTest          # noqa: E402

from kdbmonitor.core.dashboard_models import Dataset   # noqa: E402
from kdbmonitor.core.mock import demo_connection_specs  # noqa: E402
from kdbmonitor.core.storage import Storage             # noqa: E402

VIEW_SCRIPT = '''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboards

store = Storage(r"{db}")
store.init_db()
st.query_params["dash"] = "1"
dashboards.render(store, ConnectionManager())
'''


def _spaced_dashboard() -> Dashboard:
    """A heading, a table, air, a second heading, a second table — the layout
    the whole feature exists for."""
    def text(name):
        return Widget(type="text", dataset="ds", title="",
                      spec={"markdown": f"### {name}"})

    table = Widget(type="table", dataset="ds", title="Rows", width=1.0)
    return Dashboard(
        id=None, name="Sections", description="", refresh_secs=0,
        datasets=[Dataset(name="ds", env="orders", table="target")],
        rows=[Row(widgets=[text("Section A")], height_in=0.5),
              Row(widgets=[table], height_in=2.0),
              Row(widgets=[text("Section B")], height_in=0.5,
                  gap_above_in=0.8),
              Row(widgets=[table, table], height_in=2.0, col_gap_in=0.5)])


def _render(tmp_path, board: Dashboard) -> AppTest:
    db = str(tmp_path / "k.db")
    store = Storage(db)
    store.init_db()
    for spec in demo_connection_specs():
        store.add_connection(spec)
    store.add_dashboard(board)
    return AppTest.from_string(VIEW_SCRIPT.format(db=db),
                               default_timeout=60).run()


def test_a_dashboard_with_gaps_renders(tmp_path):
    assert not _render(tmp_path, _spaced_dashboard()).exception


def test_a_dashboard_without_gaps_still_renders(tmp_path):
    board = _spaced_dashboard()
    for row in board.rows:
        row.gap_above_in = 0.0
        row.col_gap_in = 0.0
    assert not _render(tmp_path, board).exception


def test_a_column_gap_on_a_single_widget_row_is_harmless(tmp_path):
    """st.columns would reject a spec of one weight and a trailing spacer."""
    board = _spaced_dashboard()
    board.rows[1].col_gap_in = 1.0        # a row holding one table
    assert not _render(tmp_path, board).exception
```

- [ ] **Step 7: Run and commit**

Run: `python -m pytest tests/test_row_spacing.py tests/test_ui_dashboards.py tests/test_all_pages_render.py -q`
Expected: PASS

Run: `python -m pytest -q`
Expected: PASS

```bash
git add kdbmonitor/ui/common.py kdbmonitor/ui/dashboards.py tests/test_row_spacing.py
git commit -m "feat: the screen opens the same air the page does, from one number"
```

---

## Task 4: The editor, and looking at it

**Files:**
- Modify: `kdbmonitor/ui/dashboard_editor.py` (row header ~line 2539, preview ~line 2720)
- Test: `tests/test_row_spacing.py`

**Interfaces:**
- Consumes: `column_spec` from Task 3; `Row.gap_above_in`, `Row.col_gap_in` from Task 1.

- [ ] **Step 1: Add the two controls**

In `kdbmonitor/ui/dashboard_editor.py`, widen the row header's column spec and
add the inputs after `Height (in)`:

```python
            head = st.columns([1.9, 1.1, 1.1, 1.1, 1.0, 0.6, 0.6, 0.6, 0.6],
                              vertical_alignment="bottom")
```

Keep `head[0]` (the label) and `head[1]` (`Height (in)`) as they are, then
insert:

```python
            row.gap_above_in = float(head[2].number_input(
                "Gap above", 0.0, 4.0, float(row.gap_above_in), step=0.1,
                key=f"r{r_i}_gap",
                help="Extra air before this row, in inches, on screen and on "
                     "the page. 0 is the standard spacing. A gap is not "
                     "printed when the row starts a page."))
            # Disabled rather than hidden: a control that silently does nothing
            # teaches the reader that the feature does not work.
            row.col_gap_in = float(head[3].number_input(
                "Cols gap", 0.0, 4.0, float(row.col_gap_in), step=0.1,
                key=f"r{r_i}_cgap", disabled=len(row.widgets) < 2,
                help="Extra air between this row's widgets, in inches. Needs "
                     "two or more widgets to separate."
                     if len(row.widgets) > 1 else
                     "This row holds one widget, so there is nothing to "
                     "separate."))
```

Renumber the remaining `head[…]` indices in that block by two: `Move to`
becomes `head[4]`, and the four buttons become `head[5]` … `head[8]`.

- [ ] **Step 2: Make the editor preview use the same layout**

In the same file, in the preview loop, replace the columns construction so the
preview shows the spacing it is previewing:

```python
    for r_i, row in enumerate(draft.rows):
        if not row.widgets:
            continue
        if row.gap_above_in:
            st.container(height=row_height_px(row.gap_above_in), border=False)
        weights, slots = column_spec(row)
        cols = st.columns(weights, vertical_alignment="top")
        for c_i, w in enumerate(row.widgets):
            with cols[slots[c_i]]:
                render_widget(build_plot_model(w, results),
                              row_height_px(row.height_in),
                              key=f"prev_{r_i}_{c_i}")
```

and add `column_spec` to the file's imports from `kdbmonitor.ui.common`.

- [ ] **Step 3: Test that the editor renders and the controls are addressable**

Append to `tests/test_row_spacing.py`:

```python
# --- the editor --------------------------------------------------------------

EDITOR_SCRIPT = '''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboards

store = Storage(r"{db}")
store.init_db()
st.session_state["dash_edit_id"] = 1
st.session_state["dash_mode"] = "edit"
dashboards.render(store, ConnectionManager())
'''


def _editor(tmp_path, board: Dashboard) -> AppTest:
    db = str(tmp_path / "k.db")
    store = Storage(db)
    store.init_db()
    for spec in demo_connection_specs():
        store.add_connection(spec)
    store.add_dashboard(board)
    return AppTest.from_string(EDITOR_SCRIPT.format(db=db),
                               default_timeout=60).run()


def test_the_editor_offers_a_gap_control_for_every_row(tmp_path):
    at = _editor(tmp_path, _spaced_dashboard())
    assert not at.exception
    keys = {e.key for e in at.number_input}
    for r_i in range(4):
        assert f"r{r_i}_gap" in keys
        assert f"r{r_i}_cgap" in keys


def test_the_column_gap_is_disabled_where_there_is_nothing_to_separate(tmp_path):
    at = _editor(tmp_path, _spaced_dashboard())
    by_key = {e.key: e for e in at.number_input}
    assert by_key["r1_cgap"].disabled          # one table
    assert not by_key["r3_cgap"].disabled      # two tables


def test_the_editor_shows_the_gap_that_is_stored(tmp_path):
    at = _editor(tmp_path, _spaced_dashboard())
    by_key = {e.key: e for e in at.number_input}
    assert by_key["r2_gap"].value == pytest.approx(0.8)
```

- [ ] **Step 4: Run and commit**

Run: `python -m pytest tests/test_row_spacing.py tests/test_editor_render.py -q`
Expected: PASS

Run: `python -m pytest -q`
Expected: PASS

```bash
git add kdbmonitor/ui/dashboard_editor.py tests/test_row_spacing.py
git commit -m "feat: the row that wants air can say how much"
```

- [ ] **Step 5: Look at it in the running application**

**REQUIRED SUB-SKILL:** Use the `run` skill to start the app.

Every test above can pass while the page still looks wrong: a container that
collapses to nothing, a spacer column Streamlit gives its own padding, a gap
that reads as double what was asked for. This feature is a claim about how
something looks, so it is not finished until it has been looked at.

Build this on a demo dashboard and check it by eye:

```
Section A          (text row, height 0.5, gap 0.0)
[ table ]          (height 2.0)
Section B          (text row, height 0.5, gap 0.8)   ← the air goes here
[ table ][ table ] (height 2.0, cols gap 0.5)        ← and here
```

Confirm, in this order:

1. **The vertical gap is visible** before "Section B" and nowhere else — in
   particular, "Section A" is still tight against its own table.
2. **The two tables in the last row are separated**, and both are narrower than
   the single table above them.
3. **Set the gap to 0 and the page returns to exactly its old spacing** — this
   is the claim in §3 of the spec, and the eye is a better judge of it than the
   arithmetic test.
4. **Export the PDF** from the same dashboard and compare: the gaps must read
   the same on paper as on screen. This is the one check no test performs,
   because it is the one thing tests cannot see.
5. **Add enough gap to push a row onto page 2**, then confirm the editor's page
   break marker moves to match, and that page 2 does *not* start with a blank
   band where the swallowed gap would have been.

Screenshot the screen and the PDF page side by side and report both.

- [ ] **Step 6: Commit anything the eye caught**

If steps 1–5 turned up a discrepancy, fix it, add the test that would have
caught it, and commit. If they did not, say so explicitly rather than silently
skipping this step.

---

## Self-review notes

Checked against the spec:

- §3 the model, defaults, inches, no migration → Task 1
- §4 vertical gap, swallowed at page top, first-part-only, gutter, landscape
  consequence → Task 2
- §5 screen vertical container, spacer columns, `column_spec` shared → Task 3
- §6 editor controls, disabled with a reason, preview agrees → Task 4
- §7 testing, including the running-app check → Tasks 1–4, Step 5 of Task 4

**One deviation from the spec, deliberate:** §3 says the two new fields are
coerced on load; Task 1 coerces `height_in` as well. It is the same measurement
read the same way, and leaving one of three able to arrive as a string would be
a strange thing to have chosen on purpose.

**One thing the spec left implicit, now explicit:** widget keys in `render_rows`
stay keyed by the widget's own index rather than its column slot, so adding a
column gap does not rename a table's key and drop the filters a reader had set
on it.
