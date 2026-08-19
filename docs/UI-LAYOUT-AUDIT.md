# KdbMonitor — UI layout audit

**Date:** 2026-08-19 · **Scope:** layout only (alignment, symmetry, spacing, component
consistency, visual hierarchy, organization, navigation, responsive behaviour).
Functional bugs are explicitly out of scope.

**Method:** every page rendered in Chromium via Playwright at 1680 / 1280 / 1024 px
against a seeded database (4 dashboards, 6 alerts, 4 servers, 2 report jobs, live run
history), full-height screenshots, plus a DOM geometry dump of every horizontal block,
button, card, heading and input. Source cross-checked against
`kdbmonitor/ui/*.py` (7,481 lines).

**Baseline:** `2015 passed, 31 skipped`.

---

## The headline numbers

| Measure | Value | What it should be |
|---|---:|---|
| Distinct `st.columns([...])` ratios | **80** across 105 calls | ~6 |
| Column layouts that are one-offs | **76 %** | ~0 % |
| Distinct content right-edges on Builder | **7** (1193, 1207, 1221, 1581, 1595, 1596, 1610 px) | 1–2 |
| Distinct content right-edges on Admin | **4**, alternating section by section | 1–2 |
| Distinct button widths on Admin | **8** for 11 buttons (72–187 px) | 2–3 |
| Page title heading levels | **2** (`h1` on Schedules, `h3` everywhere else) | 1 |
| Section labels that are bold body text, not headings | **30** | 0 |
| `use_container_width` on buttons | 19/23 on Dashboards, 1/19 on Builder, 0/8 on Schedules | one rule |
| Overlapping buttons at 1024 px | **6 pairs** on Builder, 1 on Admin | 0 |
| Smallest rendered button at 1024 px | **34 px** (Dashboards row actions) | ≥ 64 px |

The root cause is singular: **there is no shared layout system.** Each page, and in
several cases each row within a page, invents its own grid. Everything below is a
symptom of that.

---

## S1 — Critical

Breaks the interface or makes a screen unusable at a supported size.

### S1-1 · Dashboards action row collapses into overlapping buttons at 1024 px
`ui/dashboards.py:632` — `st.columns([0.35, 2.3, 2.0, 1, 1, 1.15, 1.0, 1.05, 0.8])`

Nine columns, six of them action buttons (Open · Edit · Duplicate · Move▾ · Export · 🗑▾).
At 1024 px each button box is **34–44 px** wide while its label needs 60–90 px, so labels
and icons render on top of one another — "Ed📋", "Duplic⏩", "Move⬇" physically collide.
The name/description column collapses to ~40 px, turning a two-line description into a
30-line vertical ribbon and a 450 px-tall card.

Streamlit columns do not reflow, so this cannot be fixed by CSS. The row must carry
fewer things.

### S1-2 · Builder alert rows overlap at 1024 px
`ui/builder.py:656` — `st.columns([1.1, 3.0, 0.85, 1.05, 1.0, 1.05, 1.1])`

Seven columns; measured **6 overlapping button pairs** at 1024 px. Same cause as S1-1.

### S1-3 · Six co-equal actions per row, no primary
Both list rows above present six buttons of identical visual weight. Nothing indicates
which is the common action. Linear, GitHub, Notion and the Stripe dashboard all converge
on the same rule: **one primary action inline, everything else behind an overflow menu.**
This is simultaneously the fix for S1-1 and S1-2.

### S1-4 · Content width thrashes section-to-section
`ui/admin.py`, `ui/builder.py`, `ui/schedules.py`, `ui/dashboard_editor.py`

Admin alternates full-width → `form_area()` → full-width → full-width → `form_area()` ×3.
Measured right edges: 1207 px (5 rows) vs 1596 px (8 rows), stacked directly on top of
each other. Builder is worse — 7 distinct right edges. Schedules puts a `form_area()`
status card directly above a full-width job list, a 390 px difference.

In the dashboard editor the `st.divider()` under the Section tabs runs to 1606 px while
every card above and below it stops at 1218 px — a rule that overshoots its own content
by 390 px.

`common.py` documents a good reason for the narrow form column. The defect is that it is
applied to *some* sections and not others on the same page, so no two blocks share a
right edge.

---

## S2 — High

Consistently wrong across the app; each violates a stated goal in the brief.

