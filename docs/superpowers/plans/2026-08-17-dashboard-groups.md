# Dashboard groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a dashboard the same `group` label an alert already has, and give the Dashboards gallery the same buckets the Monitor already draws — without breaking any dashboard saved or exported before the field existed.

**Architecture:** One optional string field on `Dashboard`, read back with the module's existing tolerance guard so a document written before today loads as Ungrouped. A dashboard is stored as one JSON blob, so there is no SQLite migration. Two `Storage` methods mirror the alert ones. The gallery buckets through `group_alerts` in `ui/common.py`, reused unchanged, and renders flat when nothing is grouped.

**Tech Stack:** Python 3.11+, Streamlit, SQLite (`sqlite3`), pytest, `streamlit.testing.v1.AppTest`.

**Spec:** `docs/superpowers/specs/2026-08-17-dashboard-groups-design.md`

## Global Constraints

- **Blank means Ungrouped.** `""` is the unset value everywhere. The display name is the `UNGROUPED` constant in `kdbmonitor/ui/common.py` (`"Ungrouped"`), which sorts last.
- **Reuse `ui/common.py` unchanged.** `group_label`, `sort_group_names` and `group_alerts` are already duck-typed on a `.group` attribute. Do **not** rename them, do not copy them, do not add dashboard-specific twins.
- **No SQLite migration.** The field lives inside `dashboards.dashboard_json`. Do not add a column, and do not touch `init_db`.
- **The export envelope stays `version: 2`.** `EXPORT_VERSION` in `kdbmonitor/core/portability.py` is not to be changed.
- **Grouping appears in exactly two places:** the Dashboards gallery and the dashboard editor. Do not touch the tab strip, the tab menu, or the Reports page's dashboard picker.
- **Reading a stored dashboard must never raise.** `dashboard_from_dict` takes every field off the dict with `.get` and coerces junk through `_int` / `_float` / `_text_or`. The new field follows that rule.
- Tests run with `python -m pytest` from the repo root (`pyproject.toml` sets `testpaths = ["tests"]` and `addopts = "-q"`).
- Commit messages follow this repo's voice: lowercase, a short sentence about what the code now does, prefixed `feat:` / `fix:` / `test:` / `docs:`.

---

### Task 1: The field on a dashboard, and every old document still loading

**Files:**
- Modify: `kdbmonitor/core/dashboard_models.py:205` (the `Dashboard` dataclass), `kdbmonitor/core/dashboard_models.py:427-441` (`dashboard_from_dict`)
- Test: `tests/test_dashboard_models.py`, `tests/test_portability.py`, `tests/test_example_dashboards.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Dashboard.group: str` (default `""`), read back by `dashboard_from_dict` and written out by `dashboard_to_dict` / `dashboard_to_json`. Every later task depends on this attribute existing on every `Dashboard`.

- [ ] **Step 1: Write the failing model tests**

Append to `tests/test_dashboard_models.py`:

```python
# --- groups ------------------------------------------------------------------

def test_a_dashboard_carries_its_group_through_a_json_roundtrip():
    d = _sample()
    d.group = "Equities"
    assert dashboard_from_json(dashboard_to_json(d)).group == "Equities"


def test_a_dashboard_stored_before_groups_existed_is_ungrouped():
    """The one rule the whole feature rests on: an old document has no 'group'
    key, and comes back as what it was — Ungrouped."""
    assert dashboard_from_json('{"id": null, "name": "Old"}').group == ""


def test_a_group_that_is_not_text_costs_that_field_and_nothing_else():
    for junk in ("null", "42", "[]", '{"a": 1}', '""'):
        d = dashboard_from_json('{"id": null, "name": "Odd", "group": %s}' % junk)
        assert d.group == "", junk
        assert d.name == "Odd", junk
```

- [ ] **Step 2: Write the failing portability tests**

Append to `tests/test_portability.py` (the file already imports `json` at the top and defines `_dash()` at line 172):

