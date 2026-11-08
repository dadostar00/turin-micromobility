"""Origin-destination matrices and zonal indicators with significance tests.

Corrections with respect to the original scripts
-------------------------------------------------
* intra-zonal trips are kept: the original code removed them
  (``zone_o != zone_d``) *before* computing self-containment, which is why the
  report showed 0.01-0.08 %;
* connectivity is computed on off-diagonal pairs only, N(N-1);
* net flow and tidal indices are tested zone by zone (exact binomial test and
  two-proportion z-test) with Benjamini-Hochberg FDR control; non-significant
  zones are hatched on the maps;
* indicators are computed on working days only, with common time bands for
  all operators.
"""
from __future__ import annotations

import itertools

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LogNorm
from scipy import stats as st
from shapely.geometry import LineString

from . import config, plotting as P, spatial, stats as S
from .zones import assign_zones, load_zones

NF_BINS = [-1, -0.3, -0.15, -0.05, 0.05, 0.15, 0.3, 1]


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------
def zoned_trips(op: str, trips: pd.DataFrame, use_cache: bool = True) -> pd.DataFrame:
    f = config.CACHE_DIR / f"trips_zoned_{op}.pkl"
    if use_cache and f.exists():
        return pd.read_pickle(f)
    z = assign_zones(trips)
    z.to_pickle(f)
    return z


def in_city(t: pd.DataFrame) -> pd.DataFrame:
    return t[t["zone_o"].notna() & t["zone_d"].notna()]


def band_of_hour(h: pd.Series) -> pd.Series:
    m = {hh: b for b, hours in config.TIME_BANDS.items() for hh in hours}
    return h.map(m)


# ---------------------------------------------------------------------------
# Annual OD statistics
# ---------------------------------------------------------------------------
def od_matrix(t: pd.DataFrame, zones: list[str]) -> pd.DataFrame:
    m = t.groupby(["zone_o", "zone_d"]).size().unstack(fill_value=0)
    return m.reindex(index=zones, columns=zones, fill_value=0)


def annual_stats(op: str, t_all: pd.DataFrame, zones: gpd.GeoDataFrame) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    t = in_city(t_all)
    codes = zones["zone"].tolist()
    m = od_matrix(t, codes)
    n = len(codes)
    off = m.values[~np.eye(n, dtype=bool)]
    intra = np.trace(m.values)
    served = (m.sum(axis=1) > 0).sum()
    sc, lo, hi = S.wilson_ci(int(intra), int(m.values.sum()))
    stats = {
        "operator": config.label(op),
        "trips_total": len(t_all),
        "trips_both_ends_in_city": len(t),
        "share_in_city_pct": 100 * len(t) / len(t_all),
        "intra_zonal_trips": int(intra),
        "self_containment_pct": 100 * sc,
        "self_containment_ci": f"[{100 * lo:.2f}, {100 * hi:.2f}]",
        "zones_generating_trips": int(served),
        "connectivity_pct": 100 * (off > 0).sum() / (n * (n - 1)),
        "pairs_with_>=1_trip_per_week_pct": 100 * (off >= 52).sum() / (n * (n - 1)),
        "top10_pairs_share_pct": 100 * np.sort(off)[::-1][:10].sum() / off.sum(),
    }
    # undirected corridors
    und = (m + m.T)
    iu = np.triu_indices(n, 1)
    cor = pd.DataFrame({"zone_a": np.array(codes)[iu[0]], "zone_b": np.array(codes)[iu[1]],
                        "trips_both_directions": und.values[iu]})
    cor["a_to_b"] = m.values[iu]
    cor["b_to_a"] = m.values[iu[1], iu[0]]
    names = zones.set_index("zone")["name"]
    cor["name_a"] = cor["zone_a"].map(names)
    cor["name_b"] = cor["zone_b"].map(names)
    cor = cor.sort_values("trips_both_directions", ascending=False).head(15)
    cor.insert(0, "operator", op)
    return stats, cor, m


