"""Dashboard -> A4 PDF.

Renders from the dataset results already on screen — never a fresh query — so the
downloaded page is the state the user was looking at, not a near-miss taken a
moment later.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Optional

from kdbmonitor.core import theme
from kdbmonitor.core.dashboard_models import Dashboard, Row
from kdbmonitor.core.plotmodel import build_plot_model, slice_table
from kdbmonitor.core.render_mpl import draw, table_capacity
from kdbmonitor.core.timectx import ResolvedTime

PAGE_W, PAGE_H = 8.27, 11.69       # A4 portrait, inches
MARGIN = 0.6
HEADER_H_FIRST = 1.05              # title band on page 1
# Continuation pages carry no header at all — just breathing room above the
# first row. A repeated "<name> (continued)" band says nothing the footer's
# page number does not, and costs a third of an inch of every later page.
HEADER_H_CONT = 0.15
FOOTER_H = 0.45
GUTTER = 0.28                      # between rows and between widgets

CONTENT_H_FIRST = PAGE_H - MARGIN * 2 - HEADER_H_FIRST - FOOTER_H
CONTENT_H_CONT = PAGE_H - MARGIN * 2 - HEADER_H_CONT - FOOTER_H
CONTENT_W = PAGE_W - MARGIN * 2


@dataclass(frozen=True)
class Part:
    """One printed instance of a dashboard row.

    A table with more rows than its slot holds prints over several parts. Part 0
    carries the row as laid out; every later part is the table continuing, with
    the row's other widgets left blank because they were printed once already.
    """
    row: Row
    part: int = 0                                  # 0 = the row itself
    slices: dict = field(default_factory=dict)     # widget position -> (start, count)
    capacity: dict = field(default_factory=dict)   # widget position -> rows per part
    height_in: float = 0.0                         # taller than the row when merged
    spans: int = 1                                 # parts merged into this block

    def sized(self) -> "Part":
        return self if self.height_in else replace(self, height_in=self.row.height_in)


def _joins(first: Part, second: Part) -> bool:
    """Whether ``second`` can simply continue ``first`` in one taller block.

    Two chunks of the same table, back to back on a page, should read as one
    table — not as the same header printed twice with a gap between. Only safe
    where growing the block cannot distort a neighbour: either the earlier chunk
    is already a table-only continuation, or every widget in the row is a table
    that is flowing.

    ``first`` may itself be several parts already merged, so the next part to
    follow it is ``part + spans`` — comparing against ``part + 1`` stopped a
    third chunk joining a block of two and printed its header again mid-page.
    """
    if first.row is not second.row or second.part != first.part + first.spans:
        return False
    if set(first.slices) != set(second.slices):
        return False
    return bool(first.part) or len(first.slices) == len(first.row.widgets)


def _join(first: Part, second: Part) -> Part:
    """Two consecutive chunks of the same table as a single, taller block."""
    return replace(
        first,
        slices={pos: (start, count + second.slices[pos][1])
                for pos, (start, count) in first.slices.items()},
        capacity={pos: cap + second.capacity[pos]
                  for pos, cap in first.capacity.items()},
        height_in=first.height_in + GUTTER + second.height_in,
        spans=first.spans + 1)


# A table long enough to need more parts than this stops being a report and
# starts being a data dump; the last part then says how many rows it showed
# rather than printing hundreds of pages nobody asked for.
MAX_TABLE_PARTS = 50


def _model(widget, results: dict, cache: Optional[dict]) -> object:
    """The widget's plot model, built once per render.

    Resolving a widget formats every row of its dataset, so a table continued
    over fifty parts would otherwise be formatted fifty-one times.
    """
    if cache is None:
        return build_plot_model(widget, results)
    key = id(widget)
    if key not in cache:
        cache[key] = build_plot_model(widget, results)
    return cache[key]


def _table_rows(widget, results: dict, cache: Optional[dict] = None) -> int:
    """How many rows a table widget would print, 0 if it is not a live table."""
    if widget.type != "table" or not results:
        return 0
    try:
        return len(_model(widget, results, cache).rows)
    except Exception:      # noqa: BLE001 - an unreadable widget just does not flow
        return 0


def split_rows(rows: list[Row], results: Optional[dict] = None,
               cache: Optional[dict] = None) -> list[Part]:
    """Rows as they will actually print, tables split over as many parts as
    they need. Without results nothing can be measured, so each row is one part.
    """
    out: list[Part] = []
    for row in rows:
        slices, capacity = {}, {}
        for position, widget in enumerate(row.widgets):
            total = _table_rows(widget, results or {}, cache)
            per_part = table_capacity(_widget_height_in(row, widget))
            if total and per_part and total > per_part:
                slices[position] = total
                capacity[position] = per_part

        parts = 1
        for position, total in slices.items():
            needed = -(-total // capacity[position])            # ceil
            parts = max(parts, min(needed, MAX_TABLE_PARTS))

        for part in range(parts):
            taken = {p: (part * capacity[p], capacity[p]) for p in slices
                     if part * capacity[p] < slices[p]}
            out.append(Part(row=row, part=part, slices=taken,
                            capacity={p: capacity[p] for p in taken}).sized())
    return out


def paginate(rows: list[Row], results: Optional[dict] = None,
             cache: Optional[dict] = None) -> list[list[tuple[Part, float]]]:
    """Split rows into pages of ``(part, y_top)``, y measured in inches from the
    top of the page.

    A row taller than a whole page is placed anyway rather than looping forever —
    it simply overflows its page.
    """
    pages: list[list[tuple[Part, float]]] = []
    page: list[tuple[Part, float]] = []
    y = MARGIN + HEADER_H_FIRST
    limit = PAGE_H - MARGIN - FOOTER_H

    for part in split_rows(rows, results, cache):
        if page and y + part.height_in > limit:
            pages.append(page)
            page = []
            y = MARGIN + HEADER_H_CONT
        elif page and _joins(page[-1][0], part):
            # Same table, still on this page: grow the block rather than start
            # a second one under its own repeated header.
            page[-1] = (_join(page[-1][0], part), page[-1][1])
            y += part.height_in + GUTTER
            continue
        page.append((part, y))
        y += part.height_in + GUTTER

    if page:
        pages.append(page)
    return pages


@dataclass(frozen=True)
class RowPlacement:
    """Where a row lands on the printed page."""
    index: int          # position in dashboard.rows
    page: int           # 1-based page number
    y_top: float        # inches from the top of that page
    starts_page: bool   # first row on its page
    free_after: float   # inches still free on the page below this row


def page_limit(page_no: int) -> float:
    """Usable height on a page — page 1 gives up more to the title band."""
    header = HEADER_H_FIRST if page_no == 1 else HEADER_H_CONT
    return PAGE_H - MARGIN * 2 - header - FOOTER_H


def plan_rows(rows: list[Row]) -> list[RowPlacement]:
    """Which page each row prints on, so the editor can show it while you build.

    Same pagination the PDF uses — derived from :func:`paginate` rather than
    reimplemented, so the editor can never disagree with the output.
    """
    placements: list[RowPlacement] = []
    index = 0
    pages = paginate(rows)
    for page_no, page in enumerate(pages, start=1):
        bottom = PAGE_H - MARGIN - FOOTER_H
        for position, (row, y_top) in enumerate(page):
            placements.append(RowPlacement(
                index=index, page=page_no, y_top=y_top,
                starts_page=position == 0,
                free_after=max(bottom - (y_top + row.height_in), 0.0)))
            index += 1
    return placements


def _rect(x_in: float, y_top_in: float, w_in: float, h_in: float) -> list[float]:
    """Inches from the top-left -> matplotlib figure coordinates."""
    return [x_in / PAGE_W, 1.0 - (y_top_in + h_in) / PAGE_H,
            w_in / PAGE_W, h_in / PAGE_H]


TITLE_H = 0.30      # inches reserved above a widget for its title

# Padding (left, bottom, right, top) in inches between a widget's slot and its
# axes. Charts need a gutter for tick labels, which are drawn *outside* the
# axes and would otherwise be clipped at the page margin.
_INSET_NONE = (0.0, 0.0, 0.0, 0.0)
_INSET_CHART = (0.72, 0.30, 0.12, 0.06)
_INSET_PIE = (0.34, 0.10, 0.34, 0.04)

_INSETS = {"kpi": _INSET_NONE, "table": _INSET_NONE, "text": _INSET_NONE,
           "error": _INSET_NONE, "pie": _INSET_PIE, "heatmap": (0.72, 0.30, 0.12, 0.06)}


def _title_height(widget) -> float:
    """KPIs render their own label, so they take the whole slot."""
    return TITLE_H if (widget.title and widget.type != "kpi") else 0.0


def _widget_height_in(row: Row, widget) -> float:
    """The drawn height of a widget's axes — what a table's rows are spent on."""
    _, bottom, _, top = _INSETS.get(widget.type, _INSET_CHART)
    return max(row.height_in - _title_height(widget) - top - bottom, 0.15)


def _axes_rect(x: float, y_top: float, w_in: float, h_in: float,
               widget) -> tuple[list[float], float]:
    """The widget's axes rect, plus the title height reserved above it."""
    title_h = _title_height(widget)
    left, bottom, right, top = _INSETS.get(widget.type, _INSET_CHART)
    return (_rect(x + left, y_top + title_h + top,
                  max(w_in - left - right, 0.15),
                  max(h_in - title_h - top - bottom, 0.15)),
            title_h)