### S2-1 · Page titles use two different heading levels
`ui/schedules.py:324` uses `st.title()` (h1, ~32 px). All six other pages use
`st.subheader()` (h3, ~18 px). Navigating Monitor → Schedules changes the title size by
14 px for no reason.

Separately, `h3` is too quiet for a page title: it is the same weight as the
`st.markdown("**Bold**")` section labels beneath it, so page and section read as peers.

### S2-2 · Thirty section labels are bold body text, not headings
Admin (9), Builder (8), Schedules (5), dashboard_editor (3), Dashboards (2), others (3).
"Registered servers", "Environments", "Email (SMTP)", "Jobs", "Parameters",
"Share dashboards" are all `st.markdown("**…**")` at 14 px — no semantic level, no
consistent space above, and indistinguishable from ordinary emphasised text.

Result: the whole app is visually **flat**. There is no page → section → subsection
ladder, which is the first thing Microsoft, Atlassian and Stripe settings surfaces
establish.

### S2-3 · The primary action lives in a different place on every page
| Page | "Create / commit" action | Location |
|---|---|---|
| Dashboards | New dashboard | top-right |
| Builder | Save alert | **bottom-left** |
| Schedules | New schedule | **bottom-left, after the list** |
| Dashboard editor | Save | top-right, but rendered **disabled/grey**, wrapped to 2 lines |
| Admin | Add · Save SMTP · Save admin notifications · Save snapshot settings | four different mid-page positions |
| Reports | Generate report | mid-page, floating with 400 px of dead space to its right |

The brief asks for "action buttons in consistent locations". Today there is no rule at all.

### S2-4 · Three different treatments of the same import/export block
- Builder — inside an expander, `st.columns(2)`
- Admin — inside an expander, `st.columns(2)`
- Dashboards — **not** in an expander; a divider, a bold label, then `st.columns([1.8, 4.2])`

Identical concern, three shapes. The Dashboards variant also leaves a 250 px gap between
the export button and the uploader, and floats the uploader's help icon 800 px from its
own label.

### S2-5 · Two label positions inside a single control row
Monitor `ui/monitor.py:60` and Reports `ui/reports.py:23` both put a toggle (label to the
*right* of the switch) next to a select/segmented control (label *above*). The two
controls therefore sit on different baselines — measured 16 px apart on Monitor, 20 px on
Reports — despite `vertical_alignment=` being set. Every row that mixes a toggle with a
labelled input has this.

### S2-6 · Admin's connection form is a 4-column grid whose two rows don't align
`ui/admin.py:334` `[2, 2, 1.1, 1.6]` then `:344` `[2.6, 2.6, 1.8, 1.2]`.

| | row 1 | row 2 | offset |
|---|---:|---:|---:|
| col 1 | Name @ 291 | Environment @ 291 | 0 |
| col 2 | Host @ 458 | New environment name @ 470 | **12 px** |
| col 3 | Port @ 619 | No counterpart @ 651 | **32 px** |
| col 4 | Kind @ 716 | Add @ 770 | **54 px** |

A form that looks like a grid but isn't one. The same defect appears in the dashboard
editor's widget cards, where row 1 (Type/Dataset/Title/Width) and row 2
(Column/Aggregate/Number format/Suffix) use different spans.

### S2-7 · Two selection affordances and two action sets for the same kind of list
Builder lists alerts with a **toggle** for enable/disable; Dashboards lists dashboards
with a **checkbox** for multi-select; Admin lists servers with **neither**; Schedules
lists jobs as bare **expanders** with ASCII `✓` / `✗` marks instead of the
`STATUS_META` badge vocabulary used everywhere else.

Four lists of saved objects, four different row grammars.

### S2-8 · "Result" is a leaf view exposed as top-level navigation
`app.py:130` registers Result as a peer of Monitor / Builder / Dashboards. It is reachable
only from a Monitor row's **View** button, and visiting it directly shows an empty state.
Detail views do not belong in primary nav — GitHub, Linear and Notion all keep them behind
the list they belong to.

### S2-9 · Sidebar puts the product identity below the navigation
`app.py:47` renders nav (from `st.navigation`) and *then* the brand block, a caption, a
counts line, two status badges and a permissions warning — six loose stacked elements with
uneven default gaps and no grouping.

Every reference product puts identity at the top and status/utility at the bottom.

