"""Micromobility vs scheduled public transport (GTT GTFS).

Corrections with respect to the original analysis
-------------------------------------------------
* the GTFS service calendar is resolved for a single representative weekday
  (``config.GTFS_REFERENCE_DAY``) using ``calendar.txt`` *and*
  ``calendar_dates.txt``. The original script matched services to "July" and
  "December" by comparing only the month numbers of start/end dates and summed
  every overlapping service variant (weekday, Saturday, holiday...), which
  produced the +931 % December/July ratio. In fact the feed holds a complete
  urban timetable only until 2026-02-28 (see :func:`feed_coverage`), so no
  summer timetable exists in it: supply is held fixed and only the
  micromobility side is compared between July and December;
* supply in a hexagon = number of distinct vehicle trips (passages) calling at
  any stop in the hexagon in the hour, so a line with two stops in the same
  hexagon is not double counted;
* micromobility classes are tertiles of the non-zero counts, pooled over all
  scenarios so that maps are comparable; PT classes use headway thresholds;
* the association between the two layers is tested (Spearman, chi-square);
* each trip is checked for a *direct* urban line linking stops within 250 m of
  its origin and destination (potential substitution) and for metro access.

Note: the 2025-26 feed is used as a proxy of the 2024 winter timetable.
"""
from __future__ import annotations

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch
from scipy import stats as st
from scipy.spatial import cKDTree
from shapely.geometry import Polygon

from . import config, plotting as P, stats as S
from .zones import load_zones

BIVAR = {  # PT class (first digit) x micromobility class (second digit)
    "11": "#e8e8e8", "12": "#e4acac", "13": "#c85a5a",
    "21": "#b0d5df", "22": "#ad9ea5", "23": "#985356",
    "31": "#64acbe", "32": "#627f8c", "33": "#574249",
}
PT_BREAKS = [4, 12]  # passages/h: <4 (headway >15 min or none), 4-11, >=12 (<=5 min)
SCENARIOS = {"AM peak (08-09)": 8, "Late evening (23-24)": 23}
METRO_M = 300


# ---------------------------------------------------------------------------
# GTFS
# ---------------------------------------------------------------------------
def _read(name, **kw):
    return pd.read_csv(config.GTFS_DIR / name, dtype=str, **kw)


def active_services(date: str) -> set[str]:
    cal = _read("calendar.txt")
    cd = _read("calendar_dates.txt")
    dow = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][pd.Timestamp(date).dayofweek]
    base = set(cal.loc[(cal[dow] == "1") & (cal.start_date <= date) & (cal.end_date >= date), "service_id"])
    add = set(cd.loc[(cd.date == date) & (cd.exception_type == "1"), "service_id"])
    rem = set(cd.loc[(cd.date == date) & (cd.exception_type == "2"), "service_id"])
    return (base | add) - rem


def load_gtfs() -> dict:
    routes = _read("routes.txt")
    trips = _read("trips.txt", usecols=["route_id", "service_id", "trip_id"])
    stops = _read("stops.txt", usecols=["stop_id", "stop_lat", "stop_lon"])
    st_ = _read("stop_times.txt", usecols=["trip_id", "departure_time", "stop_id"])
    st_["hour"] = st_["departure_time"].str.split(":").str[0].astype(int) % 24
    urban = routes[routes["agency_id"] == "U"]
    trips = trips[trips["route_id"].isin(urban["route_id"])].merge(urban[["route_id", "route_type"]], on="route_id")
    pts = gpd.GeoDataFrame(stops, geometry=gpd.points_from_xy(stops.stop_lon.astype(float), stops.stop_lat.astype(float)),
                           crs=config.CRS_WGS84).to_crs(config.CRS_METRIC)
    return {"trips": trips, "stops": pts, "stop_times": st_}


def day_supply(g: dict, date: str) -> pd.DataFrame:
    """Stop-level calls (stop_id, trip_id, route_id, route_type, hour) on a date."""
    tr = g["trips"][g["trips"]["service_id"].isin(active_services(date))]
    return g["stop_times"].merge(tr[["trip_id", "route_id", "route_type"]], on="trip_id")