def report_period(rt: ResolvedTime, as_of: datetime) -> str:
    """What the report covers: a date range, or the moment it was taken.

    Just the dates. A single timestamp reads as a snapshot and an arrow reads as
    a range, so the words "real-time" and "historical" add nothing to a printed
    page — the reader wants to know *when*, not which server answered.
    """
    if rt.mode == "historical" and rt.start and rt.end:
        if rt.start == rt.end:
            return f"{rt.start:%Y-%m-%d}"
        return f"{rt.start:%Y-%m-%d} → {rt.end:%Y-%m-%d}"
    return f"{as_of:%Y-%m-%d %H:%M}"


def _header(fig, dashboard: Dashboard, rt: ResolvedTime, as_of: datetime,
            first: bool) -> None:
    """Title band — page 1 only.

    Continuation pages get no header: the report is one document, and repeating
    its name on every page is filler. The footer's page number already says
    where you are.
    """
    if not first:
        return

    import matplotlib.pyplot as plt

    fig.text(MARGIN / PAGE_W, 1 - 0.42 / PAGE_H, dashboard.name,
             fontsize=22, fontweight="bold", color=theme.INK, va="top")
    fig.text(MARGIN / PAGE_W, 1 - 0.78 / PAGE_H,
             report_period(rt, as_of), fontsize=11, color=theme.INK2, va="top")

    rule_y = 1 - (MARGIN + HEADER_H_FIRST - 0.18) / PAGE_H
    fig.add_artist(plt.Line2D([MARGIN / PAGE_W, 1 - MARGIN / PAGE_W],
                              [rule_y, rule_y], color=theme.GRID, lw=1,
                              transform=fig.transFigure))