```python
def test_a_dashboards_group_survives_an_export_import_roundtrip():
    from kdbmonitor.core.portability import (export_dashboards_json,
                                             import_dashboards_json)
    dash = _dash()
    dash.group = "Equities"
    back = import_dashboards_json(export_dashboards_json([dash]))
    assert back[0].group == "Equities"


def test_a_dashboard_exported_before_groups_existed_arrives_ungrouped():
    """The requirement this feature was asked for: an export file written by
    yesterday's build imports, and its dashboards are Ungrouped."""
    from kdbmonitor.core.portability import import_dashboards_json
    raw = json.dumps({"kind": "kdbmonitor-export", "version": 2,
                      "exported_at": None, "connections": [], "alerts": [],
                      "dashboards": [{"name": "old", "description": "",
                                      "refresh_secs": 15, "periods": "both",
                                      "datasets": [], "rows": []}]})
    imported = import_dashboards_json(raw)
    assert len(imported) == 1
    assert imported[0].name == "old" and imported[0].group == ""
```

- [ ] **Step 3: Write the failing shipped-examples test**

Append to `tests/test_example_dashboards.py`:

```python
def test_the_shipped_examples_predate_groups_and_import_as_ungrouped():
    """The four bundles in docs/examples were written before the field existed
    and are not to be re-exported for it."""
    for path in sorted(EXAMPLES.glob("*.json")):
        assert _load(path).group == "", path.name
```

- [ ] **Step 4: Run the three test files to verify they fail**

Run: `python -m pytest tests/test_dashboard_models.py tests/test_portability.py tests/test_example_dashboards.py -q`
Expected: FAIL — `AttributeError: 'Dashboard' object has no attribute 'group'` (and `TypeError: ... unexpected keyword argument 'group'` from the round-trip test).

- [ ] **Step 5: Add the field**

In `kdbmonitor/core/dashboard_models.py`, inside the `Dashboard` dataclass, directly after `description: str = ""` (line 205):

```python
    description: str = ""
    # Which bucket it sits in on the gallery. A label, not a thing: a group
    # exists while some dashboard wears its name and is gone when the last one
    # leaves it, exactly as an alert's group does. Blank is Ungrouped, which is
    # what every dashboard saved or exported before this existed comes back as.
    group: str = ""
```

- [ ] **Step 6: Read it back**

In `dashboard_from_dict` (line 427), after the `source=` line:

```python
        source=d.get("source", "kdb"),
        group=_text_or(d.get("group"), ""),
```

`_text_or` (line 268) is the guard already protecting `delimiter`: anything that is not a non-empty string becomes the default, so a hand-edited bundle costs that one field its value rather than costing the import its error message.

- [ ] **Step 7: Check nothing builds a Dashboard positionally**

Run: `grep -rn "Dashboard(" --include=*.py kdbmonitor tests app.py | grep -v "id=" | grep -v "class Dashboard"`
Expected: no hits that pass a third positional argument. The field is inserted mid-dataclass, so a positional construction would silently receive the group. If any hit appears, convert that call site to keyword arguments in this task.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dashboard_models.py tests/test_portability.py tests/test_example_dashboards.py -q`
Expected: PASS.

- [ ] **Step 9: Run the whole suite — nothing else may change**

Run: `python -m pytest -q`
Expected: PASS. A new field with a default must not disturb any existing round-trip or PDF test.

- [ ] **Step 10: Commit**

```bash
git add kdbmonitor/core/dashboard_models.py tests/test_dashboard_models.py tests/test_portability.py tests/test_example_dashboards.py
git commit -m "feat: a dashboard can wear a group label, and one written before this wears none"
```

---

### Task 2: Moving and renaming groups in the store

**Files:**
- Modify: `kdbmonitor/core/storage.py:288` (after `delete_dashboard`)
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `Dashboard.group` from Task 1; `Storage.get_dashboard`, `Storage.list_dashboards`, `Storage.update_dashboard`.
- Produces: `Storage.set_dashboard_group(dashboard_id: int, group: str) -> None` and `Storage.rename_dashboard_group(old: str, new: str) -> int` (returns how many dashboards changed). Tasks 5 and 6 call them.

- [ ] **Step 1: Write the failing storage tests**

Append to `tests/test_storage.py` (mirroring `test_set_alert_group` / `test_rename_group` above them):

```python
# --- dashboard groups --------------------------------------------------------

def _named_dashboard(name: str, group: str = ""):
    from kdbmonitor.core.dashboard_models import Dashboard
    return Dashboard(id=None, name=name, group=group)


def test_set_dashboard_group():
    store = Storage(":memory:")
    store.init_db()
    did = store.add_dashboard(_named_dashboard("Order flow"))
    assert store.get_dashboard(did).group == ""
    store.set_dashboard_group(did, "Equities")
    assert store.get_dashboard(did).group == "Equities"
    store.set_dashboard_group(did, "  Fx  ")          # trimmed
    assert store.get_dashboard(did).group == "Fx"
    store.set_dashboard_group(did, "")                # back to Ungrouped
    assert store.get_dashboard(did).group == ""
    store.set_dashboard_group(9999, "x")              # unknown id: no-op, no crash