# ---------------------------------------------------------------------------
# Hexagon grid
# ---------------------------------------------------------------------------
def hex_grid(size: float = config.HEX_SIZE_M) -> gpd.GeoDataFrame:
    """Flat-topped hexagons with circumradius ``size`` covering the city."""
    city = load_zones().union_all()
    xmin, ymin, xmax, ymax = city.bounds
    w, h = 2 * size, np.sqrt(3) * size
    polys = []
    for i, x in enumerate(np.arange(xmin, xmax + w, 0.75 * w)):
        for y in np.arange(ymin - h, ymax + h, h):
            cy = y + (h / 2 if i % 2 else 0)
            ang = np.deg2rad(np.arange(0, 360, 60))
            polys.append(Polygon(zip(x + size * np.cos(ang), cy + size * np.sin(ang))))
    grid = gpd.GeoDataFrame(geometry=polys, crs=config.CRS_METRIC)
    grid = grid[grid.intersects(city)].reset_index(drop=True)
    grid["hex_id"] = grid.index
    return grid


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def pooled_trips(zoned: dict[str, pd.DataFrame], month: int) -> gpd.GeoDataFrame:
    t = pd.concat([z[(z["month"] == month) & z["has_timestamp"] & ~z["is_weekend"]] for z in zoned.values()])
    g = gpd.GeoDataFrame(t, geometry=gpd.points_from_xy(t.lon_o, t.lat_o), crs=config.CRS_WGS84).to_crs(config.CRS_METRIC)
    d = gpd.GeoSeries(gpd.points_from_xy(t.lon_d, t.lat_d), crs=config.CRS_WGS84).to_crs(config.CRS_METRIC)
    g["xd"], g["yd"] = d.x.values, d.y.values
    return g


def trip_pt_relation(trips: gpd.GeoDataFrame, calls: pd.DataFrame, stops: gpd.GeoDataFrame) -> pd.DataFrame:
    """Per trip: distance to nearest active stop, direct-line availability, metro access."""
    act = stops[stops.stop_id.isin(calls.stop_id.unique())].reset_index(drop=True)
    xy = np.c_[act.geometry.x, act.geometry.y]
    tree = cKDTree(xy)
    routes_by_stop = calls.groupby("stop_id")["route_id"].apply(frozenset)
    stop_routes = act["stop_id"].map(routes_by_stop).tolist()
    o = np.c_[trips.geometry.x, trips.geometry.y]
    d = np.c_[trips["xd"], trips["yd"]]
    dist_o, _ = tree.query(o)
    near_o = tree.query_ball_point(o, config.STOP_BUFFER_M)
    near_d = tree.query_ball_point(d, config.STOP_BUFFER_M)

    def routes(idx):
        s = set()
        for i in idx:
            s |= stop_routes[i]
        return s
    direct = np.array([bool(routes(a) & routes(b)) for a, b in zip(near_o, near_d)])

    metro_ids = calls.loc[calls.route_type == "1", "stop_id"].unique()
    metro = act[act.stop_id.isin(metro_ids)]
    mt = cKDTree(np.c_[metro.geometry.x, metro.geometry.y])
    dm_o, _ = mt.query(o)
    dm_d, _ = mt.query(d)
    return pd.DataFrame({"dist_stop_m": dist_o, "stop_within_250m": dist_o <= config.STOP_BUFFER_M,
                         "direct_line": direct, "metro_end": (dm_o <= METRO_M) | (dm_d <= METRO_M)},
                        index=trips.index)


def hex_layers(grid, trips, calls, stops, hour) -> gpd.GeoDataFrame:
    g = grid.copy()
    s = stops.merge(calls[calls.hour == hour][["stop_id", "trip_id"]], on="stop_id")
    s = gpd.sjoin(gpd.GeoDataFrame(s, geometry="geometry", crs=stops.crs), grid[["hex_id", "geometry"]], predicate="within")
    g["pt_passages"] = g["hex_id"].map(s.groupby("hex_id")["trip_id"].nunique()).fillna(0)
    tr = trips[trips["hour"] == hour]
    j = gpd.sjoin(tr[["geometry"]], grid[["hex_id", "geometry"]], predicate="within")
    g["micro_trips"] = g["hex_id"].map(j.groupby("hex_id").size()).fillna(0)
    return g