---

## S3 — Medium

Visible, localised, individually cheap to fix.

### S3-1 · The two Monitor KPI rows don't share a grid
`ui/monitor.py:39` uses `st.columns(3)`; `:124` uses `st.columns(5 if n_off else 4)`.
Measured tile edges: **396 / 792 / 1187** vs **396 / 634 / 872 / 1109 / 1347**. Only the
left edge matches. The rows sit 30 px apart and align on nothing.

### S3-2 · KPI tiles have ragged bottoms
The Off-hours tile carries a caption its four siblings don't, so it is 60 px taller at
1680 px and 70 px taller at 1024 px. The caption slot must be reserved whether or not it
is filled.

### S3-3 · Field width varies 6× inside a single card
Dashboard editor, dataset card: Name and Environment render at ~95 px; "Its own period",
"Range" and "Also connect" render at ~750 px — full card width for a preset picker. Same
card, same form.

### S3-4 · Same action, two glyphs, two positions
Dashboard editor: row-level delete is 🗑, widget-level delete is ✕. Row controls are
↑ ↓ ⧉ 🗑; widget controls are ← → ⧉ ✕. An unlabelled `⧉▾` popover sits alone at the
*bottom-left* of each widget card while every sibling control is top-right.

### S3-5 · The same either/or choice uses two different widgets
Builder step mode is a `segmented_control` (Guided | Raw). Dashboard editor query mode is
a horizontal `st.radio` (guided / raw). Identical semantics, different component.

### S3-6 · "Save SMTP settings" wraps to two lines
`ui/admin.py:433` — `st.columns([1, 1, 3])` gives the button ~100 px for a 130 px label.
The neighbouring "Save admin notifications" (one line, 187 px) and "Save snapshot
settings" (one line, 174 px) prove there is no width rule.

### S3-7 · An action's input sits below the action
Admin: the **Send test email** button is rendered *above* the "Send a test email to"
field it consumes.

### S3-8 · Filter controls stretch to the full content width
Monitor and Dashboards both render "Filter groups" as a 1176 px multiselect holding four
short chips, and push the help icon to the far right edge, ~800 px from its label. Filters
are toolbar controls; they should be capped and grouped, not stretched.

### S3-9 · Dashboard viewer's nav row is three misaligned pieces
`ui/dashboards.py:1052` — `[1.2, 6.2, 1.6]`. The "All dashboards" button centre sits at
y≈127, the tab pills at y≈140, the Tabs popover at y≈127. The rule beneath the tab strip
starts at x=571 and stops at x=1390 — it neither spans the content nor aligns to either
neighbour.

### S3-10 · Save-state banner changes shape, shifting the tabs below it
Dashboard editor: the validation slot renders a red **expander** when there are problems
and a green **success box** when there are none. The two have different heights, so the
Section tab bar jumps vertically as you edit.

### S3-11 · Card padding is doubled by nesting
Monitor and Builder both put bordered `st.container(border=True)` rows *inside* a bordered
`st.expander`, producing box-in-box with two borders and two paddings ~14 px apart.

### S3-12 · Long descriptions run to a 200-character measure
Reports' intro paragraph spans the full 1176 px. Readable measure is 60–80 characters;
this is ~200. Every page-level description has the same problem.

---

## S4 — Low

Polish; no functional or comprehension cost.

- **S4-1** `st.divider()` used at three different widths on the same page (dashboard editor).
- **S4-2** "Add filter" (x=228) and "Add step" (x=219) sit 9 px apart on the Builder — a visible stagger in a left-aligned button stack.
- **S4-3** Vertical rhythm is Streamlit's default gap everywhere; no spacing scale, so unrelated blocks are as far apart as related ones.
- **S4-4** `.streamlit/config.toml` sets 14 theme keys that the installed Streamlit **1.45.1 silently rejects**, including `headingFontSizes`, `headingFontWeights`, `baseRadius`, `buttonRadius`, `codeFontSize` and the semantic colour palette. The intended type scale is therefore not applied at all; headings fall back to Streamlit's defaults. `requirements.txt` pins only `streamlit>=1.45`.
- **S4-5** Dashboard viewer's per-dataset export rows leave a 250 px gap between the dataset name and its CSV button, then stretch two 40 px labels to 155 px buttons.
- **S4-6** The dashboard editor has no page title, breadcrumb or context header of any kind — it opens straight into a form row.