def test_rename_dashboard_group():
    store = Storage(":memory:")
    store.init_db()
    store.add_dashboard(_named_dashboard("d1", group="Equities"))
    store.add_dashboard(_named_dashboard("d2", group="Equities"))
    store.add_dashboard(_named_dashboard("d3", group="Orders"))

    assert store.rename_dashboard_group("Equities", "US Equities") == 2
    groups = {d.name: d.group for d in store.list_dashboards()}
    assert groups == {"d1": "US Equities", "d2": "US Equities", "d3": "Orders"}

    assert store.rename_dashboard_group("Orders", "") == 1       # dissolve
    assert {d.group for d in store.list_dashboards()} == {"US Equities", ""}

    assert store.rename_dashboard_group("nope", "x") == 0        # no members
    assert store.rename_dashboard_group("A", "A") == 0           # same name


def test_rename_dashboard_group_merges():
    store = Storage(":memory:")
    store.init_db()
    store.add_dashboard(_named_dashboard("d1", group="Fx"))
    store.add_dashboard(_named_dashboard("d2", group="Rates"))
    store.rename_dashboard_group("Fx", "Rates")
    assert {d.group for d in store.list_dashboards()} == {"Rates"}


def test_a_dashboards_group_survives_being_stored():
    store = Storage(":memory:")
    store.init_db()
    did = store.add_dashboard(_named_dashboard("d1", group="Equities"))
    assert store.list_dashboards()[0].group == "Equities"
    assert store.get_dashboard(did).group == "Equities"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_storage.py -q -k dashboard_group`
Expected: FAIL — `AttributeError: 'Storage' object has no attribute 'set_dashboard_group'`.

- [ ] **Step 3: Add the two methods**

In `kdbmonitor/core/storage.py`, immediately after `delete_dashboard` (line 288) and before the `# --- reusable components` comment:

```python
    # --- dashboard groups (a group is just the `group` label on each dashboard) ---
    def set_dashboard_group(self, dashboard_id: int, group: str) -> None:
        """Move a single dashboard into ``group`` ('' = Ungrouped)."""
        d = self.get_dashboard(dashboard_id)
        if d is None:
            return
        d.group = (group or "").strip()
        self.update_dashboard(d)

    def rename_dashboard_group(self, old: str, new: str) -> int:
        """Re-label every dashboard whose group is ``old`` to ``new`` (blank =
        dissolve into Ungrouped). Returns how many dashboards were changed.
        Renaming onto an existing name merges the two groups.

        The alert twin is ``rename_group``; this one says which it renames
        because that name is taken.
        """
        old = (old or "").strip()
        new = (new or "").strip()
        if old == new:
            return 0
        changed = 0
        for d in self.list_dashboards():
            if (d.group or "").strip() == old:
                d.group = new
                self.update_dashboard(d)
                changed += 1
        return changed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_storage.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/storage.py tests/test_storage.py
git commit -m "feat: a dashboard can be moved between groups, and a group renamed across all of them"
```

---

### Task 3: The gallery, bucketed

**Files:**
- Modify: `kdbmonitor/ui/dashboards.py:33` (the `ui.common` import), `kdbmonitor/ui/dashboards.py:510-611` (the gallery section)
- Create: `tests/test_dashboard_groups.py`

**Interfaces:**
- Consumes: `Dashboard.group` (Task 1); `group_alerts`, `UNGROUPED` from `kdbmonitor/ui/common.py`, unchanged.
- Produces, all module-level in `kdbmonitor/ui/dashboards.py`:
  - `group_buckets(saved) -> list[tuple[str, list]]`
  - `shows_groups(buckets) -> bool`
  - `kept_groups(held, names: list[str]) -> list[str]`
  - `_gallery_card(store, d, open_ids: list[int]) -> None` — Task 5 modifies it.

- [ ] **Step 1: Write the failing helper tests**

Create `tests/test_dashboard_groups.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_dashboard_groups.py -q`
Expected: FAIL — `AttributeError: module 'kdbmonitor.ui.dashboards' has no attribute 'shows_groups'`.

- [ ] **Step 3: Import the shared helpers**

In `kdbmonitor/ui/dashboards.py`, replace line 33:

```python
from kdbmonitor.ui.common import column_spec, pluralize
```

with:

```python
from kdbmonitor.ui.common import UNGROUPED, column_spec, group_alerts, pluralize
```

- [ ] **Step 4: Add the three helpers**

In `kdbmonitor/ui/dashboards.py`, directly under the `# --- gallery ---` banner (line 510), above `_selection_key`:

```python
def group_buckets(saved) -> list[tuple[str, list]]:
    """The gallery's dashboards bucketed by group, Ungrouped last.

    ``group_alerts`` is duck-typed on a ``.group`` attribute and a dashboard
    now has one, so the Monitor and the gallery bucket through the same code.
    """
    return group_alerts(saved)


def shows_groups(buckets) -> bool:
    """Whether the gallery draws group headers and a filter at all.

    An empty gallery, or one nobody has grouped, is drawn flat — so somebody
    who never uses groups never meets them.
    """
    names = {g for g, _ in buckets}
    return bool(names) and names != {UNGROUPED}


def kept_groups(held, names: list[str]) -> list[str]:
    """The filter's effective selection.

    What it holds, minus groups that have since been renamed or dissolved;
    everything when it holds nothing, because an empty filter means "show all"
    rather than "show none".
    """
    if held is None:
        return list(names)
    kept = [g for g in held if g in names]
    return kept or list(names)
```

- [ ] **Step 5: Run the helper tests to verify they pass**

Run: `python -m pytest tests/test_dashboard_groups.py -q`
Expected: PASS.

- [ ] **Step 6: Extract the card**

In `kdbmonitor/ui/dashboards.py`, add this function immediately after `_clear_selection` (line 524) — its body is the current `for d in saved:` block from lines 562-610, moved verbatim:

```python
def _gallery_card(store, d, open_ids: list[int]) -> None:
    """One dashboard's row in the gallery."""
    with st.container(border=True):
        c = st.columns([0.35, 2.5, 2.2, 1, 1, 1.2, 1.1, 0.8],
                       vertical_alignment="center")
        c[0].checkbox("Select", key=_selection_key(d.id),
                      label_visibility="collapsed",
                      help=f"Tick to open '{d.name}' with the others")
        c = c[1:]
        open_flag = (" :green-badge[:material/tab: Open]"
                     if d.id in open_ids else "")
        c[0].markdown(f"**{d.name}**{open_flag}"
                      + (f"<br>:gray[{d.description}]" if d.description else ""),
                      unsafe_allow_html=True)
        envs = sorted({ds.env for ds in d.datasets if ds.env})
        n_widgets = sum(len(r.widgets) for r in d.rows)
        c[1].markdown(f":gray[{len(d.datasets)} dataset(s) · {n_widgets} widget(s)]"
                      + (f"<br>:gray[env: {', '.join(envs)}]" if envs else ""),
                      unsafe_allow_html=True)
        if c[2].button("Open", key=f"open_{d.id}", icon=":material/open_in_new:",
                       use_container_width=True):
            _open(d.id, store)
        if c[3].button("Edit", key=f"edit_{d.id}", icon=":material/edit:",
                       use_container_width=True):
            st.session_state["dash_edit_id"] = d.id
            st.session_state["dash_mode"] = "edit"
            st.session_state.pop("dash_draft", None)
            st.rerun()
        if c[4].button("Duplicate", key=f"dup_{d.id}",
                       icon=":material/content_copy:", use_container_width=True):
            copy = store.get_dashboard(d.id)
            copy.id = None
            copy.name = f"{copy.name} (copy)"
            store.add_dashboard(copy)
            st.toast(f"Duplicated '{d.name}'", icon=":material/check:")
            st.rerun()
        c[5].download_button(
            "Export", key=f"exp_{d.id}",
            data=export_dashboards_json([d]),
            file_name=dashboard_filename(d), mime="application/json",
            icon=":material/download:", use_container_width=True,
            help="Download this dashboard as JSON")
        with c[6].popover("", icon=":material/delete:"):
            st.warning(f"Delete '{d.name}'?")
            if st.button("Confirm", key=f"delok_{d.id}", type="primary"):
                store.delete_dashboard(d.id)
                # Its tab goes with it — parse_tabs drops an id that no longer
                # exists, and this drops what it was holding.
                _forget(d.id)
                st.rerun()
```

- [ ] **Step 7: Bucket the gallery**

In `_render_gallery`, replace lines 528-529:

```python
    saved = store.list_dashboards()
    open_ids = _open_ids(store)
```

with:

```python
    saved = store.list_dashboards()
    open_ids = _open_ids(store)

    # Worked out before the head row is drawn, because "Open selected (n)"
    # counts what is on the screen: a group filtered out is not drawn, so its
    # ticks are not counted either.
    buckets = group_buckets(saved)
    grouped = shows_groups(buckets)
    names = [g for g, _ in buckets]
    held = st.session_state.get("gal_group_filter")
    visible_names = kept_groups(held, names)
    if held is not None and any(g not in names for g in held):
        st.session_state["gal_group_filter"] = [g for g in held if g in names]
    visible = ([d for g, items in buckets if g in visible_names for d in items]
               if grouped else saved)
```

Then replace line 538:

```python
    picked = selected_ids(saved)
```

with:

```python
    picked = selected_ids(visible)
```

and line 544:

```python
        _clear_selection(saved)
```

stays as it is — clearing every tick, visible or not, is right when they have just been opened.

Finally replace the whole `for d in saved:` block (lines 562-610, now living in `_gallery_card`) with:

```python
    if grouped:
        chosen = st.multiselect(
            "Filter groups", names, default=names, key="gal_group_filter",
            help="Show only the chosen groups. Collapse a group with its ▸ "
                 "header; filter one out and its ticks go with it.")
        visible_names = kept_groups(chosen, names)
        for gname, items in buckets:
            if gname not in visible_names:
                continue
            with st.expander(f"**{gname}** · {len(items)}", expanded=True,
                             icon=":material/folder:"):
                for d in items:
                    _gallery_card(store, d, open_ids)
    else:
        for d in saved:
            _gallery_card(store, d, open_ids)
```

- [ ] **Step 8: Write the failing page tests**

Append to `tests/test_dashboard_groups.py`:

```python
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
```

- [ ] **Step 9: Run the page tests**

Run: `python -m pytest tests/test_dashboard_groups.py -q`
Expected: PASS.

- [ ] **Step 10: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS — `tests/test_dashboard_tabs.py` exercises this same gallery and must be unaffected, because an ungrouped gallery still renders flat.

- [ ] **Step 11: Commit**

```bash
git add kdbmonitor/ui/dashboards.py tests/test_dashboard_groups.py
git commit -m "feat: the gallery buckets its dashboards, and stays flat when none is grouped"
```

---

### Task 4: The Group picker in the editor

**Files:**
- Modify: `kdbmonitor/ui/dashboards.py` (add `NO_GROUP`, `NEW_GROUP`, `existing_groups`, `group_options` beside the Task 3 helpers), `kdbmonitor/ui/dashboard_editor.py:48` (import), `kdbmonitor/ui/dashboard_editor.py:2770-2776` (the head row)
- Test: `tests/test_dashboard_groups.py`

**Interfaces:**
- Consumes: `Storage.list_dashboards`; `Dashboard.group`.
- Produces:
  - `dashboards.NO_GROUP = "(no group)"`, `dashboards.NEW_GROUP = "➕ New group…"`
  - `dashboards.existing_groups(store) -> list[str]`
  - `dashboards.group_options(existing: list[str], current: str) -> list[str]`
  - `dashboard_editor._group_picker(container, store, draft) -> None`
  Task 5 reuses the first three.

- [ ] **Step 1: Write the failing option tests**

Append to `tests/test_dashboard_groups.py`, after the filter tests:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_dashboard_groups.py -q -k "picker or groups_in_use"`
Expected: FAIL — `AttributeError: module 'kdbmonitor.ui.dashboards' has no attribute 'group_options'`.

- [ ] **Step 3: Add the picker vocabulary**

In `kdbmonitor/ui/dashboards.py`, above `group_buckets` (added in Task 3):

```python
# What a group picker offers besides the groups themselves. The same two
# entries the alert builder offers, worded the same way.
NO_GROUP = "(no group)"
NEW_GROUP = "➕ New group…"


def existing_groups(store) -> list[str]:
    """Every group label some dashboard wears, alphabetically."""
    return sorted({(d.group or "").strip() for d in store.list_dashboards()
                   if (d.group or "").strip()})


def group_options(existing: list[str], current: str) -> list[str]:
    """The choices a group picker offers.

    ``current`` stays selectable even when no saved dashboard uses it yet, so a
    group typed into the editor survives the rerun that follows the typing.
    """
    current = (current or "").strip()
    named = sorted(set(existing) | ({current} if current else set()))
    return [NO_GROUP] + named + [NEW_GROUP]