def classify(scen: dict[str, gpd.GeoDataFrame]) -> tuple[dict, list]:
    pooled = pd.concat([g["micro_trips"] for g in scen.values()])
    br = list(np.quantile(pooled[pooled > 0], [1 / 3, 2 / 3]))
    for g in scen.values():
        g["pt_class"] = np.digitize(g["pt_passages"], PT_BREAKS) + 1
        g["micro_class"] = np.digitize(g["micro_trips"], br, right=True) + 1
        g["bivar"] = (g["pt_class"].astype(str) + g["micro_class"].astype(str)).where(
            (g["pt_passages"] > 0) | (g["micro_trips"] > 0))
    return scen, br


def legend_square(ax):
    ins = ax.inset_axes([0.99, 0.02, 0.22, 0.22])
    for i in range(3):
        for j in range(3):
            ins.add_patch(plt.Rectangle((j, i), 1, 1, color=BIVAR[f"{i + 1}{j + 1}"]))
    ins.set_xlim(0, 3)
    ins.set_ylim(0, 3)
    ins.set_xticks([])
    ins.set_yticks([])
    ins.set_xlabel("Micromobility →", fontsize=7)
    ins.set_ylabel("PT passages →", fontsize=7)
    for s in ins.spines.values():
        s.set_visible(False)


def feed_coverage(g: dict) -> pd.Series:
    """Number of urban vehicle trips scheduled on every date of the feed."""
    cd = _read("calendar_dates.txt")
    n = g["trips"]["service_id"].value_counts()
    a = cd[cd.exception_type == "1"].copy()
    a["n"] = a["service_id"].map(n).fillna(0)
    d = a.groupby("date")["n"].sum()
    d.index = pd.to_datetime(d.index)
    return d


