# Row spacing — design

**Status:** design agreed, not built.
**Date:** 2026-08-14

---

## 1. The problem

A dashboard built in sections reads as one undifferentiated column. Write a
heading and a table, then another heading and another table, and the second
heading sits hard against the first table — closer to the section it is ending
than to the one it is introducing. The eye groups by proximity, so the layout
says the opposite of what the author meant.

There is no way to say otherwise. A `Row` carries its widgets and a printed
height, and the space between rows is a constant — `GUTTER = 0.28` in
`core/dashpdf.py` on paper, and whatever Streamlit does between blocks on
screen. Neither is reachable from the editor.

So: let a row ask for air above it, and let a row ask for air between its
widgets.

---

## 2. What this is and is not

In scope:

- Extra vertical space before a chosen row.
- Extra horizontal space between the widgets inside a chosen row.
- Both honoured on screen and in the PDF, from one stored value.

Deliberately out:

- **Padding inside a widget** — between a widget's border and its content. That
  is the widget's own business and the renderers already own it.
- **A dashboard-wide spacing default.** Considered and dropped: the gap that
  needs opening is the one before a heading, and the gap that must stay tight is
  the one between a heading and its own table. A single number cannot do both,
  and adding one alongside per-row gaps is two controls where one will do.
- **A spacer element** inserted into the layout. It would make empty rows part
  of the document — one more thing to move when reordering, and one more thing
  to explain.
- **Negative gaps.** Pulling rows together is not the problem being solved.

---

## 3. The model

Two fields on `Row`, in `core/dashboard_models.py`:

```python
@dataclass
class Row:
    widgets: list[Widget] = field(default_factory=list)
    height_in: float = 2.5
    gap_above_in: float = 0.0     # extra air before this row
    col_gap_in: float = 0.0       # extra air between its widgets
```

**Inches**, because that is already this document's unit: `height_in`, the page
geometry, the margins and the gutter all speak it, and the screen converts from
it through `row_height_px`. A second unit — points, pixels, or named sizes —
would be a second mental model for one idea, and would have to be reconciled at
the page boundary anyway.

**Both default to `0.0`, and both are additions** to the spacing that exists
today rather than replacements for it. A dashboard saved before this feature
loads with zeroes and must render identically, to the page break; that is the
load-bearing test in §7. It also means the fields need no migration —
`dashboard_from_dict` reads a missing key as `0.0` the way it already reads
every other absent field.

Range is `0.0` to `4.0` inches. Above that a gap is not spacing, it is a blank
page, and the row height control is the honest way to ask for one.

---

## 4. On paper

`core/dashpdf.py` owns page geometry, and both fields land in functions that
already exist.

**Vertical.** `paginate` advances `y` by `gap_above_in` before placing a row:

```python
for part in split_rows(rows, results, cache):
    lead = part.row.gap_above_in if part.part == 0 else 0.0
    ...
```

The gap belongs to the row's *first* part only. A table long enough to continue
onto later parts asked for air before it started, not before each of its
continuations.

**Swallowed at a page top.** A row that begins a page prints no gap above it.
Leading space exists to separate a heading from what precedes it; at the top of
a page nothing does, and printing it there would open a hole in the margin and
push the page's content down for no reason. This is the ordinary typographic
rule and it falls out of `paginate` naturally — the gap is added when placing
after something, not when starting fresh.

**Horizontal.** The per-row gutter becomes `GUTTER + row.col_gap_in`. Three
places compute widget widths and all three already read the gutter from one
place, so all three follow: `_render_page` (drawing), `_widget_width_in`
(measuring a table's columns), and `fits_width` (deciding whether the paper has
to turn).

That last consequence is worth stating plainly: **a wider column gap leaves each
widget narrower, so a table that fitted portrait may now legitimately turn the
report landscape.** That is correct — the alternative is columns colliding — and
it needs no new code, because `choose_page` already asks `fits_width`. It is a
surprise only if nobody wrote it down, which is what this paragraph is for.

---

## 5. On screen

`ui/dashboards.py`, in `render_rows`. Both gaps are drawn with native
Streamlit; neither needs CSS.

**Vertical** is an empty fixed-height container:

```python
if row.gap_above_in:
    st.container(height=row_height_px(row.gap_above_in), border=False)
```

Exact, and converted through the same `row_height_px` the row's own height uses.

**Horizontal** cannot use `st.columns(gap=…)` — that argument takes only
`"small" | "medium" | "large"`, which is a different unit and a coarser one. It
does not need to. `st.columns` weights are *relative*, so an inch gap can be
expressed as a weight against the row's total inch width and rendered as an
empty spacer column:

```python
st.columns([2.0, 0.4, 3.0])      # widget, gap, widget
```

The row's total width in inches is `PORTRAIT.content_w`, which is what the same
row occupies on the page, so one stored number drives both surfaces and they
cannot drift. Spacer columns appear only where `col_gap_in > 0`, so a row that
has not asked for one is built exactly as it is today.

A helper, `column_spec(row) -> list[float]`, returns the weights with spacers
interleaved. Pure, so the arithmetic is tested without a browser, and shared by
the live view and the editor's preview — which is what stops the preview
disagreeing with the page it previews.

---

## 6. In the editor

`ui/dashboard_editor.py`, in the row header that already carries `Height (in)`:

```
Row 3   Height (in) [0.5]   Gap above [0.6]   Cols gap [0.0]   Move to […]
```

Both are `number_input`s with `step=0.1`, bounds `0.0`–`4.0`, and help text
saying what they do and that `0` is the standard spacing. `Cols gap` is
disabled, with a reason, on a row holding fewer than two widgets — there is
nothing for it to separate, and a control that silently does nothing teaches
the reader that the feature does not work.

The editor's page-break preview derives from `paginate`, so it accounts for the
new gaps without being told.

---

## 7. Testing

**The regression that matters most:** with both fields at `0.0`, `plan_rows`
placements and `page_count` must be identical to today for every dashboard in
`docs/examples/`. The feature is invisible until asked for, and that is the
claim most likely to be quietly broken.

Then, on paper: a gap pushes the rows below it down by exactly its size; a row
starting a page prints no gap; a gap mid-page does print; a gap belongs to a
long table's first part and not to its continuations; a column gap narrows each
widget by the right amount; a column gap large enough can flip `choose_page`
from portrait to landscape.

On screen: `column_spec` interleaves spacers only where a gap is set, returns
today's weights when none is, and puts no trailing spacer after the last
widget. Rendering tests through `AppTest` cover a dashboard with gaps, one
without, and a row of one widget carrying a column gap that must be ignored.

Model: both fields survive a save/load round trip, and a dashboard payload
written before they existed loads with zeroes rather than raising.

**And in the running application.** The tests above can all pass while the page
still looks wrong — a container that collapses, a spacer column that Streamlit
renders with its own padding, a gap that reads as double what was asked for.
This feature is a claim about what something looks like, so it is not finished
until it has been looked at: the app is started, a dashboard with a heading, a
table, a gap, a second heading and a second table is opened, and the spacing is
confirmed by eye against the same dashboard exported to PDF.