```

- [ ] **Step 4: Run the option tests to verify they pass**

Run: `python -m pytest tests/test_dashboard_groups.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing editor tests**

Append to `tests/test_dashboard_groups.py`:

```python
# --- the picker in the editor ------------------------------------------------

def _editor_script(db_path: str, dash_id: int) -> str:
    """The editor, on whichever dashboard `_want` names.

    `_draft` reloads whenever the id it holds is not the one asked for, so
    switching dashboards is one session-state write and a rerun — which is
    exactly what the Edit button does from the gallery.
    """
    return f'''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboard_editor

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
```

- [ ] **Step 6: Run them to verify they fail**

Run: `python -m pytest tests/test_dashboard_groups.py -q -k editor`
Expected: FAIL — `StopIteration` from `_group_box`: no selectbox is labelled "Group".

- [ ] **Step 7: Import the helpers into the editor**

In `kdbmonitor/ui/dashboard_editor.py`, replace line 48:

```python
from kdbmonitor.ui.dashboards import back_to_gallery, render_widget, row_height_px
```

with:

```python
from kdbmonitor.ui.dashboards import (
    NEW_GROUP, NO_GROUP, back_to_gallery, existing_groups, group_options,
    render_widget, row_height_px,
)
```

- [ ] **Step 8: Add the picker**

In `kdbmonitor/ui/dashboard_editor.py`, immediately above `def render(store, mgr)` (line 2767):

```python
def _group_picker(container, store, draft: Dashboard) -> None:
    """The Group control: a group already in use, none, or a new one.

    Keyless on purpose, like the name and description beside it: the value is
    read from the draft and written straight back. A keyed picker would carry
    one dashboard's group into the next one opened and quietly write it there —
    Streamlit hands an unkeyed selectbox back to `index` when `index` changes,
    which is precisely when the draft changed underneath it.
    """
    current = (draft.group or "").strip()
    options = group_options(existing_groups(store), current)
    choice = container.selectbox(
        "Group", options,
        index=options.index(current) if current in options else 0,
        help="Bucket related dashboards together in the gallery.")
    if choice == NEW_GROUP:
        draft.group = container.text_input(
            "New group name", value="", placeholder="e.g. Equities").strip()
    elif choice == NO_GROUP:
        draft.group = ""
    else:
        draft.group = choice
```

- [ ] **Step 9: Put it in the head row**

In `render` (line 2770), replace:

```python
        head = st.columns([2.6, 2.6, 1.8, 1.4, 1.2, 1.2],
                          vertical_alignment="bottom")
        draft.name = head[0].text_input("Dashboard name", value=draft.name)
        draft.description = head[1].text_input("Description",
                                               value=draft.description)
        sources = ["kdb", "file"]
        draft.source = head[2].selectbox(
```

with:

```python
        head = st.columns([2.4, 2.4, 1.6, 1.6, 1.4, 1.2, 1.2],
                          vertical_alignment="bottom")
        draft.name = head[0].text_input("Dashboard name", value=draft.name)
        draft.description = head[1].text_input("Description",
                                               value=draft.description)
        _group_picker(head[2], store, draft)
        sources = ["kdb", "file"]
        draft.source = head[3].selectbox(
```

Then renumber the three buttons further down the same function: `head[3].button(f"Save ...` becomes `head[4]`, `head[4].button("Open"` becomes `head[5]`, and `head[5].button("Back"` becomes `head[6]`.

Run `grep -n "head\[" kdbmonitor/ui/dashboard_editor.py` afterwards and confirm the indices read 0,1,2,3,4,5,6 exactly once each.

- [ ] **Step 10: Run the editor tests**

Run: `python -m pytest tests/test_dashboard_groups.py -q`
Expected: PASS.

- [ ] **Step 11: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS — `tests/test_editor_render.py` renders this head row and asserts every key is unique.

- [ ] **Step 12: Commit**

```bash
git add kdbmonitor/ui/dashboards.py kdbmonitor/ui/dashboard_editor.py tests/test_dashboard_groups.py
git commit -m "feat: the editor says which group a dashboard is in, and takes a new one"
```

---

### Task 5: Move, on a card

**Files:**
- Modify: `kdbmonitor/ui/dashboards.py` (`_gallery_card` from Task 3, plus a `_safe_select` helper)
- Test: `tests/test_dashboard_groups.py`