def run(zoned: dict[str, pd.DataFrame]) -> dict:
    P.set_style()
    g = load_gtfs()
    grid = hex_grid()
    date = config.GTFS_REFERENCE_DAY
    calls = day_supply(g, date)
    prof = calls.groupby("hour")["trip_id"].nunique().reindex(range(24), fill_value=0)

    # --- figure: feed coverage + hourly supply on the reference day
    cov = feed_coverage(g)
    wk = cov[cov.index.dayofweek < 5]
    fig_s, (a1, a2) = plt.subplots(1, 2, figsize=(13, 3.6), gridspec_kw={"width_ratios": [1.4, 1]})
    a1.plot(wk.index, wk.values, color=P.SEQ_BLUE[4], lw=1.2)
    a1.axvline(pd.Timestamp("2026-03-01"), color=P.INK_2, lw=1, ls="--")
    a1.text(pd.Timestamp("2026-03-15"), wk.max() * 0.85, "complete urban timetable\nends 28 Feb 2026",
            fontsize=8, color=P.INK_2)
    a1.set_title("GTT feed: urban vehicle trips scheduled per weekday", loc="left")
    a1.set_ylim(bottom=0)
    a2.plot(prof.index, prof.values, color=P.SEQ_BLUE[4])
    a2.set_title(f"Urban trips in service by hour, {pd.Timestamp(date):%a %d %b %Y}", loc="left")
    a2.set_xticks(range(0, 24, 3))
    a2.set_xlabel("Hour")
    a2.set_ylim(bottom=0)
    fig_s.tight_layout()
    P.save(fig_s, "pt_feed_coverage_and_supply")

    supply = pd.DataFrame([{
        "reference_day": date, "active_services": len(active_services(date)),
        "urban_vehicle_trips_day": calls.trip_id.nunique(), "stops_served": calls.stop_id.nunique(),
        "peak_hour": int(prof.idxmax()), "trips_in_peak_hour": int(prof.max()),
        "trips_at_08": int(prof[8]), "trips_at_11": int(prof[11]), "trips_at_23": int(prof[23]),
        "ratio_08_to_23": prof[8] / prof[23],
        "first_weekday_incomplete_timetable": str(wk[wk < 0.2 * wk.max()].index.min().date()),
    }])

    rel_rows, assoc_rows, hexes = [], [], {}
    for season, month in {"July": 7, "December": 12}.items():
        trips = pooled_trips(zoned, month)
        rel = trip_pt_relation(trips, calls, g["stops"])
        for col, lab in [("stop_within_250m", "Origin within 250 m of an active stop"),
                         ("direct_line", "Direct urban line between O and D stops (<=250 m)"),
                         ("metro_end", f"Origin or destination <={METRO_M} m from a metro station")]:
            k = int(rel[col].sum())
            p, lo, hi = S.wilson_ci(k, len(rel))
            rel_rows.append({"season": season, "indicator": lab, "trips": len(rel), "count": k,
                             "share_pct": 100 * p, "ci_low": 100 * lo, "ci_high": 100 * hi})
        rel_rows.append({"season": season, "indicator": "Median distance to nearest active stop (m)",
                         "trips": len(rel), "count": np.nan, "share_pct": rel["dist_stop_m"].median(),
                         "ci_low": np.nan, "ci_high": np.nan})
        for sname, hour in SCENARIOS.items():
            hexes[f"{season} — {sname}"] = hex_layers(grid, trips, calls, g["stops"], hour)

    # seasonal differences of the trip-level indicators (two-proportion z-tests)
    rel_df = pd.DataFrame(rel_rows)
    tests = []
    for lab in rel_df.indicator.unique():
        if lab.startswith("Median"):
            continue
        j = rel_df[(rel_df.indicator == lab) & (rel_df.season == "July")].iloc[0]
        d = rel_df[(rel_df.indicator == lab) & (rel_df.season == "December")].iloc[0]
        p = S.two_proportion_z([j["count"]], [j["trips"]], [d["count"]], [d["trips"]])[0]
        tests.append({"indicator": lab, "july_pct": j.share_pct, "december_pct": d.share_pct,
                      "difference_pp": d.share_pct - j.share_pct, "p_value": p})

    hexes, micro_breaks = classify(hexes)
    for name, h in hexes.items():
        act = h[(h.pt_passages > 0) | (h.micro_trips > 0)]
        rho = st.spearmanr(act.pt_passages, act.micro_trips)
        ct = pd.crosstab(act.pt_class, act.micro_class)
        chi = st.chi2_contingency(ct)
        cramer = np.sqrt(chi.statistic / (ct.values.sum() * (min(ct.shape) - 1)))
        assoc_rows.append({"scenario": name, "active_hexagons": len(act), "spearman_rho": rho.statistic,
                           "spearman_p": rho.pvalue, "chi2": chi.statistic, "chi2_p": chi.pvalue,
                           "cramers_v": cramer,
                           "hex_13_micro_high_pt_low": int((act.bivar == "13").sum()),
                           "hex_33_both_high": int((act.bivar == "33").sum()),
                           "hex_31_pt_high_micro_low": int((act.bivar == "31").sum())})

    fig, axes = P.map_grid(4, ncols=2, size=5.2)
    boundary = load_zones()
    for ax, (name, h) in zip(axes, hexes.items()):
        boundary.plot(ax=ax, color="white", edgecolor="none")
        for code, col in BIVAR.items():
            sub = h[h.bivar == code]
            if len(sub):
                sub.plot(ax=ax, color=col, edgecolor="none", zorder=2)
        P._basemap(ax)
        legend_square(ax)
        r = next(a for a in assoc_rows if a["scenario"] == name)
        P._finish_map(ax, f"{name}\nSpearman ρ = {r['spearman_rho']:.2f} · Cramér's V = {r['cramers_v']:.2f}")
    fig.tight_layout()
    P.save(fig, "map_bivariate_pt_micromobility", config.MAP_DIR)

    out = {"supply": supply, "relation": rel_df, "seasonal_tests": pd.DataFrame(tests),
           "association": pd.DataFrame(assoc_rows),
           "breaks": pd.DataFrame({"class_break": ["micro tertile 1/3", "micro tertile 2/3", "PT 4 /h", "PT 12 /h"],
                                   "value": list(micro_breaks) + PT_BREAKS})}
    for k in ["supply", "relation", "seasonal_tests", "association", "breaks"]:
        out[k].to_csv(config.TABLE_DIR / f"pt_{k}.csv", index=False)
    return out
