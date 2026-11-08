"""Shared plotting style and map helpers (matplotlib only).

Colour roles
------------
* categorical  -> one fixed colour per operator (``config.OPERATOR_COLORS``)
* sequential   -> single blue ramp, light = low
* diverging    -> red <-> grey <-> blue (grey = balanced / zero)
* not significant -> neutral grey + hatching, so significance never relies on
  colour alone
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap, ListedColormap
from matplotlib.patches import Patch

from . import config

INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"
NEUTRAL = "#f0efec"
NODATA = "#d9d8d2"

SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SEQ_CMAP = LinearSegmentedColormap.from_list("seq_blue", SEQ_BLUE)
DIV_CMAP = LinearSegmentedColormap.from_list(
    "div_red_blue", ["#a52a2a", "#e34948", "#f3a6a0", NEUTRAL, "#9ec5f4", "#2a78d6", "#104281"])
# legends sit outside the map frame (to the right) so they never hide zones
LEGEND_OUT = dict(loc="lower left", bbox_to_anchor=(0.97, 0.0), borderaxespad=0.0)
LISA_COLORS = {"High-High": "#d03b3b", "Low-Low": "#2a78d6", "Low-High": "#9ec5f4",
               "High-Low": "#f3a6a0", "Not significant": NEUTRAL}


def set_style() -> None:
    mpl.rcParams.update({
        "figure.dpi": 110, "savefig.dpi": 200, "savefig.bbox": "tight",
        "figure.facecolor": "white", "axes.facecolor": "white",
        "font.family": "sans-serif", "font.sans-serif": ["Segoe UI", "DejaVu Sans", "Arial"],
        "font.size": 10, "axes.titlesize": 11, "axes.titleweight": "bold", "axes.labelsize": 10,
        "axes.edgecolor": "#c3c2b7", "axes.labelcolor": INK_2, "text.color": INK,
        "xtick.color": INK_2, "ytick.color": INK_2,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
        "axes.spines.top": False, "axes.spines.right": False,
        "legend.frameon": False, "lines.linewidth": 2,
    })


def op_color(op: str) -> str:
    return config.OPERATOR_COLORS[op]


def save(fig, name: str, folder: Path = config.FIG_DIR) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.png"
    fig.savefig(path)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Maps
# ---------------------------------------------------------------------------
def _basemap(ax) -> None:
    """Light grey city outline; no web tiles so the output is reproducible offline."""
    from .zones import load_zones
    load_zones().boundary.plot(ax=ax, color="#ffffff", linewidth=0.4, zorder=3)
    gpd.GeoSeries([load_zones().union_all()], crs=config.CRS_METRIC).boundary.plot(
        ax=ax, color=INK_2, linewidth=0.8, zorder=4)


def _finish_map(ax, title: str | None = None) -> None:
    ax.set_axis_off()
    if title:
        ax.set_title(title, loc="left")
    # 2 km scale bar in the lower-left corner
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    sx, sy = x0 + 0.05 * (x1 - x0), y0 + 0.04 * (y1 - y0)
    ax.plot([sx, sx + 2000], [sy, sy], color=INK, lw=2, solid_capstyle="butt", zorder=6)
    ax.text(sx + 1000, sy + 0.015 * (y1 - y0), "2 km", ha="center", va="bottom", fontsize=8, color=INK)
    ax.annotate("N", xy=(0.95, 0.95), xycoords="axes fraction", ha="center", fontsize=10,
                fontweight="bold", color=INK)
    ax.annotate("", xy=(0.95, 0.94), xytext=(0.95, 0.86), xycoords="axes fraction",
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.2))


def choropleth(ax, gdf: gpd.GeoDataFrame, column: str, *, bins=None, cmap=SEQ_CMAP, diverging=False,
               vlim=None, significant: str | None = None, title=None, legend_title="",
               fmt="{:.0f}", show_legend=True, label_top: int = 0) -> None:
    """Choropleth with class breaks, grey no-data and hatched non-significant zones.

    Parameters
    ----------
    bins : list of class breaks (len = n_classes + 1). If None, a continuous
        colour bar is used with limits ``vlim``.
    significant : name of a boolean column; zones with False are drawn in
        neutral grey with diagonal hatching ("not significant at 5% FDR").
    label_top : annotate the zone codes of the ``label_top`` largest |values|.
    """
    gdf = gdf.copy()
    nodata = gdf[column].isna()
    gdf[nodata].plot(ax=ax, color=NODATA, edgecolor="none", zorder=1)
    data = gdf[~nodata]
    if significant is not None:
        ns = data[~data[significant].astype(bool)]
        data = data[data[significant].astype(bool)]
    if bins is not None:
        n = len(bins) - 1
        colors = cmap(np.linspace(0, 1, n))
        norm = BoundaryNorm(bins, n)
        data.plot(ax=ax, column=column, cmap=ListedColormap(colors), norm=norm, edgecolor="none", zorder=2)
        if show_legend:
            handles = [Patch(facecolor=colors[i], label=f"{fmt.format(bins[i])} – {fmt.format(bins[i + 1])}")
                       for i in range(n)]
            if significant is not None:
                handles.append(Patch(facecolor=NEUTRAL, hatch="////", edgecolor=MUTED, label="Not significant"))
            handles.append(Patch(facecolor=NODATA, label="No / too few data"))
            ax.legend(handles=handles, title=legend_title, **LEGEND_OUT, fontsize=7,
                      title_fontsize=8, handlelength=1.2)
    else:
        vmin, vmax = vlim if vlim else (data[column].min(), data[column].max())
        if diverging:
            m = max(abs(vmin), abs(vmax))
            vmin, vmax = -m, m
        data.plot(ax=ax, column=column, cmap=cmap, vmin=vmin, vmax=vmax, edgecolor="none", zorder=2)
        if show_legend:
            sm = mpl.cm.ScalarMappable(cmap=cmap, norm=mpl.colors.Normalize(vmin, vmax))
            cb = plt.colorbar(sm, ax=ax, fraction=0.035, pad=0.01)
            cb.set_label(legend_title, fontsize=8)
            cb.ax.tick_params(labelsize=7)
            cb.outline.set_visible(False)
            extra = []
            if significant is not None:
                extra.append(Patch(facecolor=NEUTRAL, hatch="////", edgecolor=MUTED, label="Not significant (5% FDR)"))
            extra.append(Patch(facecolor=NODATA, label="No / too few data"))
            ax.legend(handles=extra, **LEGEND_OUT, fontsize=7)
    if significant is not None and len(ns):
        ns.plot(ax=ax, facecolor=NEUTRAL, edgecolor=MUTED, hatch="////", linewidth=0, zorder=2)
    _basemap(ax)
    if label_top:
        top = gdf[~nodata].reindex(gdf[~nodata][column].abs().sort_values(ascending=False).index[:label_top])
        for _, r in top.iterrows():
            p = r.geometry.representative_point()
            ax.annotate(r["zone"], (p.x, p.y), ha="center", va="center", fontsize=6.5, color=INK,
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.75), zorder=7)
    _finish_map(ax, title)


def categorical_map(ax, gdf: gpd.GeoDataFrame, column: str, colors: dict, title=None, legend_title="") -> None:
    for cat, col in colors.items():
        sub = gdf[gdf[column] == cat]
        if len(sub):
            sub.plot(ax=ax, color=col, edgecolor="none", zorder=2)
    gdf[gdf[column].isna()].plot(ax=ax, color=NODATA, edgecolor="none", zorder=1)
    _basemap(ax)
    counts = gdf[column].value_counts()
    handles = [Patch(facecolor=c, label=f"{k} ({counts.get(k, 0)})") for k, c in colors.items() if counts.get(k, 0)]
    if gdf[column].isna().any():
        handles.append(Patch(facecolor=NODATA, label="No data"))
    ax.legend(handles=handles, title=legend_title, **LEGEND_OUT, fontsize=7, title_fontsize=8)
    _finish_map(ax, title)


def map_grid(n: int, ncols: int = 3, size: float = 4.6):
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(size * 1.35 * ncols, size * 1.05 * nrows))
    axes = np.atleast_1d(axes).ravel()
    for ax in axes[n:]:
        ax.set_visible(False)
    return fig, axes[:n]