**Interfaces:**
- Consumes: `Storage.set_dashboard_group` (Task 2); `existing_groups`, `group_options`, `NO_GROUP`, `NEW_GROUP` (Task 4); `_gallery_card` (Task 3).
- Produces: `dashboards._move_to_group(store, dashboard) -> None`, drawn inside a per-card popover keyed `gmv_sel_<id>` / `gmv_new_<id>` / `gmv_go_<id>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard_groups.py`:

```python
# --- moving one dashboard ----------------------------------------------------

def test_a_card_offers_to_move_its_dashboard(grouped_db):
    at = _run(grouped_db)
    assert sum(1 for b in at.button if b.key.startswith("gmv_go_")) == 4


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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_dashboard_groups.py -q -k move`
Expected: FAIL — no button whose key starts `gmv_go_`.

- [ ] **Step 3: Add the guarded selectbox**

In `kdbmonitor/ui/dashboards.py`, directly under `group_options`:

```python
def _safe_select(container, label, options, key, **kw):
    """selectbox that won't crash when a persisted value leaves the option set.

    A group picked in a popover and then dissolved from Manage groups is exactly
    that case. Twin of the helper in ui/builder.py, which guards the alert
    pickers for the same reason.
    """
    if key in st.session_state and st.session_state[key] not in options:
        del st.session_state[key]
    return container.selectbox(label, options, key=key, **kw)
```

- [ ] **Step 4: Add the popover body**

In `kdbmonitor/ui/dashboards.py`, directly above `_gallery_card`:

```python
def _move_to_group(store, dashboard) -> None:
    """Popover body: reassign one dashboard's group without opening the editor."""
    st.caption(f"Move **{dashboard.name}** to")
    current = (dashboard.group or "").strip()
    options = group_options(existing_groups(store), current)
    sel = _safe_select(st, "Group", options, key=f"gmv_sel_{dashboard.id}",
                       index=options.index(current) if current in options else 0,
                       label_visibility="collapsed")
    if sel == NEW_GROUP:
        target = st.text_input("New group name", key=f"gmv_new_{dashboard.id}",
                               placeholder="e.g. Equities").strip()
    elif sel == NO_GROUP:
        target = ""
    else:
        target = sel
    if st.button("Move", key=f"gmv_go_{dashboard.id}", type="primary",
                 icon=":material/check:"):
        if target != current:
            store.set_dashboard_group(dashboard.id, target)
        st.rerun()
```

- [ ] **Step 5: Give the card its ninth column**

In `_gallery_card`, replace the column list:

```python
        c = st.columns([0.35, 2.5, 2.2, 1, 1, 1.2, 1.1, 0.8],
                       vertical_alignment="center")
```

with:

```python
        c = st.columns([0.35, 2.3, 2.0, 1, 1, 1.15, 1.0, 1.05, 0.8],
                       vertical_alignment="center")
```

Then insert Move after the Duplicate block and renumber what follows — Export moves from `c[5]` to `c[6]`, and the delete popover from `c[6]` to `c[7]`:

```python
        with c[5].popover("Move", icon=":material/drive_file_move:",
                          use_container_width=True,
                          help="Move this dashboard to another group"):
            _move_to_group(store, d)
        c[6].download_button(
            "Export", key=f"exp_{d.id}",
            data=export_dashboards_json([d]),
            file_name=dashboard_filename(d), mime="application/json",
            icon=":material/download:", use_container_width=True,
            help="Download this dashboard as JSON")
        with c[7].popover("", icon=":material/delete:"):
```

The card does not print its own group: it is already inside that group's expander, and when the gallery is flat there is no group to print.

- [ ] **Step 6: Run the move tests**

Run: `python -m pytest tests/test_dashboard_groups.py -q`
Expected: PASS.

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS — `tests/test_dashboard_tabs.py` clicks Open and Export on these cards.

- [ ] **Step 8: Commit**

```bash
git add kdbmonitor/ui/dashboards.py tests/test_dashboard_groups.py
git commit -m "feat: a dashboard moves between groups from its own card"
```

---

### Task 6: Manage groups

**Files:**
- Modify: `kdbmonitor/ui/dashboards.py` (`_render_gallery`, above the "Share dashboards" divider)
- Test: `tests/test_dashboard_groups.py`

