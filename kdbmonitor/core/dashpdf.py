"""Dashboard -> A4 PDF.

Renders from the dataset results already on screen — never a fresh query — so the
downloaded page is the state the user was looking at, not a near-miss taken a
moment later.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime

from kdbmonitor.core import theme
from kdbmonitor.core.dashboard_models import Dashboard, Row
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.render_mpl import draw
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


def paginate(rows: list[Row]) -> list[list[tuple[Row, float]]]:
    """Split rows into pages of ``(row, y_top)``, y measured in inches from the
    top of the page.

    A row taller than a whole page is placed anyway rather than looping forever —
    it simply overflows its page.
    """
    pages: list[list[tuple[Row, float]]] = []
    page: list[tuple[Row, float]] = []
    y = MARGIN + HEADER_H_FIRST
    limit = PAGE_H - MARGIN - FOOTER_H

    for row in rows:
        if page and y + row.height_in > limit:
            pages.append(page)
            page = []
            y = MARGIN + HEADER_H_CONT
        page.append((row, y))
        y += row.height_in + GUTTER

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


def _axes_rect(x: float, y_top: float, w_in: float, h_in: float,
               widget) -> tuple[list[float], float]:
    """The widget's axes rect, plus the title height reserved above it.

    KPIs render their own label, so they take the whole slot.
    """
    title_h = TITLE_H if (widget.title and widget.type != "kpi") else 0.0
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
                 rt: ResolvedTime, as_of: datetime, page_no: int, total: int):
    """Build one A4 figure. The caller owns closing it."""
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(PAGE_W, PAGE_H))
    fig.patch.set_facecolor(theme.SURFACE)
    _header(fig, dashboard, rt, as_of, first=page_no == 1)

    for row, y_top in page:
        widgets = row.widgets
        if not widgets:
            continue
        total_w = sum(max(w.width, 0.01) for w in widgets)
        usable = CONTENT_W - GUTTER * (len(widgets) - 1)
        x = MARGIN
        for widget in widgets:
            w_in = usable * (max(widget.width, 0.01) / total_w)
            rect, title_h = _axes_rect(x, y_top, w_in, row.height_in, widget)
            if title_h:
                fig.text(x / PAGE_W, 1 - (y_top + 0.16) / PAGE_H, widget.title,
                         fontsize=12, fontweight="bold", color=theme.INK,
                         va="center")
            ax = fig.add_axes(rect)
            draw(ax, build_plot_model(widget, results))
            x += w_in + GUTTER

    _footer(fig, as_of, page_no, total)
    return fig


def dashboard_to_pdf_bytes(dashboard: Dashboard, results: dict,
                           rt: ResolvedTime, as_of: datetime) -> bytes:
    """Render the dashboard's current state to a multi-page A4 PDF."""
    theme.apply_seaborn_theme()
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    pages = paginate(dashboard.rows) or [[]]
    buf = io.BytesIO()

    with PdfPages(buf) as pdf:
        for page_no, page in enumerate(pages, start=1):
            fig = _render_page(dashboard, page, results, rt, as_of,
                               page_no, len(pages))
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

    pages = paginate(dashboard.rows) or [[]]
    index = max(1, min(page_no, len(pages)))
    fig = _render_page(dashboard, pages[index - 1], results, rt, as_of,
                       index, len(pages))
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    return buf.getvalue()


def page_count(dashboard: Dashboard) -> int:
    return len(paginate(dashboard.rows) or [[]])


def pdf_filename(dashboard: Dashboard, as_of: datetime) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", dashboard.name.lower()).strip("_") or "dashboard"
    return f"{slug}_{as_of:%Y-%m-%d_%H%M}.pdf"