def _footer(fig, as_of: datetime, page_no: int, total: int) -> None:
    """Page number only. The header already dates the report, and the tool that
    produced it is not something the reader needs on every page."""
    import matplotlib.pyplot as plt

    y = (MARGIN + FOOTER_H - 0.14) / PAGE_H
    fig.add_artist(plt.Line2D([MARGIN / PAGE_W, 1 - MARGIN / PAGE_W], [y, y],
                              color=theme.GRID, lw=1, transform=fig.transFigure))
    fig.text(1 - MARGIN / PAGE_W, y - 0.2 / PAGE_H, f"{page_no} / {total}",
             fontsize=8.5, color=theme.MUTED, va="top", ha="right")


def _render_page(dashboard: Dashboard, page: list, results: dict,
                 rt: ResolvedTime, as_of: datetime, page_no: int, total: int,
                 cache: Optional[dict] = None):
    """Build one A4 figure. The caller owns closing it."""
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(PAGE_W, PAGE_H))
    fig.patch.set_facecolor(theme.SURFACE)
    _header(fig, dashboard, rt, as_of, first=page_no == 1)

    for part, y_top in page:
        widgets = part.row.widgets
        if not widgets:
            continue
        total_w = sum(max(w.width, 0.01) for w in widgets)
        usable = CONTENT_W - GUTTER * (len(widgets) - 1)
        x = MARGIN
        for position, widget in enumerate(widgets):
            w_in = usable * (max(widget.width, 0.01) / total_w)
            # The geometry is the row's whatever the part: a continuing table
            # keeps its column, and the widgets already printed leave a gap
            # rather than shuffling everything left.
            carried = position in part.slices
            if part.part and not carried:
                x += w_in + GUTTER
                continue

            rect, title_h = _axes_rect(x, y_top, w_in, part.height_in, widget)
            # A widget is titled where it starts and nowhere else: the pages a
            # long table runs onto carry no heading at all. The space stays
            # reserved so every part lays out to the same capacity and the type
            # does not grow on the continuation pages.
            if title_h and not part.part:
                fig.text(x / PAGE_W, 1 - (y_top + 0.16) / PAGE_H, widget.title,
                         fontsize=12, fontweight="bold", color=theme.INK,
                         va="center")
            pm = _model(widget, results, cache)
            if carried:
                start, count = part.slices[position]
                pm = slice_table(pm, start, count, part.capacity[position])
            ax = fig.add_axes(rect)
            draw(ax, pm)
            x += w_in + GUTTER

    _footer(fig, as_of, page_no, total)
    return fig


