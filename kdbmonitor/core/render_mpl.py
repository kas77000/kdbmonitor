"""PlotModel -> matplotlib axes, for the printed report.

Draws onto an ``Axes`` the caller owns so the page assembler controls layout.
Styling follows short_sell_report.py: light surface, no chart junk, values
labelled directly rather than read off an axis.
"""
from __future__ import annotations

from typing import Callable

import seaborn as sns

from kdbmonitor.core import theme
from kdbmonitor.core.plotmodel import Band, PlotModel, Reference


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

# A table is bounded by its width as much as by its height, and matplotlib
# neither wraps nor clips cell text: a column given less room than its text
# needs does not shrink, it runs into its neighbour. So the width has to be
# spent as deliberately as the height, which means knowing what a character
# costs. These are the average glyph widths of the table font in ems, measured
# off the renderer, so the type size can be decided before anything is drawn.
# Set above the *average* glyph on purpose: a column of '09:15:03.221' is all
# wide glyphs, and sizing it by the average leaves it a hair short.
TABLE_CHAR_EM = 0.55        # body cells, regular weight
TABLE_HEAD_EM = 0.62        # header row — bold, and so wider per character
COLUMN_PAD = 2              # breathing room, in characters, on every column


def _axes_height_in(ax) -> float:
    """The axes' drawn height in inches — what the row budget is spent from."""
    figure = getattr(ax, "figure", None)
    if figure is None:
        return 0.0
    return ax.get_position().height * figure.get_figheight()


def _axes_width_in(ax) -> float:
    """The axes' drawn width in inches — what a table's columns are spent from."""
    figure = getattr(ax, "figure", None)
    if figure is None:
        return 0.0
    return ax.get_position().width * figure.get_figwidth()


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


def _column_chars(columns: list[str], rows: list[list[str]]) -> list[tuple[int, int]]:
    """Per column, (characters in the header, characters in its longest cell)."""
    return [(len(str(c)),
             max([0] + [len(str(r[i])) for r in rows if i < len(r)]))
            for i, c in enumerate(columns)]


def _column_widths(columns: list[str], rows: list[list[str]]) -> list[float]:
    """Share of the width per column, proportional to the longest text in it.

    Equal columns make a nine-column table overlap: 'RELIANCE.IN' spills into
    the next cell while 'Side' leaves half its box empty.
    """
    widest = [COLUMN_PAD + max(head, body)
              for head, body in _column_chars(columns, rows)]
    total = sum(widest) or 1
    return [w / total for w in widest]


def table_fit_font(columns: list[str], rows: list[list[str]],
                   width_in: float) -> float:
    """The largest type size at which every column's text fits the width it gets.

    Width is handed out in proportion to a column's longest text, so the binding
    column is whichever one's text is longest *relative to its share* — not
    simply the widest one. The header is weighed against the body separately
    because it is bold, and a short header over long values can still overrun
    the share those values earned it.

    Pure, and needs no figure: this is what decides whether a dashboard can be
    printed portrait at all, and that has to be answerable before a page exists.
    """
    if width_in <= 0 or not columns:
        return TABLE_FONT
    fits = [share * width_in * 72 / need
            for share, (head, body) in zip(_column_widths(columns, rows),
                                           _column_chars(columns, rows))
            if (need := max(head * TABLE_HEAD_EM, body * TABLE_CHAR_EM)) > 0]
    return min(fits) if fits else TABLE_FONT


# The ellipsis is 0.75 em — wider than the average character it replaces — so it
# is charged two characters against the column's budget. Billed as one, a value
# cut to fit came back out a shade wider than the box it was cut for.
ELLIPSIS = "…"
ELLIPSIS_CHARS = 2