# ---------------------------------------------------------------------------
# Zonal indicators
# ---------------------------------------------------------------------------
def zonal_indicators(op: str, t_all: pd.DataFrame, zones: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    t = in_city(t_all)
    g = zones[["zone", "name", "area_km2", "population", "IDISE", "dist_centre_km", "geometry"]].copy()
    g = g.set_index("zone")
    days = t["date"].dropna().nunique()
    O = t.groupby("zone_o").size()
    D = t.groupby("zone_d").size()
    intra = t[t["zone_o"] == t["zone_d"]].groupby("zone_o").size()
    g["origins"] = O.reindex(g.index).fillna(0)
    g["destinations"] = D.reindex(g.index).fillna(0)
    g["origins_per_km2_day"] = g["origins"] / g["area_km2"] / days
    g["log_origin_density"] = np.log10(g["origins_per_km2_day"].where(g["origins_per_km2_day"] > 0))
    g["origins_per_1000_inh_day"] = 1000 * g["origins"] / g["population"] / days
    # self-containment SC_i = q_ii / O_i
    g["sc_pct"] = 100 * intra.reindex(g.index).fillna(0) / g["origins"].where(g["origins"] >= config.MIN_EVENTS_PER_ZONE)

    # time bands (working days only)
    w = t[t["has_timestamp"] & ~t["is_weekend"]].copy()
    w["band"] = band_of_hour(w["hour"])
    for band in config.TIME_BANDS:
        b = w[w["band"] == band]
        o = b.groupby("zone_o").size().reindex(g.index).fillna(0)
        d = b.groupby("zone_d").size().reindex(g.index).fillna(0)
        tot = o + d
        ok = tot >= config.MIN_EVENTS_PER_ZONE
        nf = ((d - o) / tot).where(ok)
        p = S.binom_two_sided(d.where(ok, 0).values, tot.where(ok, 0).values)
        p[~ok.values] = np.nan
        rej, q = S.fdr(p)
        key = band.split()[0].lower().replace("-", "")  # am / interpeak / pm / night
        g[f"o_{key}"], g[f"d_{key}"] = o, d
        g[f"nf_{key}"] = nf
        g[f"nf_{key}_q"] = q
        g[f"nf_{key}_sig"] = rej

    # tidal variation: attraction share AM - attraction share PM (percentage points)
    dam, dpm = g["d_am"], g["d_pm"]
    g["as_am_pct"] = 100 * dam / dam.sum()
    g["as_pm_pct"] = 100 * dpm / dpm.sum()
    ok = (dam + dpm) >= config.MIN_EVENTS_PER_ZONE
    g["tidal_pp"] = (g["as_am_pct"] - g["as_pm_pct"]).where(ok)
    p = S.two_proportion_z(dam, dam.sum(), dpm, dpm.sum())
    p[~ok.values] = np.nan
    g["tidal_sig"], g["tidal_q"] = S.fdr(p)
    g["operator"] = op
    return gpd.GeoDataFrame(g.reset_index(), geometry="geometry", crs=zones.crs)


def consistency_tests(ind: dict[str, gpd.GeoDataFrame]) -> pd.DataFrame:
    """Do the operators agree on which zones attract in the AM peak?"""
    rows = []
    for a, b in itertools.combinations(ind, 2):
        for col, lab in [("tidal_pp", "Tidal index"), ("nf_am", "Net flow AM"), ("nf_pm", "Net flow PM"),
                         ("log_origin_density", "log origin density")]:
            x = pd.concat([ind[a].set_index("zone")[col], ind[b].set_index("zone")[col]], axis=1).dropna()
            r, p = st.spearmanr(x.iloc[:, 0], x.iloc[:, 1])
            rows.append({"pair": f"{a} vs {b}", "indicator": lab, "n_zones": len(x), "spearman_rho": r, "p_value": p})
    for op, g in ind.items():
        x = g[["nf_am", "nf_pm"]].dropna()
        r, p = st.spearmanr(x["nf_am"], x["nf_pm"])
        rows.append({"pair": op, "indicator": "Net flow AM vs PM (same operator)", "n_zones": len(x),
                     "spearman_rho": r, "p_value": p})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def fig_od_heatmaps(mats: dict[str, pd.DataFrame]):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8))
    for ax, (op, m) in zip(axes, mats.items()):
        im = ax.imshow(m.values + 0, norm=LogNorm(vmin=1, vmax=m.values.max()), cmap=P.SEQ_CMAP, aspect="auto")
        im.cmap.set_bad("white")
        step = 10
        ax.set_xticks(range(0, len(m), step), m.columns[::step], fontsize=7, rotation=90)
        ax.set_yticks(range(0, len(m), step), m.index[::step], fontsize=7)
        ax.set_xlabel("Destination zone")
        ax.set_ylabel("Origin zone")
        ax.set_title(config.label(op), loc="left")
        ax.grid(False)
        cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.01)
        cb.set_label("Trips 2024 (log)", fontsize=8)
        cb.outline.set_visible(False)
    fig.tight_layout()
    return fig