---

## Target design system

The refactor introduces `kdbmonitor/ui/layout.py` as the single source of layout truth,
modelled on the conventions shared by Fluent 2, Atlassian Design System, Linear, Notion,
the Stripe dashboard and GitHub Primer.

### Grid
A **12-unit grid**. Every multi-control row declares spans that sum to ≤ 12; the helper
appends the remaining units as a spacer. Rows in the same card therefore share column
edges by construction, which is what S2-6 and S3-3 are missing.

### Page shell
```
page_header(icon, title, subtitle, actions=[...])   # h2 title, caption, right-aligned actions
divider
section(title, description=None, actions=None)      # h4, one spacing step above
```
One heading ladder — `h2` page, `h4` section, `h5` subsection — replacing 7 page titles at
two levels and 30 bold-text labels.

### Widths
Two widths only, and a page may not alternate between them within a stack:
- **Full content width** — lists, tables, cards, headers, dividers.
- **Field width** — inputs are capped by their grid span, *inside* a full-width card.

This keeps `common.py`'s correct instinct (a host-name box must not span 27 inches)
while giving every block one right edge.

### List rows
`list_row(status, title, meta, primary_action, overflow_actions)` — one shape for Monitor
alerts, Builder alerts, Dashboards, Admin servers and Schedules jobs:

```
[ status badge ][ title + secondary line ][ meta ][ primary ][ ⋯ ]
     span 2              span 5             span 2   span 2   span 1
```
One primary action inline; everything else behind `⋯`. Fixes S1-1, S1-2, S1-3 and S2-7 in
one move, and holds at 1024 px because the row now carries five things, not nine.

### Action placement
- Page-level create/commit → **top-right of the page header**.
- Row-level → **right end of the row**, one primary + overflow.
- Form-level submit → **bottom-left of the form card**, next to its validation text.

Three rules, no exceptions.

### Toolbar
`toolbar()` for filter/search rows: capped-width controls, left-aligned, actions right.
Fixes S3-8.

### KPI tiles
`kpi_row(items)` — uniform height, reserved caption slot, laid out on the same 12-unit
grid so a 3-tile row and a 5-tile row share outer edges. Fixes S3-1 and S3-2.

### Type scale
A single scoped stylesheet injected once, so the intended scale applies on the installed
Streamlit rather than depending on theme keys the running version rejects (S4-4).

---

## Fix order

1. `layout.py` + type scale (unblocks everything)
2. App shell & sidebar — S2-8, S2-9
3. Page headers across all 7 pages — S2-1, S2-2, S2-3
4. List rows — S1-1, S1-2, S1-3, S2-7
5. Forms & grid alignment — S1-4, S2-4, S2-5, S2-6, S3-6, S3-7
6. Monitor KPIs & toolbars — S3-1, S3-2, S3-8
7. Dashboard viewer & editor — S3-3, S3-4, S3-5, S3-9, S3-10, S4-6
8. Polish — S3-11, S3-12, S4-*

The test suite (2015 tests) is treated as a hard contract: every widget `key=` is
preserved and the suite must stay green after each step.

---

# Results

All of the above is implemented. `kdbmonitor/ui/layout.py` (554 lines) is the new
single source of layout truth; 13 files changed, +928 / −643. The suite is
**2015 passed, 31 skipped** — the same as the baseline, with every widget `key`
preserved.

Re-measured the same way: Chromium, the same seeded database, the same three
viewports, the same DOM geometry dump.

| Measure | Before | After |
|---|---:|---:|
| Distinct `st.columns([...])` ratios | 80 | **0** |
| Distinct content right-edges — Builder | 7 | **3** |
| Distinct content right-edges — Admin | 4 | **2** |
| Distinct button widths — Admin | 8 | **4** |
| Distinct button widths — Builder | 7 | **5** |
| Distinct button widths — Schedules | 5 | **3** |
| Distinct button widths — Dashboards | 6 | **4** |
| Page-title heading levels across 7 pages | 2 (`h1`, `h3`) | **1 (`h2`)** |
| Section labels that are bold body text | 30 | **3** |
| Overlapping buttons at 1024px — Builder | 6 | **0** |
| Overlapping buttons at 1024px — Admin | 1 | **0** |
| Narrowest rendered button at 1024px | 34px | **52px** (an icon-only menu) |
| Non-heading elements rendered as `h3` — Monitor | 8 | **0** |