def _clip(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters, saying so where it was cut.

    The mark of the cut is part of the budget, never on top of it: a column too
    narrow to hold even that prints what it can and stays inside its box, since
    a column one character wide has no room to explain itself anyway.
    """
    if len(text) <= limit:
        return text
    keep = limit - ELLIPSIS_CHARS
    return text[:keep] + ELLIPSIS if keep >= 1 else text[:max(limit, 1)]


def _trimmed(columns: list[str], rows: list[list[str]], shares: list[float],
             width_in: float) -> tuple[list[str], list[list[str]]]:
    """Text cut to what each column can hold at the smallest legible size.

    The last resort, reached only once the paper and the type size have both
    been spent — a table so wide that even a turned page at 7pt cannot hold it.
    A value cut short carries an ellipsis and so admits it was cut; a value left
    whole just collides with the next column and admits nothing.

    Trimming against the shares the *untrimmed* text earned, rather than
    recomputing them after, keeps the guarantee: shorter text in the same box
    still fits.
    """
    def limit(share: float, em: float) -> int:
        return max(int(share * width_in * 72 / (TABLE_MIN_FONT * em)), 1)

    heads = [limit(s, TABLE_HEAD_EM) for s in shares]
    cells = [limit(s, TABLE_CHAR_EM) for s in shares]
    return ([_clip(str(c), heads[i]) for i, c in enumerate(columns) if i < len(heads)],
            [[_clip(str(v), cells[i]) for i, v in enumerate(r) if i < len(cells)]
             for r in rows])


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

    # Height has had its say; now width has hers. The size settled on above fits
    # the rows into the slot, but says nothing about whether the columns fit
    # across it — so take the smaller of the two, and where even the smallest
    # legible size will not span the columns, cut the text rather than let it
    # collide.
    width_in = _axes_width_in(ax)
    columns, shares = pm.columns, _column_widths(pm.columns, rows)
    fit = table_fit_font(pm.columns, rows, width_in)
    if width_in > 0 and fit < TABLE_MIN_FONT:
        columns, rows = _trimmed(pm.columns, rows, shares, width_in)
        font_size = TABLE_MIN_FONT
    elif width_in > 0:
        font_size = max(min(font_size, fit), TABLE_MIN_FONT)

    # bbox makes the table fill its axes exactly (no internal gap), less any
    # strip kept for the note.
    table = ax.table(cellText=rows, colLabels=columns, cellLoc="right",
                     colLoc="right", colWidths=shares, bbox=bbox)
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


# How many off-scale notices stack before they'd run off the top of the axes —
# not a limit enforced anywhere, just the spacing between them.
_OFF_SCALE_STEP = 0.055


def _mid(ax, axis, v0, v1) -> float:
    """The midpoint between two values on ``axis`` ('x' or 'y'), in whatever
    units it has settled on — categorical, numeric or datetime.

    Going through the axis' own unit converter rather than averaging ``v0``
    and ``v1`` directly means a categorical label or a Timestamp locates
    exactly the same way a plain float does, without a second code path per
    axis kind.
    """
    conv = ax.xaxis if axis == "x" else ax.yaxis
    return (float(conv.convert_units(v0)) + float(conv.convert_units(v1))) / 2


def _band_span(xs: list, band: Band, kind: str):
    """The two positions a band's ends resolve to along the category axis, or
    None if either is not on this axis after all.

    Matched by text against the series' own x list — the same way the
    resolver matched them against the data — so a category, a date and a
    number all locate the same way. A bar chart's categories are not plotted
    at their own values but at integer positions, so a band there spans
    whole categories rather than the gap between two labels.
    """
    str_xs = [str(v) for v in xs]
    try:
        i0 = str_xs.index(str(band.start))
        i1 = str_xs.index(str(band.end))
    except ValueError:
        return None
    if kind == "bar":
        return i0 - 0.5, i1 + 0.5
    return xs[i0], xs[i1]


def _draw_band(ax, band: Band, xs: list, kind: str, horizontal: bool) -> None:
    span = _band_span(xs, band, kind)
    if span is None:
        return
    v0, v1 = span
    # Below the series' own zorder (2 for a line, 3 for a bar or scatter point)
    # so the shading frames the data instead of sitting on top of it. A
    # horizontal bar has swapped its category axis onto y, so the span follows.
    if horizontal:
        ax.axhspan(v0, v1, color=theme.MUTED, alpha=0.12, zorder=0.5,
                  linewidth=0)
    else:
        ax.axvspan(v0, v1, color=theme.MUTED, alpha=0.12, zorder=0.5,
                  linewidth=0)
    if not band.label:
        return
    mid = _mid(ax, "y" if horizontal else "x", v0, v1)
    if horizontal:
        ax.text(0.97, mid, band.label, transform=ax.get_yaxis_transform(),
                ha="right", va="center", fontsize=8, color=theme.MUTED)
    else:
        ax.text(mid, 0.97, band.label, transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=8, color=theme.MUTED)


def _draw_reference(ax, ref: Reference, xs: list, lim: tuple, kind: str,
                    horizontal: bool):
    """Draw one reference against the value-axis range already settled on.

    Returns the reference's label if a constant fell outside that range —
    drawing it would force the axis to widen and flatten the real series, but
    dropping it without a word leaves the reader wondering where the line they
    asked for went, so the caller turns this into a small note instead. A
    horizontal bar reads its values off x rather than y, so the line, and the
    range it is checked against, are drawn on whichever axis actually carries
    the values.
    """
    lo, hi = lim
    line = ax.axvline if horizontal else ax.axhline
    if ref.value is not None:
        if not (lo <= ref.value <= hi):
            return ref.label or "reference"
        line(ref.value, color=theme.MUTED, linestyle="--", linewidth=1.2,
            zorder=1)
        if ref.label:
            if horizontal:
                ax.text(ref.value, 0.995, f" {ref.label}",
                        transform=ax.get_xaxis_transform(), ha="left",
                        va="top", fontsize=8, color=theme.MUTED)
            else:
                ax.text(0.995, ref.value, f" {ref.label}",
                        transform=ax.get_yaxis_transform(), ha="right",
                        va="bottom", fontsize=8, color=theme.MUTED)
        return None

    # A curve reference plots against the same category positions the chart
    # itself uses — a bar chart's categories are integer slots, not the
    # category values, so the reference follows suit there. A length mismatch
    # against the series (a stale reference left over from a shorter dataset)
    # is truncated to whichever is shorter rather than raising.
    positions = list(range(len(xs))) if kind == "bar" else xs
    n = min(len(positions), len(ref.values))
    if n == 0:
        return None
    cats, values = positions[:n], ref.values[:n]
    plot_x, plot_y = (values, cats) if horizontal else (cats, values)
    ax.plot(plot_x, plot_y, color=theme.MUTED, linestyle="--", linewidth=1.4,
            zorder=1)
    if ref.label:
        ax.annotate(ref.label, (plot_x[-1], plot_y[-1]), xytext=(4, 0),
                    textcoords="offset points", fontsize=8,
                    color=theme.MUTED, va="center")
    return None


def _draw_extras(ax, pm: PlotModel) -> None:
    """References and bands, drawn against the range the data already settled
    on — never the other way round.

    A mistyped threshold sitting far outside the real series would otherwise
    widen the axis until that series flattens into a line along the bottom of
    the chart, which is worse than not drawing the reference at all. So the
    range is captured before anything extra goes on, and restored after,
    whatever the extras did to it in between.
    """
    if not pm.references and not pm.bands:
        return
    horizontal = pm.kind == "bar" and pm.orientation == "h"
    value_lim = ax.get_xlim() if horizontal else ax.get_ylim()
    xs = list(pm.series[0].x) if pm.series else []

    for band in pm.bands:
        _draw_band(ax, band, xs, pm.kind, horizontal)

    off_scale = [note for ref in pm.references
                 if (note := _draw_reference(ax, ref, xs, value_lim, pm.kind,
                                             horizontal))]
    for i, label in enumerate(off_scale):
        ax.text(0.99, 0.99 - i * _OFF_SCALE_STEP, f"{label} — off scale",
                transform=ax.transAxes, ha="right", va="top", fontsize=8,
                color=theme.MUTED, style="italic")

    if horizontal:
        ax.set_xlim(value_lim)
    else:
        ax.set_ylim(value_lim)


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

    _draw_extras(ax, pm)


def _line(ax, pm: PlotModel) -> None:
    _bare(ax)
    for s in pm.series:
        ax.plot(s.x, s.y, color=s.color, label=s.label, marker="o",
                markersize=3.5, linewidth=1.8)
    ax.grid(axis="y", color=theme.GRID, linewidth=0.8)
    if len(pm.series) > 1:
        ax.legend(frameon=False, fontsize=9)

    _draw_extras(ax, pm)


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

    _draw_extras(ax, pm)


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
