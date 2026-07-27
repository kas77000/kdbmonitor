"""PlotModel -> matplotlib axes, for the printed report.

Draws onto an ``Axes`` the caller owns so the page assembler controls layout.
Styling follows short_sell_report.py: light surface, no chart junk, values
labelled directly rather than read off an axis.
"""
from __future__ import annotations

from typing import Callable

import seaborn as sns

from kdbmonitor.core import theme
from kdbmonitor.core.plotmodel import PlotModel


def _bare(ax, keep_bottom: bool = True) -> None:
    ax.set_facecolor(theme.SURFACE)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_visible(keep_bottom)
    if keep_bottom:
        ax.spines["bottom"].set_color(theme.BASELINE)
    ax.tick_params(length=0, labelsize=9, colors=theme.INK2)


def _value_formatter(values) -> Callable[[float], str]:
    """One format for the whole series — mixing '88.2' and '12' in a single
    chart reads as sloppy, so the decimals are decided once.

    A decimal on a four-figure number is noise, and it made a chart print
    '47,833.3' beside a table saying '47,833'. Past 100, drop it.
    """
    numbers = [float(v) for v in values] or [0.0]
    if all(v.is_integer() for v in numbers) or max(abs(v) for v in numbers) >= 100:
        return lambda v: format(v, ",.0f")
    return lambda v: format(v, ",.1f")


def _kpi(ax, pm: PlotModel) -> None:
    ax.axis("off")
    ax.text(0, 0.62, pm.value or "—", fontsize=26, fontweight="bold",
            color=pm.value_color or theme.INK, transform=ax.transAxes, va="center")
    ax.text(0, 0.24, pm.title, fontsize=10.5, color=theme.MUTED,
            transform=ax.transAxes, va="center")
    if pm.caption:
        ax.text(0, 0.05, pm.caption, fontsize=8.5, color=theme.MUTED,
                transform=ax.transAxes, va="center")


# A printed table has a fixed height, so rows and type size trade off against
# each other. Below MIN_FONT the page stops being a report and starts being a
# smudge, so the row count gives way instead: what fits is printed at a legible
# size and the rest is declared, never silently dropped.
TABLE_FONT = 10.5           # preferred size, used whenever the rows fit
TABLE_MIN_FONT = 7.0        # smallest size still worth printing
LINE_SPACING = 1.45         # cell height as a multiple of the font size
NOTE_H_IN = 0.17            # strip under the table for "showing X of Y rows"


def _axes_height_in(ax) -> float:
    """The axes' drawn height in inches — what the row budget is spent from."""
    figure = getattr(ax, "figure", None)
    if figure is None:
        return 0.0
    return ax.get_position().height * figure.get_figheight()


def table_capacity(height_in: float) -> int:
    """How many rows fit in ``height_in`` at the smallest legible size.

    No room is kept for a "showing X of Y" note: this is the capacity used when
    a table is being *continued* onto another page, where nothing is dropped
    and so there is nothing to declare.
    """
    if height_in <= 0:
        return 0
    return max(int(height_in / (TABLE_MIN_FONT * LINE_SPACING / 72)) - 1, 1)


def _font_for(height_in: float, cells: int) -> float:
    return min(TABLE_FONT,
               max(TABLE_MIN_FONT, height_in / max(cells, 1) * 72 / LINE_SPACING))


def table_layout(height_in: float, n_rows: int) -> tuple[int, float, bool]:
    """How to print ``n_rows`` in ``height_in``: (rows shown, font size, noted).

    Pure, so the trade-off can be tested without rendering anything: rows are
    dropped only once the type would fall below :data:`TABLE_MIN_FONT`, and
    dropping any at all buys a line to say so.
    """
    if height_in <= 0:                                  # unknown geometry
        return n_rows, TABLE_FONT, False
    if n_rows <= table_capacity(height_in):
        return n_rows, _font_for(height_in, n_rows + 1), False

    body = height_in - NOTE_H_IN
    shown = min(n_rows, table_capacity(body))
    return shown, _font_for(body, shown + 1), True


