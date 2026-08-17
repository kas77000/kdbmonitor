# Dashboard groups — design

**Status:** built.
**Date:** 2026-08-17

---

## 1. The problem

Alerts can be bucketed. A desk with forty alerts puts the equities ones under
"Equities", collapses the rest, and reads its own row of the Monitor without
scrolling past everybody else's. `Alert.group` is the whole mechanism: a label
carried on each alert, blank meaning Ungrouped.

Dashboards have no such thing. The gallery is one flat list ordered by name, and
it grows the way saved views always grow — a desk's four, another desk's six, and
the three somebody built to answer one question in March. There is no way to say
which belong together, and no way to look at only those.

So: give a dashboard the same label an alert already has, and give the gallery
the same buckets the Monitor already draws.

---

## 2. What this is and is not

In scope:

- A `group` label on a dashboard, blank meaning Ungrouped.
- Setting it in the editor.
- The gallery bucketed by it, with a filter, matching the Monitor.
- Moving one dashboard between groups without opening the editor.
- Renaming and dissolving whole groups.
- The label travelling in an export, and **every dashboard saved or exported
  before this existed still importing.**

Deliberately out:

- **Grouping anywhere else dashboards are listed.** The tab strip, the tab menu
  and the Reports page's dashboard picker are left exactly as they are. The
  gallery is where you go looking for a dashboard you cannot immediately see;
  the tab strip holds the handful you already opened.
- **Nested groups.** One level, like alerts. A group is a label, not a tree.
- **A group a dashboard belongs to twice.** One label, one bucket.
- **A groups table.** A group has no existence apart from the dashboards
  wearing its name — create one by typing it, and it is gone when the last
  dashboard leaves it. Same as alerts.
- **Per-group export.** Export-all and per-card export already cover it.

---

## 3. The model

One field on `Dashboard`, beside `description`:

```python
group: str = ""              # optional grouping label ("" = Ungrouped)
```

Read back in `dashboard_from_dict` through the module's existing tolerance
guard:

```python
group=_text_or(d.get("group"), ""),
```

`_text_or` is what already protects `delimiter`: a hand-edited bundle holding
`null`, `42` or a list where a label belongs costs that one field its value
rather than costing the whole import its error message. Reading a stored
dashboard must not raise.

Nothing else in the model changes. `dashboard_to_dict` is `asdict`, so the field
exports itself.

---

## 4. Why old exports still import

This is the requirement the feature is measured against, and it is met by how
`dashboard_from_dict` already reads rather than by anything added for it: every
field comes off the dict with `.get`, never by splatting. A document written
before today has no `"group"` key, gets `""`, and lands as Ungrouped — which is
what it was.

That one path covers all three places old data lives:

| Old data | What happens |
|---|---|
| An export file on someone's disk | Imports; its dashboards are Ungrouped. |
| `docs/examples/*.json` | Loads; Ungrouped. |
| Rows already in `kdbmonitor.db` | Load; Ungrouped. |

A dashboard is stored as one JSON blob in `dashboards.dashboard_json`, so there
is **no SQLite migration** — the new field rides inside a column that already
holds free-form JSON.

The export envelope stays `version: 2`. This is an additive optional field,
exactly like `static`, `base` and `gap_above_in` before it; the version is
about the shape of the envelope, not the vocabulary of what it carries. A new
export read by an older build is not a case this app has ever had to serve, and
inventing a version bump for it would only make old files look wrong.

§8 pins this down with a test that imports a real pre-feature document.

---

## 5. Storage

Two methods on `Store`, mirroring `set_alert_group` and `rename_group`:

```python
def set_dashboard_group(self, dashboard_id: int, group: str) -> None:
    """Move a single dashboard into `group` ('' = Ungrouped)."""

def rename_dashboard_group(self, old: str, new: str) -> int:
    """Re-label every dashboard whose group is `old` to `new` (blank =
    dissolve into Ungrouped). Returns how many were changed. Renaming onto
    an existing name merges the two groups."""
```

Both go through `get_dashboard` / `update_dashboard`, so the label is written
the same way every other dashboard field is. Labels are compared and stored
stripped.

The dashboard method is spelled out in full because `rename_group` is already
taken by alerts. The asymmetry is deliberate: renaming the alert method to match
would touch working alert code for a naming preference, and this feature is not
a reason to.

---

## 6. The editor

A **Group** picker joins the top of `dashboard_editor.render()`, offering the
groups already in use, plus `(no group)` and `➕ New group…`. Choosing the last
one reveals a text box; whatever is settled on is written to `draft.group` and
saved with the rest of the dashboard.

Two decisions worth recording:

**It goes on a line of its own, drawn once for both sources.** Not the period
row below: that row is written twice, once for a kdb dashboard and once for a
file one, and the comment already standing there records what putting a control
in both branches cost the last time — two copies that drifted until their help
text disagreed.