The right-edge counts do not reach 1 because they cannot: 1610px is the content
column, 1596px is inside an expander's border and 1588px inside a card's. Those
are nesting insets, which is what a border is for. What has gone is the
*alternation* — Admin no longer steps between 1207px and 1596px section by
section, and the Builder no longer draws a section heading at one width and its
own body at another.

## What each finding became

| ID | Resolution |
|---|---|
| S1-1, S1-2, S1-3 | One `list_row` shape for every list of saved objects: status/select · title · meta · **one** primary action · `⋯` menu. Dashboards went from 6 co-equal buttons to Open + menu; the Builder from 5 to Edit + menu. |
| S1-4 | `form_area()` retired. Cards are full width; inputs are narrowed by their grid *span* inside them. |
| S2-1 | `page_header` on all 7 pages, `h2`. Schedules' `st.title` gone. |
| S2-2 | `section()` — a real `h4`/`h5`, with the space above it that says so. |
| S2-3 | Three rules: page create/commit top-right; row actions right end of row; form submit bottom-left with its validation line. |
| S2-4 | Import/export is one **"Share" menu in the page header** on Builder, Admin and Dashboards — a page-level action in the page-level action zone. |
| S2-5 | `layout.switch()` / `layout.check()` give a toggle and a checkbox a label above, so they land on the row's baseline. |
| S2-6 | Both rows of Admin's connection form are measured in the same units from the same origin. Same for the editor's widget cards. |
| S2-7 | Same row grammar for alerts, dashboards, servers and jobs. Schedules' ASCII `✓`/`✗` replaced by the `STATUS_META` badge vocabulary. |
| S2-8 | Navigation grouped **Alerts / Dashboards / Setup**. Result stays registered (so `st.switch_page` reaches it) but is hidden from the list — it is a detail view, reached from the Monitor's View button, and now opens with a breadcrumb. |
| S2-9 | Product mark moved above the nav via `st.logo`; the sidebar body is one **Status** panel instead of six loose stacked elements. |
| S3-1, S3-2 | `kpi_row` fills the content column whatever the tile count, so the 3-tile and 5-tile rows share both outer edges; each tile is exactly three lines by construction. |
| S3-3 | Field spans inside the editor's cards, so a preset picker is a field's width, not the card's. |
| S3-4 | Delete says delete at both levels; duplicate and delete moved behind the same `⋯` menu the rest of the app uses. |
| S3-5 | One widget for one either/or — a horizontal radio, in the Builder and the editor alike. See `layout.MODE_CONTROL` for why radio and not segmented control. |
| S3-6, S3-7 | Save buttons sized by label length via `BUTTON` / `ACTION_SPAN`; the test-email address now sits above the button that sends to it. |
| S3-8 | `toolbar()` — filters at their own width, not the window's. |
| S3-9 | The viewer's back control, tabs and tab menu on one grid; the dashboard's name is the page title. |
| S3-10, S4-6 | The editor has a breadcrumb, a page title, Save top-right and a menu — and the validation slot no longer moves the Section tabs. |
| S3-11 | Expander padding halved so a group of cards is not boxes inside boxes. |
| S3-12 | `.kdb-measure` caps descriptions at 78ch. |
| S4-4 | Recorded, not fixed: `.streamlit/config.toml` still sets 14 keys Streamlit 1.45.1 rejects. The type scale now lives in `layout.inject_css()`, which applies on the installed version. Pinning a newer Streamlit and moving it back is a follow-up. |

## Known limits

- **Streamlit columns do not reflow.** Below about 1100px, long button labels
  wrap to two lines rather than the row rewrapping. That is graceful — the
  overlapping is gone — but it is a wrap, not a responsive layout. A real
  breakpoint system would need custom components.
- **`segmented_control` is kept off form fields.** Streamlit 1.45's `AppTest`
  cannot re-run a page holding a single-select segmented control whose value is
  a string; `element_tree` iterates the value as a sequence. This is why the
  guided/raw choice is a radio in both places rather than a segmented control in
  both.
- **Three bold-text labels remain** — in `dashboards.py`, `popup.py` and
  `tables.py`. They are widget *titles* and data labels, not section headings,
  so bold body text is correct for them.