def _column_widths(columns: list[str], rows: list[list[str]]) -> list[float]:
    """Share of the width per column, proportional to the longest text in it.

    Equal columns make a nine-column table overlap: 'RELIANCE.IN' spills into
    the next cell while 'Side' leaves half its box empty.
    """
    pad = 2                       # breathing room, in characters, on every column
    widest = [pad + max([len(str(c))] + [len(str(r[i])) for r in rows if i < len(r)])
              for i, c in enumerate(columns)]
    total = sum(widest) or 1
    return [w / total for w in widest]


def _table(ax, pm: PlotModel) -> None:
    ax.axis("off")
    if not pm.rows:
        ax.text(0, 0.5, "no rows", fontsize=10, color=theme.MUTED,
                transform=ax.transAxes)
        return

    height_in = _axes_height_in(ax)

    if pm.row_capacity:
        # One part of a table continued across pages. Every part lays out to the
        # same capacity, so a short last part keeps the row height of the others
        # instead of stretching to fill the slot.
        rows, noted = pm.rows, False
        capacity = max(pm.row_capacity, len(rows))
        font_size = _font_for(height_in, capacity + 1)
        used = (len(rows) + 1) / (capacity + 1)
        bbox = [0, 1 - used, 1, used]
    else:
        shown, font_size, noted = table_layout(height_in, len(pm.rows))
        rows = pm.rows[:shown]
        bottom = (NOTE_H_IN / height_in) if noted and height_in > 0 else 0.0
        bbox = [0, bottom, 1, 1 - bottom]
        if noted:
            ax.text(1, bottom / 2, f"showing {shown:,} of {len(pm.rows):,} rows",
                    fontsize=8.5, color=theme.MUTED, transform=ax.transAxes,
                    ha="right", va="center")

    # bbox makes the table fill its axes exactly (no internal gap), less any
    # strip kept for the note.
    table = ax.table(cellText=rows, colLabels=pm.columns, cellLoc="right",
                     colLoc="right", colWidths=_column_widths(pm.columns, rows),
                     bbox=bbox)
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(theme.GRID)
        cell.set_linewidth(0.8)
        if col == 0:
            cell.get_text().set_ha("left")
        if row == 0:
            cell.set_facecolor(theme.INK)
            cell.set_text_props(color="white", fontweight="bold")
        else:
            cell.set_facecolor(theme.SURFACE if row % 2 else "#f4f3f0")
            colour = pm.cell_colors.get((row - 1, col))   # row 0 is the header
            if colour:
                cell.get_text().set_color(colour)
                cell.get_text().set_fontweight("bold")


def _bar(ax, pm: PlotModel) -> None:
    _bare(ax)
    n = max(len(pm.series), 1)
    for i, s in enumerate(pm.series):
        positions = [p + i * (0.8 / n) for p in range(len(s.x))]
        if pm.orientation == "h":
            ax.barh(positions, s.y, height=0.8 / n, color=s.color,
                    label=s.label, zorder=3)
        else:
            ax.bar(positions, s.y, width=0.8 / n, color=s.color,
                   label=s.label, zorder=3)
    labels = [str(v) for v in pm.series[0].x] if pm.series else []
    centres = [p + 0.4 - 0.4 / n for p in range(len(labels))]
    if pm.orientation == "h":
        ax.set_yticks(centres, labels)
        ax.set_xticks([])
    else:
        ax.set_xticks(centres, labels)

    # Label values directly. With a single series the axis ticks are hidden, so
    # without these the chart carries no readable numbers at all.
    values = [v for s in pm.series for v in s.y]
    span = max([abs(v) for v in values] or [1]) or 1
    if len(pm.series) == 1:
        s = pm.series[0]
        fmt = _value_formatter(s.y)
        for i, v in enumerate(s.y):
            pos = i + 0.4 - 0.4 / n
            if pm.orientation == "h":
                ax.text(v + span * 0.02, pos, fmt(v), va="center",
                        fontsize=9.5, color=theme.INK, fontweight="bold")
            else:
                ax.text(pos, v + span * 0.02, fmt(v), ha="center",
                        fontsize=9.5, color=theme.INK, fontweight="bold")
        if pm.orientation == "h":
            ax.set_xlim(0, span * 1.18)
        else:
            ax.set_ylim(0, span * 1.15)

    if len(pm.series) > 1:
        ax.legend(frameon=False, fontsize=9)


