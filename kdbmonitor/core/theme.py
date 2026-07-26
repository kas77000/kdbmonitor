"""One palette, both renderers.

The report surface is light (a PDF is paper); the screen surface is dark to match
the app. Only the *surface* differs — data colours are shared, so a series keeps
its identity between screen and print.
"""
from __future__ import annotations

from typing import Optional

# --- report surface (PDF) — from short_sell_report.py ---------------------- #
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

# --- data colours (shared) ------------------------------------------------- #
BLUE = "#2a78d6"         # magnitude / the default series
CRITICAL = "#d03b3b"     # a concern metric
GOOD = "#006300"         # a positive outcome

CATEGORICAL = [BLUE, "#4b9f6e", "#d98a2b", "#8b5fbf", CRITICAL,
               "#2a9d9b", "#b5762a", "#5a6b8c"]

SEMANTIC = {"ink": INK, "ink2": INK2, "muted": MUTED, "good": GOOD,
            "critical": CRITICAL, "blue": BLUE}

SEQUENTIAL_CMAP = "Blues"    # heatmaps

# --- screen surface (Plotly) ---------------------------------------------- #
PLOTLY_TEMPLATE = "plotly_dark"
SCREEN_SURFACE = "rgba(0,0,0,0)"     # inherit Streamlit's background
SCREEN_INK = "#dfe7ef"
SCREEN_GRID = "#2b3542"


def color_for(i: int) -> str:
    """The i-th categorical colour, cycling."""
    return CATEGORICAL[i % len(CATEGORICAL)]


def resolve_color(name: Optional[str]) -> str:
    """A semantic name ('good'), a hex literal ('#123456'), or ink by default."""
    if not name:
        return INK
    if name.startswith("#"):
        return name
    return SEMANTIC.get(name, INK)


def apply_seaborn_theme() -> None:
    """Configure matplotlib + seaborn for the printed report surface.

    Call once before drawing a PDF; safe to call repeatedly.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    sns.set_theme(style="whitegrid", palette=CATEGORICAL)
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Segoe UI", "DejaVu Sans", "Arial"],
        "text.color": INK,
        "axes.labelcolor": INK2,
        "axes.edgecolor": BASELINE,
        "xtick.color": INK2,
        "ytick.color": INK2,
        "grid.color": GRID,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
    })