def dashboard_to_pdf_bytes(dashboard: Dashboard, results: dict,
                           rt: ResolvedTime, as_of: datetime) -> bytes:
    """Render the dashboard's current state to a multi-page A4 PDF."""
    theme.apply_seaborn_theme()
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    cache: dict = {}
    pages = paginate(dashboard.rows, results, cache) or [[]]
    buf = io.BytesIO()

    with PdfPages(buf) as pdf:
        for page_no, page in enumerate(pages, start=1):
            fig = _render_page(dashboard, page, results, rt, as_of,
                               page_no, len(pages), cache)
            pdf.savefig(fig)
            plt.close(fig)

    return buf.getvalue()


def dashboard_page_png_bytes(dashboard: Dashboard, results: dict,
                             rt: ResolvedTime, as_of: datetime,
                             page_no: int = 1, dpi: int = 110) -> bytes:
    """One page as a PNG, for the on-screen 'Preview PDF' — the real printed
    page, not an approximation of it."""
    theme.apply_seaborn_theme()
    import matplotlib.pyplot as plt

    cache: dict = {}
    pages = paginate(dashboard.rows, results, cache) or [[]]
    index = max(1, min(page_no, len(pages)))
    fig = _render_page(dashboard, pages[index - 1], results, rt, as_of,
                       index, len(pages), cache)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    return buf.getvalue()


def page_count(dashboard: Dashboard, results: Optional[dict] = None) -> int:
    """How many pages this dashboard prints on.

    Pass the results to count truthfully: a table longer than its row adds
    pages, and only the data says how many.
    """
    return len(paginate(dashboard.rows, results) or [[]])


def pdf_filename(dashboard: Dashboard, as_of: datetime) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", dashboard.name.lower()).strip("_") or "dashboard"
    return f"{slug}_{as_of:%Y-%m-%d_%H%M}.pdf"