def _line(ax, pm: PlotModel) -> None:
    _bare(ax)
    for s in pm.series:
        ax.plot(s.x, s.y, color=s.color, label=s.label, marker="o",
                markersize=3.5, linewidth=1.8)
    ax.grid(axis="y", color=theme.GRID, linewidth=0.8)
    if len(pm.series) > 1:
        ax.legend(frameon=False, fontsize=9)


def _scatter(ax, pm: PlotModel) -> None:
    _bare(ax)
    for s in pm.series:
        ax.scatter(s.x, s.y, color=s.color, label=s.label, s=26, zorder=3)
        if pm.regression and len(s.x) > 1:
            sns.regplot(x=list(s.x), y=list(s.y), ax=ax, scatter=False,
                        color=s.color, line_kws={"linewidth": 1.4})
    ax.set_xlabel(pm.x_label, fontsize=9.5, color=theme.INK2)
    ax.set_ylabel(pm.y_label, fontsize=9.5, color=theme.INK2)
    if len(pm.series) > 1:
        ax.legend(frameon=False, fontsize=9)


def _hist(ax, pm: PlotModel) -> None:
    _bare(ax)
    for s in pm.series:
        sns.histplot(x=s.y, bins=pm.bins, ax=ax, color=s.color, kde=False)
    ax.set_xlabel(pm.x_label, fontsize=9.5, color=theme.INK2)
    ax.set_ylabel("count", fontsize=9.5, color=theme.INK2)


def _box(ax, pm: PlotModel) -> None:
    _bare(ax)
    parts = ax.boxplot([s.y for s in pm.series],
                       tick_labels=[s.label for s in pm.series],
                       patch_artist=True, medianprops={"color": theme.INK})
    for patch, s in zip(parts["boxes"], pm.series):
        patch.set_facecolor(s.color)
        patch.set_alpha(0.75)
    ax.set_ylabel(pm.y_label, fontsize=9.5, color=theme.INK2)


def _heatmap(ax, pm: PlotModel) -> None:
    sns.heatmap(pm.matrix, ax=ax, cmap=theme.SEQUENTIAL_CMAP,
                annot=pm.annotate, fmt=".0f", cbar=False,
                xticklabels=pm.col_labels, yticklabels=pm.row_labels,
                linewidths=0.5, linecolor=theme.SURFACE)
    ax.tick_params(length=0, labelsize=9, colors=theme.INK2)
    ax.set_ylabel("")
    ax.set_xlabel("")


def _pie(ax, pm: PlotModel) -> None:
    s = pm.series[0]
    ax.pie(s.y, labels=[str(v) for v in s.x],
           colors=[theme.color_for(i) for i in range(len(s.x))],
           autopct="%1.0f%%", textprops={"fontsize": 9, "color": theme.INK},
           wedgeprops={"width": 0.45} if pm.donut else None)
    ax.set_aspect("equal")


def _text(ax, pm: PlotModel) -> None:
    ax.axis("off")
    ax.text(0, 0.95, pm.text, fontsize=10.5, color=theme.INK2, wrap=True,
            transform=ax.transAxes, va="top")


def _error(ax, pm: PlotModel, message: str) -> None:
    ax.axis("off")
    # Plain ASCII on purpose: Segoe UI has no warning glyph, and a tofu box in a
    # printed report is worse than no decoration at all.
    ax.text(0.5, 0.5, f"ERROR · {message}", fontsize=10.5, color=theme.CRITICAL,
            ha="center", va="center", wrap=True, transform=ax.transAxes)


_DRAWERS = {
    "kpi": _kpi, "table": _table, "bar": _bar, "line": _line,
    "scatter": _scatter, "hist": _hist, "box": _box, "heatmap": _heatmap,
    "pie": _pie, "text": _text,
}


def draw(ax, pm: PlotModel) -> None:
    """Draw a PlotModel onto ``ax``. Never raises — a broken panel prints as a
    visible error, because a silently missing chart reads as 'nothing to report'.
    """
    if pm.kind == "error":
        _error(ax, pm, pm.error or "unknown error")
        return
    drawer = _DRAWERS.get(pm.kind)
    if drawer is None:
        _error(ax, pm, f"unknown widget type '{pm.kind}'")
        return
    try:
        drawer(ax, pm)
    except Exception as exc:      # noqa: BLE001 - never break the whole page
        ax.clear()
        _error(ax, pm, str(exc))