def fig_desire_lines(mats: dict[str, pd.DataFrame], zones: gpd.GeoDataFrame, top: int = 80):
    cent = zones.set_index("zone").geometry.representative_point()
    fig, axes = P.map_grid(3)
    for ax, (op, m) in zip(axes, mats.items()):
        und = m + m.T
        n = len(und)
        iu = np.triu_indices(n, 1)
        df = pd.DataFrame({"a": und.index[iu[0]], "b": und.columns[iu[1]], "f": und.values[iu]})
        df = df.sort_values("f", ascending=False).head(top)
        df["geometry"] = [LineString([cent[a], cent[b]]) for a, b in zip(df.a, df.b)]
        lines = gpd.GeoDataFrame(df, geometry="geometry", crs=zones.crs).sort_values("f")
        zones.plot(ax=ax, color="#f4f3ef", edgecolor="none")
        intra = pd.Series(np.diag(m.values), index=m.index)
        pts = gpd.GeoDataFrame({"f": intra.values}, geometry=cent.reindex(intra.index).values, crs=zones.crs)
        pts = pts[pts.f > 0]
        ax.scatter(pts.geometry.x, pts.geometry.y, s=60 * pts.f / pts.f.max(), color=P.INK_2, alpha=0.5,
                   zorder=5, linewidths=0, label="Intra-zonal trips (size)")
        wmax = lines.f.max()
        lines.plot(ax=ax, color=P.op_color(op), linewidth=0.4 + 5 * lines.f / wmax, alpha=0.75, zorder=4)
        P._basemap(ax)
        from matplotlib.lines import Line2D
        q = np.quantile(lines.f, [0.0, 0.5, 1.0])
        handles = [Line2D([], [], color=P.op_color(op), lw=0.4 + 5 * v / wmax, label=f"{v:,.0f}") for v in q]
        handles.append(Line2D([], [], marker="o", ls="", color=P.INK_2, alpha=0.5, label="Intra-zonal (size)"))
        ax.legend(handles=handles, title=f"Top {top} corridors\n(trips/yr, both directions)", **P.LEGEND_OUT,
                  fontsize=7, title_fontsize=8)
        P._finish_map(ax, config.label(op))
    fig.tight_layout()
    return fig


def fig_density_lisa(ind: dict[str, gpd.GeoDataFrame]):
    fig, axes = P.map_grid(6)
    for i, (op, g) in enumerate(ind.items()):
        v = g["origins_per_km2_day"]
        bins = [0] + list(np.quantile(v[v > 0], [0.2, 0.4, 0.6, 0.8])) + [v.max()]
        P.choropleth(axes[i], g, "origins_per_km2_day", bins=bins, legend_title="Trip origins / km² / day\n(quintiles)",
                     fmt="{:.1f}", title=f"{config.label(op)} — origin density")
        P.categorical_map(axes[i + 3], g, "lisa", P.LISA_COLORS, legend_title="LISA cluster (p < 0.05)",
                          title=f"{config.label(op)} — local clusters")
    fig.tight_layout()
    return fig


def fig_netflow(ind: dict[str, gpd.GeoDataFrame]):
    keys = [("am", "AM peak 08-09"), ("pm", "PM peak 17-19")]
    fig, axes = P.map_grid(6)
    for r, (k, lab) in enumerate(keys):
        for c, (op, g) in enumerate(ind.items()):
            P.choropleth(axes[r * 3 + c], g, f"nf_{k}", bins=NF_BINS, cmap=P.DIV_CMAP, significant=f"nf_{k}_sig",
                         fmt="{:+.2f}", legend_title="Normalised net flow\n(+ attracts, − generates)",
                         title=f"{config.label(op)} — {lab}", show_legend=True)
    fig.tight_layout()
    return fig