**Interfaces:**
- Consumes: `Storage.rename_dashboard_group` (Task 2); `pluralize` from `ui/common.py`.
- Produces: `dashboards._manage_groups(store) -> None`, keyed `dgrp_ren_<group>` / `dgrp_renbtn_<group>` / `dgrp_dis_<group>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard_groups.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_dashboard_groups.py -q -k "manage or renaming or dissolving"`
Expected: FAIL — no text input keyed `dgrp_ren_Equities`.

- [ ] **Step 3: Add the expander**

In `kdbmonitor/ui/dashboards.py`, directly above `_render_gallery`:

```python
def _manage_groups(store) -> None:
    """Rename or dissolve whole groups — bulk work across their dashboards.

    Draws nothing when no group exists, so it only appears once somebody has
    made one. Twin of the alerts one in ui/builder.py.
    """
    counts: dict[str, int] = {}
    for d in store.list_dashboards():
        g = (d.group or "").strip()
        if g:
            counts[g] = counts.get(g, 0) + 1
    if not counts:
        return
    groups = sorted(counts)
    with st.expander(f"Manage groups ({len(groups)})",
                     icon=":material/folder_managed:"):
        st.caption("Rename a group to re-label every dashboard in it, or "
                   "dissolve it to send them back to Ungrouped. Renaming onto "
                   "an existing group merges them.")
        for g in groups:
            with st.container(border=True):
                gc = st.columns([3, 1.2, 1.1, 1.3], vertical_alignment="bottom")
                new_name = gc[0].text_input("Group name", value=g,
                                            key=f"dgrp_ren_{g}")
                gc[1].markdown(f":gray[{pluralize(counts[g], 'dashboard')}]")
                if gc[2].button("Rename", key=f"dgrp_renbtn_{g}",
                                icon=":material/edit:",
                                disabled=not new_name.strip()
                                or new_name.strip() == g):
                    n = store.rename_dashboard_group(g, new_name.strip())
                    st.toast(f"Renamed '{g}' → '{new_name.strip()}' "
                             f"({pluralize(n, 'dashboard')})",
                             icon=":material/check:")
                    st.rerun()
                with gc[3].popover("Dissolve", icon=":material/folder_off:"):
                    st.warning(f"Dissolve '{g}'? Its "
                               f"{pluralize(counts[g], 'dashboard')} will "
                               "become Ungrouped.")
                    if st.button("Confirm dissolve", key=f"dgrp_dis_{g}",
                                 type="primary"):
                        store.rename_dashboard_group(g, "")
                        st.toast(f"Dissolved '{g}'", icon=":material/check:")
                        st.rerun()
```

- [ ] **Step 4: Call it**

In `_render_gallery`, immediately before the `st.divider()` that opens the "Share dashboards" block:

```python
    _manage_groups(store)

    st.divider()
    st.markdown("**Share dashboards**")
```

- [ ] **Step 5: Run the manage tests**

Run: `python -m pytest tests/test_dashboard_groups.py -q`
Expected: PASS.

- [ ] **Step 6: Run the whole suite one last time**

Run: `python -m pytest -q`
Expected: PASS, with no skips other than the ones the suite already reports.

- [ ] **Step 7: Mark the spec built**

In `docs/superpowers/specs/2026-08-17-dashboard-groups-design.md`, change the header line:

```markdown
**Status:** design agreed, not built.
```

to:

```markdown
**Status:** built.
```

- [ ] **Step 8: Commit**

```bash
git add kdbmonitor/ui/dashboards.py tests/test_dashboard_groups.py docs/superpowers/specs/2026-08-17-dashboard-groups-design.md
git commit -m "feat: a whole group of dashboards can be renamed, or dissolved"
```

---

## Notes for the implementer

**Three Streamlit behaviours this plan relies on, all verified before it was written:**

1. Widgets inside a `st.popover` are reachable and drivable from `AppTest` — `at.selectbox(key=...)`, `at.button(key=...).click()` all work through a closed popover.
2. An **unkeyed** selectbox follows its `index` argument when that argument changes, because `index` participates in the widget's identity. This is what makes the editor's picker reset when the draft changes underneath it.
3. An unkeyed selectbox keeps the user's own choice across reruns when `index` has *not* changed — so picking "➕ New group…" and typing nothing leaves the text box on screen rather than snapping back to "(no group)".

**A collapsed expander still runs its body**, so a collapsed group keeps its ticked checkboxes. A group removed by the filter is not drawn at all, and Streamlit drops the widget state of anything it did not draw — which is why `picked` is computed from the visible dashboards in Task 3 rather than from all of them.