It was a seventh control in the head row first, which is what the design said,
and running the app is what corrected it: at 1500px the box was too narrow to
read its own contents, showing "US Equities" as "U…" and taking "KDB queries"
down to "K…" beside it. The head row keeps its original six columns.

**It reads from the draft, not from a session key.** The head row's name and
description fields take `value=draft.x` and carry no explicit key, and the
picker follows them. A keyed picker would survive the move from a dashboard in
"Ops" to one with no group, show "Ops" still selected, and write it into the
second dashboard — a silent regrouping nobody asked for.

A group the draft is in but no saved dashboard uses yet stays selectable, the
way the alert builder keeps an edit-loaded group in its list. Typing a new name
leaves the picker reading "➕ New group…" with the name still in its box, which
is where a name still being typed belongs.

---

## 7. The gallery

Buckets come from `group_alerts` in `ui/common.py`, reused unchanged — it is
duck-typed on a `.group` attribute and a dashboard now has one. `group_label`
and `sort_group_names` come with it, so Ungrouped sorts last here for the same
reason it does on the Monitor.

**A gallery whose dashboards are all ungrouped renders exactly as it does
today** — flat cards, no headers, no filter. Someone who never uses the feature
never sees it. This is the same test the Monitor makes
(`len(buckets) > 1 or set(buckets) != {"Ungrouped"}`).

Otherwise:

- A `Filter groups` multiselect over every group, keyed `gal_group_filter`, its
  opening selection seeded into session state rather than passed as `default=`.
  A widget given both a default and a session-state value makes Streamlit print
  a warning **on the page**; it is not an exception, so it sat there in yellow
  above the gallery with every test still green. Only running the app found it.
- One `st.expander` per group, headed `**Equities** · 3` with the folder icon,
  open by default, holding that group's cards unchanged.

**Which way the filter is read.** As *everything except what you unticked*, not
as *only what you ticked* — the groups that existed when it was last drawn are
remembered beside it (`gal_group_known`), so what was excluded is
`known - held`.

This only matters when the set of groups changes underneath the filter, and
then it matters a great deal, which running the app is what showed: renaming
Equities to US Equities invents a name nobody has had the chance to tick, and
read the other way round the reader renames a group and watches its two
dashboards leave the screen. Moving a dashboard into a brand-new group had the
same fault. A group the reader deliberately unticked stays hidden across both.

**Ticks and the filter.** A collapsed expander still runs its body, so
collapsing a group keeps its ticks and its contribution to
`Open selected (n)`. A group filtered *out* is not drawn at all, so its ticks
go with it. That is the behaviour worth having: the count on the button is
always what is on the screen, and Open selected can never open a dashboard the
filter says you are not looking at.

**Move.** Each card gains a popover between Duplicate and Export: pick an
existing group, or type a new one, and `set_dashboard_group` does the rest. The
card's eight columns become nine,
`[0.35, 2.3, 2.0, 1, 1, 1.15, 1.0, 1.05, 0.8]`.

The card does **not** print its own group the way an alert row does — it is
already sitting inside that group's expander, and a label repeating its heading
is noise.

**Manage groups.** Below the cards and above the "Share dashboards" divider, an
expander listing each group with a rename box, its dashboard count, and a
Dissolve popover — `rename_dashboard_group` behind both, the same shape as the
alerts one. It draws nothing when no group exists.

Import is untouched: the group rides in the JSON and lands as it left. Names
still collide and still get suffixed by `unique_dashboard_name`; groups do not
collide, because two dashboards sharing a label is the entire point.

---

## 8. Testing

**The model.**

- A dashboard round-trips through `dashboard_to_dict` / `dashboard_from_dict`
  with its group intact.
- A dict with no `"group"` key loads as `""`.
- `"group": null`, `"group": 42` and `"group": []` each load as `""`.

**Backward compatibility — the regression guard for the requirement.**

- A pre-feature export document (the envelope with `"dashboards"` whose entries
  carry no `"group"`) imports through `import_dashboards_json`, and every
  dashboard in it comes back Ungrouped.
- A `docs/examples/*.json` dashboard still loads.
- A dashboard JSON blob written before the field exists still reads out of the
  store.

**Storage**, mirroring `test_rename_group` and `test_rename_group_merges`:

- `set_dashboard_group` moves one dashboard and leaves the others alone.
- Rename re-labels every member and returns the count.
- Renaming onto an existing group merges them.
- Renaming to `""` dissolves.
- A group with no members returns 0; renaming to the same name returns 0.

**The UI**, via AppTest:

- The gallery renders with grouped dashboards and shows the filter.
- A gallery of only ungrouped dashboards renders flat, with no filter — the
  no-change-for-non-users guarantee, asserted rather than assumed.
- The editor renders its group picker, and saving carries the choice through to
  the stored dashboard.