def fig_tidal(ind: dict[str, gpd.GeoDataFrame]):
    fig, axes = P.map_grid(3)
    lim = max(np.nanmax(np.abs(g["tidal_pp"])) for g in ind.values())
    bins = [-lim, -2, -1, -0.25, 0.25, 1, 2, lim]
    for ax, (op, g) in zip(axes, ind.items()):
        P.choropleth(ax, g, "tidal_pp", bins=bins, cmap=P.DIV_CMAP, significant="tidal_sig", fmt="{:+.2f}",
                     legend_title="AM − PM attraction share (pp)\n+ morning destination\n− evening destination",
                     title=config.label(op), label_top=6)
    fig.tight_layout()
    return fig


def fig_self_containment(ind: dict[str, gpd.GeoDataFrame]):
    fig, axes = P.map_grid(3)
    bins = [0, 5, 10, 15, 20, 30, 60]
    for ax, (op, g) in zip(axes, ind.items()):
        P.choropleth(ax, g, "sc_pct", bins=bins, legend_title="Self-containment (%)\nintra-zonal / generated",
                     title=config.label(op), show_legend=True)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
def run(trips: dict[str, pd.DataFrame]) -> dict:
    P.set_style()
    zones = load_zones()
    zt = {op: zoned_trips(op, t) for op, t in trips.items()}
    stats, cors, mats, ind, moran_rows = [], [], {}, {}, []
    for op, t in zt.items():
        s, c, m = annual_stats(op, t, zones)
        stats.append(s)
        cors.append(c)
        mats[op] = m
        g = zonal_indicators(op, t, zones)
        g["lisa"] = spatial.lisa(g, "log_origin_density")
        for col in ["log_origin_density", "nf_am", "nf_pm", "tidal_pp", "sc_pct"]:
            r = spatial.moran(g, col)
            r["operator"] = op
            moran_rows.append(r)
        ind[op] = g

    out = {
        "annual": pd.DataFrame(stats),
        "corridors": pd.concat(cors, ignore_index=True),
        "moran": pd.DataFrame(moran_rows),
        "consistency": consistency_tests(ind),
        "indicators": ind,
        "matrices": mats,
        "zoned_trips": zt,
    }
    for k in ["annual", "corridors", "moran", "consistency"]:
        out[k].to_csv(config.TABLE_DIR / f"od_{k}.csv", index=False)
    sig = []
    for op, g in ind.items():
        for k in ["am", "interpeak", "pm", "night"]:
            tested = g[f"nf_{k}"].notna()
            sig.append({"operator": op, "band": k, "zones_tested": int(tested.sum()),
                        "zones_significant": int(g[f"nf_{k}_sig"].sum()),
                        "significant_attractors": int((g[f"nf_{k}_sig"] & (g[f"nf_{k}"] > 0)).sum()),
                        "significant_generators": int((g[f"nf_{k}_sig"] & (g[f"nf_{k}"] < 0)).sum())})
        sig.append({"operator": op, "band": "tidal", "zones_tested": int(g["tidal_pp"].notna().sum()),
                    "zones_significant": int(g["tidal_sig"].sum()),
                    "significant_attractors": int((g["tidal_sig"] & (g["tidal_pp"] > 0)).sum()),
                    "significant_generators": int((g["tidal_sig"] & (g["tidal_pp"] < 0)).sum())})
        g.drop(columns="geometry").to_csv(config.TABLE_DIR / f"zonal_indicators_{op}.csv", index=False)
        g.to_file(config.GPKG_FILE, layer=f"zonal_indicators_{op}", driver="GPKG")
    out["significance"] = pd.DataFrame(sig)
    out["significance"].to_csv(config.TABLE_DIR / "od_significance.csv", index=False)

    P.save(fig_od_heatmaps(mats), "od_matrices_log")
    P.save(fig_desire_lines(mats, zones), "map_od_desire_lines", config.MAP_DIR)
    P.save(fig_density_lisa(ind), "map_origin_density_lisa", config.MAP_DIR)
    P.save(fig_netflow(ind), "map_net_flow_am_pm", config.MAP_DIR)
    P.save(fig_tidal(ind), "map_tidal_index", config.MAP_DIR)
    P.save(fig_self_containment(ind), "map_self_containment", config.MAP_DIR)
    return out
