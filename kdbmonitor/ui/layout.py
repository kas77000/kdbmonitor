"""The app's layout system: one grid, one heading ladder, one place for actions.

Before this existed every page invented its own geometry — 80 distinct
``st.columns([...])`` ratios across 105 calls, seven different content right
edges on the Builder alone, page titles at two heading levels, and thirty
section labels that were bold body text rather than headings. Nothing lined up
with anything, because nothing was ever asked to.

Everything here is about *where things sit*. What they say and what they do
stays in the page modules; this file only owns the frame.

The rules, in the order they matter:

**One grid.** :data:`GRID` units across the content column. Every row of
controls declares spans that sum to at most 24 and :func:`grid` turns the
remainder into a trailing spacer. Two rows in the same card that both use the
grid share their column edges by construction — which is exactly what a form
built from ``[2, 2, 1.1, 1.6]`` and then ``[2.6, 2.6, 1.8, 1.2]`` cannot do.

**One heading ladder.** :func:`page_header` is the only page title and
:func:`section` the only section label. Streamlit's stock ``h4`` renders at the
same 14px as body text, and the type scale in ``.streamlit/config.toml`` is
silently rejected by Streamlit 1.45, so :func:`inject_css` sets the scale here
instead — otherwise there is no ladder to climb.

**Two widths, never alternating.** Lists, tables, cards, headers and dividers
take the full content width; inputs are narrowed by their grid *span* inside a
full-width card, never by narrowing the card. ``common.form_area`` was right
that a host-name box must not span a 27" monitor, and wrong that the fix is to
shrink the section — that is what left Admin with blocks ending at 1207px and
1596px stacked directly on top of each other.

**One place for each kind of action.** Page-level create/commit goes top-right
of the page header. Row-level goes at the right end of the row: one primary
button, everything else behind :func:`overflow`. Form submit goes bottom-left of
the form card, beside its validation text. A row that carried six co-equal
buttons is why the Dashboards list collapsed into overlapping labels at 1024px.

**A button is as wide as its label.** The grid reserves the column; the button
sits at its natural width inside it. Only a page header's actions fill their
slots, because those are right-aligned against the content edge.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from typing import Iterable, Iterator, Optional, Sequence

import streamlit as st

# --- the grid -------------------------------------------------------------- #

#: Units across the content column. Every row of controls spends at most this
#: many, so rows in the same card share their column edges.
#:
#: Twenty-four rather than twelve because a button's width has to land near its
#: label — `tests/test_all_pages_render.py` holds the app to that, and rightly:
#: a lone "View" stretched to a quarter of the page is what produced a 636px
#: "Generate report". Twelve units cannot express "a little narrower than a
#: quarter" without fractions; twenty-four can, and every span stays whole.
GRID = 24

#: A page-header action button — an eighth of the row.
#:
#: The header is the one place a button is still stretched to its column, so
#: that the actions end flush with the content's right edge; everywhere else a
#: button is as wide as its label (see BUTTON below). Three units is about
#: 147px on a wide screen, which fits "New dashboard" with a little air and does
#: not leave "Share" swimming in 90px of nothing.
ACTION_SPAN = 3

#: The most actions a page header may carry: one primary, and one menu for the
#: rest. Enforced by :func:`page_header`, because it was worth enforcing.
MAX_HEADER_ACTIONS = 2

#: Spans that *reserve room* for a button — they do not set its width.
#:
#: A button is as wide as its label. Stretching one to fill its column is what
#: put a 190px box around a 99px "Refresh" and a 292px box around "All
#: dashboards", and `common.py` had the rule right from the start: only a row of
#: sibling buttons should fill its columns, and even then not by a mile. So
#: these spans size the *column* — the button sits at its natural width inside
#: it, and the leftover is the gap before whatever comes next.
#:
#:   BUTTON_TIGHT — an icon, or one short word:  ⋯, Excel, CSV, Copy
#:   BUTTON       — one word:                    Save, Start, Open, View
#:   BUTTON_LONG  — two words:                   Add transform, Send test email
#:
#: The one exception is a page header, where the actions are right-aligned to
#: the content edge and therefore do fill their slots — see ACTION_SPAN.
BUTTON_TIGHT = 2
BUTTON = 3
BUTTON_LONG = 5

#: Which widget says "one of these few".
#:
#: A **form field** that picks a mode — guided or raw, this way or that — is a
#: horizontal ``st.radio``. A **view control** that picks a scale or a filter —
#: the Monitor's check granularity, the editor's Section — is an
#: ``st.segmented_control``. The Builder said guided/raw with a segmented
#: control and the dashboard editor said it with a radio, one page apart.
#:
#: The split is not arbitrary. A segmented control is a view switch in Fluent,
#: Primer and Linear alike, and a radio group is a form field in all three.
#: There is also a practical reason to keep segmented controls off form fields
#: here: Streamlit 1.45's ``AppTest`` cannot re-run a page holding a
#: single-select segmented control whose value is a string — ``element_tree``
#: iterates that value as a sequence — so a form field built from one is a test
#: that dies the moment anything reruns.
MODE_CONTROL = "radio"

#: The standard list row: lead | title | meta | primary action | overflow.
#: Five things, not nine — which is what keeps it whole at 1024px.
ROW_SPANS: tuple[int, ...] = (4, 10, 5, 3, 2)

#: The same row where the object has two facts worth showing side by side and
#: exactly one thing you can do to it — a row count and a countdown, and View.
#: No overflow slot, because an empty menu column is as arbitrary as a full one.
ROW_SPANS_DETAIL: tuple[int, ...] = (4, 9, 3, 5, 3)

#: The same row again where the lead is a selection checkbox rather than a
#: status badge, so the lead needs a fifth of the room a badge does.
ROW_SPANS_SELECT: tuple[int, ...] = (2, 12, 5, 3, 2)

#: A single icon-only button (reorder, duplicate, remove) sat at a row's end.
ICON_SPAN = 2

#: Field spans, so a form declares intent rather than a number. A form row is
#: built from these and the grid pads whatever is left over. FIELD is a quarter
#: of the content column — about 290px on a wide screen, which is the width a
#: name, an address or a select is comfortable at and a host-name box stops
#: being comfortable past.
FIELD_WIDE = 12     # a query, an address list, a sentence
FIELD = 6           # the default: a name, a select, an address
FIELD_NARROW = 4    # a port, a count, an interval
FIELD_TINY = 3      # a checkbox, a unit, a two-digit number


def _weights(spans: Sequence[float], *, pad: bool) -> tuple[list[float], list[int]]:
    """Grid spans -> ``st.columns`` weights, plus where each span landed.

    Streamlit takes relative weights, so the spans can be handed over as they
    are; the only work is appending the unspent remainder as a spacer, which is
    what stops a three-field row from stretching each field to a third of the
    screen.
    """
    used = sum(spans)
    weights = list(spans)
    if pad and used < GRID:
        weights.append(GRID - used)
    return weights, list(range(len(spans)))


def grid(*spans: float, gap: str = "small", align: str = "bottom",
         pad: bool = True) -> list:
    """Columns on the shared grid, in the order the spans were given.

    ``pad`` appends the unspent units as an empty spacer column, which is what
    keeps a two-field row from stretching both fields to half the page. Pass
    ``pad=False`` for a row that genuinely wants the whole width — a list row,
    a header, a toolbar with a right-aligned end.

    ``align`` is Streamlit's ``vertical_alignment``. "bottom" lines up the
    *inputs* of a row of labelled fields, which is nearly always what a form
    wants; "center" is for rows of text and badges.
    """
    weights, slots = _weights(spans, pad=pad)
    cols = st.columns(weights, gap=gap, vertical_alignment=align)
    return [cols[i] for i in slots]


def field_row(n: int, *, span: int = FIELD, gap: str = "small") -> list:
    """``n`` equal fields of ``span`` units each, left-aligned on the grid.

    The row every settings form is built from. Two such rows in the same card
    align down their left edges whatever ``n`` each one uses, because both are
    measured from the same origin in the same units.
    """
    return grid(*([span] * n), gap=gap, align="bottom")


# --- the page shell -------------------------------------------------------- #

def page_header(title: str, *, icon: Optional[str] = None,
                subtitle: Optional[str] = None, actions: int = 0,
                action_span: Optional[int] = None,
                divider: bool = True) -> list:
    """The one page title, with the one place for page-level actions.

    Returns ``actions`` columns at the right of the title row, already sized —
    render the buttons into them with ``use_container_width=True`` so a row of
    siblings comes out as equal blocks. An empty list when ``actions`` is 0.

    The title is an ``h2``: an ``h3`` (Streamlit's ``st.subheader``) renders at
    16px against 14px body text, close enough to the section labels beneath it
    that page and section read as peers.
    """
    if actions > MAX_HEADER_ACTIONS:
        raise ValueError(
            f"a page header takes at most {MAX_HEADER_ACTIONS} actions, not "
            f"{actions} — put the rest behind a menu in the first slot")
    # Header actions are the one place a button fills its slot, so that they
    # end flush with the content's right edge. `action_span` narrows that slot
    # when the labels are short: a lone "Edit" in a slot cut for "New dashboard"
    # is 147px of box around 78px of label.
    span = ACTION_SPAN if action_span is None else action_span
    heading = f"{icon} {title}" if icon else title
    if actions:
        cols = grid(GRID - span * actions, *([span] * actions),
                    align="center", pad=False)
        with cols[0]:
            st.markdown(f"## {heading}")
            if subtitle:
                _measured_caption(subtitle)
        slots = cols[1:]
    else:
        st.markdown(f"## {heading}")
        if subtitle:
            _measured_caption(subtitle)
        slots = []
    if divider:
        st.divider()
    return slots


def section(title: str, *, description: Optional[str] = None,
            actions: int = 0, level: int = 4) -> list:
    """A section label inside a page, with the space above it that says so.

    ``level`` 4 is a section and 5 a subsection. Both are real headings, so the
    ladder page -> section -> subsection is visible rather than implied by three
    weights of bold text.
    """
    marks = "#" * level
    if actions:
        cols = grid(GRID - ACTION_SPAN * actions, *([ACTION_SPAN] * actions),
                    align="center", pad=False)
        with cols[0]:
            st.markdown(f"{marks} {title}")
            if description:
                _measured_caption(description)
        return cols[1:]
    st.markdown(f"{marks} {title}")
    if description:
        _measured_caption(description)
    return []


def _measured_caption(text: str) -> None:
    """A caption capped to a readable measure rather than the window's width.

    A page description stretched across 1176px runs to roughly 200 characters a
    line; 60-80 is what stays readable, and the cap is applied in CSS so the
    text still reflows when the window is narrow.
    """
    st.markdown(f'<div class="kdb-measure">{text}</div>',
                unsafe_allow_html=True)


@contextmanager
def card(*, border: bool = True) -> Iterator[None]:
    """A full-width bordered block: the container every section's body sits in.

    Full width is the point. Narrowing the *card* is what made Admin alternate
    between two right edges section by section; the fields inside are narrowed
    by their grid span instead, which gets sensible input sizes without the
    page losing its edge.
    """
    with st.container(border=border):
        yield


@contextmanager
def list_row(*, spans: Sequence[float] = ROW_SPANS,
             align: str = "center") -> Iterator[list]:
    """One row of a list of saved objects, on the shared grid.

    The same shape for alerts, dashboards, servers and scheduled jobs, because
    they are the same thing: a status, what it is, some facts about it, the
    action you nearly always want, and a menu for the rest.
    """
    with st.container(border=True):
        yield grid(*spans, align=align, pad=False)


def switch(col, label: str, value: bool, *, key: Optional[str] = None,
           help: Optional[str] = None, disabled: bool = False) -> bool:
    """A toggle that sits on the same baseline as the fields beside it.

    ``st.toggle`` puts its label to the *right* of the switch and nothing above
    it, so a toggle dropped into a row of labelled inputs has neither their
    label position nor their baseline — measured 16px out on the Monitor and
    20px on Reports, with ``vertical_alignment`` set and unable to help, because
    the two controls are different heights for a different reason.

    Giving the toggle a label of its own, in the row where every other field
    keeps its label, is what puts it back on the line.
    """
    with col:
        # Streamlit draws no help icon for a collapsed label, so the help text
        # rides on the label as the browser's own tooltip rather than being lost.
        mark = " ⓘ" if help else ""
        title = f' title="{help}"' if help else ""
        st.markdown(
            f'<div class="kdb-field-label"{title}>{label}'
            f'<span class="kdb-help">{mark}</span></div>', unsafe_allow_html=True)
        return st.toggle(label, value=value, key=key, help=help,
                         disabled=disabled, label_visibility="collapsed")


def check(col, label: str, value: bool = False, *, key: Optional[str] = None,
          help: Optional[str] = None, disabled: bool = False) -> bool:
    """A checkbox laid out like a labelled field. See :func:`switch`.

    Same problem, same fix: ``st.checkbox`` labels to the right and puts nothing
    above, so one dropped into a row of inputs floats about 10px clear of their
    baseline.
    """
    with col:
        # The label goes *above*, exactly as `switch` does it, and the control's
        # own inline label is collapsed. Leaving that inline label visible put
        # the text beside a box in a narrow column, where it wrapped underneath
        # and took the whole control 30px off the row's baseline.
        mark = " ⓘ" if help else ""
        title = f' title="{help}"' if help else ""
        st.markdown(f'<div class="kdb-field-label"{title}>{label}'
                    f'<span class="kdb-help">{mark}</span></div>',
                    unsafe_allow_html=True)
        return st.checkbox(label, value=value, key=key, help=help,
                           disabled=disabled, label_visibility="collapsed")


def toolbar(*spans: float, gap: str = "small") -> list:
    """A filter/search row: controls at their own width, the rest left empty.

    Filters are not content. A four-chip group filter stretched to the full
    1176px column is why the help icon for one ends up 800px from its label.
    """
    return grid(*spans, gap=gap, align="bottom", pad=True)


@contextmanager
def overflow(*, help: str = "More actions",
             use_container_width: bool = True) -> Iterator[None]:
    """The menu that holds every row action except the primary one.

    A list row can carry one action inline. Six — which is what Dashboards and
    the Builder each grew to — do not fit any window and physically overlap
    below about 1200px. Everything past the first goes in here.
    """
    with st.popover("", icon=":material/more_horiz:", help=help,
                    use_container_width=use_container_width):
        yield


# --- tiles ----------------------------------------------------------------- #

def kpi(col, label: str, value, *, color: Optional[str] = None,
        caption: Optional[str] = None) -> None:
    """One metric tile: exactly three lines, whatever the text and the width.

    The label and the note are each held to one line and the note's line is
    reserved whether or not it is used, so every tile in a row is the same
    height by construction rather than by luck. Left to reflow, the one tile
    with a caption stood 60px taller than the four beside it at 1680px and 70px
    taller at 1024px, and the row ended on a ragged edge at every size in
    between. The full text stays available as the browser's own tooltip.
    """
    with col.container(border=True):
        st.markdown(f'<div class="kdb-tile-label" title="{label}">{label}</div>',
                    unsafe_allow_html=True)
        # The number is a number, not a section heading. Rendering it as `###`
        # put eight h3s on the Monitor that no reader would call headings, and
        # left the page's real heading ladder competing with its own metrics.
        tint = f' style="color:{TILE_COLORS[color]}"' if color and value else ""
        st.markdown(f'<div class="kdb-tile-value"{tint}>{value}</div>',
                    unsafe_allow_html=True)
        note = caption or ""
        st.markdown(f'<div class="kdb-tile-note" title="{note}">'
                    f'{note or "&nbsp;"}</div>', unsafe_allow_html=True)


#: Tiles narrower than this stop holding their own label, so a row takes at
#: most six and wraps rather than shrinking further.
MIN_TILE_SPAN = 4

#: The semantic status colours, matching the ones `.streamlit/config.toml`
#: declares. Spelled out here because Streamlit 1.45 rejects those config keys
#: (see `inject_css`), so `:red[...]` and this have to be kept in step by hand.
TILE_COLORS = {"red": "#f85149", "green": "#3fb950", "orange": "#d29922",
               "blue": "#3b82f6", "violet": "#a371f7", "gray": "#8b98a5"}


def kpi_row(items: Iterable[tuple], *, per_row: Optional[int] = None) -> None:
    """A row of metric tiles that spans the full content width.

    Each item is ``(label, value, color, caption)``. The row is divided between
    however many tiles it holds rather than each tile taking a fixed span, so a
    row of three and a row of five below it start and end on the same two
    edges. Sharing the outer edges is the whole of what makes two stacked rows
    of different lengths read as one block; matching the inner edges as well
    would need the counts to match, which they do not.
    """
    items = list(items)
    if not items:
        return
    cap = GRID // MIN_TILE_SPAN
    width = per_row or min(len(items), cap)
    for start in range(0, len(items), width):
        chunk = items[start:start + width]
        span = GRID / len(chunk) if len(chunk) == width else GRID / width
        cols = grid(*([span] * len(chunk)), align="top",
                    pad=len(chunk) < width)
        for col, (label, value, color, caption) in zip(cols, chunk):
            kpi(col, label, value, color=color, caption=caption)


# --- the type scale and the rhythm ----------------------------------------- #

# Streamlit 1.45 rejects `headingFontSizes`, `headingFontWeights`, `baseRadius`,
# `buttonRadius`, `codeFontSize` and the semantic colour keys in config.toml —
# it logs "not a valid config option" for each and carries on with its own
# defaults. Those defaults give h3 16.4px, h4 14px, h5 11.6px against 14px body
# text, so `st.subheader` is barely a heading and h4/h5 are unusable as one.
# The scale therefore lives here, where it applies on the version that is
# actually installed. Selectors are pinned to `data-testid` values, which are
# Streamlit's public hooks for exactly this.
_CSS = """
<style>
/* --- type scale: page (h2) -> section (h3/h4) -> subsection (h5) ---------- */
[data-testid="stMain"] h1, [data-testid="stSidebar"] h1 {
  font-size: 26px; font-weight: 700; letter-spacing: -0.015em;
}
[data-testid="stMain"] h2 {
  font-size: 21px; font-weight: 700; letter-spacing: -0.012em;
  padding: 0; margin: 0 0 2px 0;
}
[data-testid="stMain"] h3 {
  font-size: 17px; font-weight: 650; letter-spacing: -0.008em;
  padding: 0; margin: 0;
}
[data-testid="stMain"] h4 {
  font-size: 15px; font-weight: 600; letter-spacing: -0.004em;
  padding: 0; margin: 0;
}
[data-testid="stMain"] h5 {
  font-size: 12px; font-weight: 600; letter-spacing: 0.06em;
  text-transform: uppercase; opacity: 0.72; padding: 0; margin: 0;
}
[data-testid="stMain"] h6 { font-size: 13px; font-weight: 600; padding: 0; margin: 0; }

/* --- vertical rhythm: a section is separated from what precedes it ------- */
[data-testid="stMain"] [data-testid="stHeadingWithActionElements"]:has(h4),
[data-testid="stMain"] [data-testid="stHeadingWithActionElements"]:has(h5) {
  margin-top: 12px;
}
[data-testid="stMain"] hr { margin: 8px 0 16px 0; }

/* --- a readable measure for descriptions --------------------------------- */
.kdb-measure {
  max-width: 78ch; font-size: 13px; line-height: 1.5; opacity: 0.75;
}

/* Every widget label occupies the same band, whatever control it belongs to.
   A segmented control's label sat 4px above a selectbox's in the same row,
   because the two controls ship different label margins.

   Only `min-height` — setting `display` here would override the `display:none`
   Streamlit uses for `label_visibility="collapsed"` and put every hidden label
   back on the screen. */
[data-testid="stMain"] [data-testid="stWidgetLabel"] {
  min-height: 1.6em; margin-bottom: 0.25rem;
}
/* Except a checkbox's or a toggle's own label, which sits *beside* the control
   rather than above it and shares the same testid. Given a 1.6em floor it
   stopped fitting on the control's line and dropped underneath it, which is
   what left "not" sitting below its own box in the filter rows. */
[data-testid="stMain"] [data-testid="stCheckbox"] [data-testid="stWidgetLabel"] {
  min-height: 0; margin-bottom: 0;
}

/* A stand-in for Streamlit's own widget label, so `layout.switch` occupies the
   same label row as the inputs beside it and lands on their baseline. */
.kdb-field-label {
  font-size: 14px; line-height: 1.6; margin-bottom: 0.25rem;
  min-height: 1.6em;
}
.kdb-help { opacity: 0.5; cursor: help; }

/* A saved object's description is a subtitle, not the object. Left to run, a
   300-character one turned a list row into a 450px card and left every row in
   the list a different height. */
.kdb-clamp {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; opacity: 0.72; font-size: 13px; line-height: 1.45;
}

/* --- density: a card's contents are one block, not a stack of paragraphs -- */
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"]
  [data-testid="stVerticalBlock"] { gap: 0.7rem; }

/* A group of rows is a container of cards, so the group's own padding should
   not be added to each card's. Without this the two borders sit 14px apart and
   the list reads as boxes inside boxes. */
[data-testid="stMain"] [data-testid="stExpanderDetails"] { padding: 0 0.5rem 0.5rem; }

/* --- tiles: label, value, note — one line each, so a row ends flat ------- */
.kdb-tile-label, .kdb-tile-note {
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.kdb-tile-label { font-size: 13px; line-height: 1.4; opacity: 0.72; min-height: 18px; }
.kdb-tile-value {
  font-size: 21px; font-weight: 650; line-height: 1.3; letter-spacing: -0.01em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.kdb-tile-note { font-size: 12px; line-height: 1.4; opacity: 0.7; min-height: 17px; }

/* A bordered block that shares a row with others fills the row's height, so
   the row has one bottom edge however much text each block holds. */
[data-testid="stColumn"] > div > [data-testid="stVerticalBlockBorderWrapper"] {
  height: 100%;
}

/* --- buttons ------------------------------------------------------------- */
/* Labels are allowed to wrap. Forcing nowrap only converts a too-narrow slot
   from a two-line button into one that overlaps its neighbour, and the slots
   are sized by BUTTON/BUTTON_TIGHT/ACTION_SPAN above so they rarely need to. */
[data-testid="stMain"] [data-testid="stButton"] button,
[data-testid="stMain"] [data-testid="stDownloadButton"] button { min-height: 38px; }

/* The overflow menu is an icon, so it does not need room for a label. */
[data-testid="stPopoverButton"]:has([data-testid="stIconMaterial"]) { min-width: 0; }

/* --- sidebar: identity, then navigation, then status -------------------- */
[data-testid="stSidebarUserContent"] { padding-top: 4px; }
[data-testid="stSidebar"] .kdb-measure { max-width: none; font-size: 12px; }
[data-testid="stSidebar"] h5 {
  font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
  text-transform: uppercase; opacity: 0.55; padding: 0; margin: 0 0 2px 0;
}
/* The status badges are a group, not four unrelated paragraphs 40px apart. */
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"]
  [data-testid="stVerticalBlock"] { gap: 0.45rem; }

/* The Result page is a detail view of one alert, reached from the Monitor's
   View button. It stays registered so `st.switch_page` can reach it, but a
   leaf does not belong beside the sections in primary navigation — visiting
   it directly only ever showed an empty state. */
[data-testid="stSidebarNav"] a[href$="/result"] { display: none; }
</style>
"""


def inject_css() -> None:
    """Apply the type scale and rhythm. Call once, from the app shell."""
    st.markdown(_CSS, unsafe_allow_html=True)


# --- the brand ------------------------------------------------------------- #

# ``st.logo`` is the one slot above the navigation list, which is where every
# product this app is modelled on puts its identity. The alternative was what
# was there before: an ``st.title`` in the sidebar *body*, i.e. a 28px wordmark
# sitting below the nav and dominating it.
_WORDMARK = """<svg xmlns="http://www.w3.org/2000/svg" width="188" height="26" \
viewBox="0 0 188 26"><g fill="none" stroke="#3b82f6" stroke-width="1.5">\
<circle cx="13" cy="13" r="9.5"/><circle cx="13" cy="13" r="5"/></g>\
<circle cx="13" cy="13" r="1.9" fill="#3b82f6"/><path d="M13 13 L20.5 7" \
stroke="#3b82f6" stroke-width="1.5" stroke-linecap="round"/>\
<text x="31" y="18.5" font-family="Inter,'Segoe UI',system-ui,sans-serif" \
font-size="15.5" font-weight="700" fill="#dfe7ef" \
letter-spacing="-0.3">KdbMonitor</text></svg>"""


def brand() -> None:
    """Put the product mark above the navigation list."""
    encoded = base64.b64encode(_WORDMARK.encode("utf-8")).decode("ascii")
    st.logo(f"data:image/svg+xml;base64,{encoded}", size="medium")
