"""Idle (dwell) time between consecutive trips of the same vehicle.

Idle time of vehicle v after trip n:  D = start(n+1) - end(n), located at the
destination of trip n. Changes with respect to the original analysis:

* idle times are extremely right-skewed, so zones are summarised by the
  **median** (with the mean reported only for reference);
* gaps longer than 24 h are not averaged but reported as a share
  (vehicle withdrawn, charging or out of service);
* a **relocation** is flagged when the next trip starts more than
  ``RELOCATION_M`` from where the previous one ended: the vehicle was moved by
  the operator (rebalancing / battery swap / pick-up). The relocation rate is
  an observed proxy of rebalancing effort, used later in the cost model;
* differences between time bands are tested with Kruskal-Wallis, and the
  claimed inverse relation between demand density and idle time is tested
  with a Spearman correlation across zones.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats as st

from . import config, plotting as P, stats as S
from .cleaning import geodesic_m
from .od import band_of_hour
from .zones import load_zones

RELOCATION_M = 300
MAX_IDLE_H = 24


def idle_events(t: pd.DataFrame) -> pd.DataFrame:
    t = t[t["has_timestamp"]].sort_values(["vehicle_id", "start_time"])
    nxt = t.groupby("vehicle_id")[["start_time", "lon_o", "lat_o"]].shift(-1)
    ev = pd.DataFrame({
        "vehicle_id": t["vehicle_id"].values,
        "end_time": t["end_time"].values,
        "zone": t["zone_d"].values,
        "idle_h": (nxt["start_time"] - t["end_time"]).dt.total_seconds().values / 3600,
        "gap_m": geodesic_m(t["lon_d"].values, t["lat_d"].values,
                            nxt["lon_o"].fillna(0).values, nxt["lat_o"].fillna(0).values),
    })
    ev = ev[ev["idle_h"].notna() & (ev["idle_h"] >= 0)]
    ev["relocated"] = ev["gap_m"] > RELOCATION_M
    ev["hour"] = ev["end_time"].dt.hour
    ev["weekend"] = ev["end_time"].dt.dayofweek >= 5
    ev["band"] = band_of_hour(ev["hour"])
    return ev


def summarise(op: str, ev: pd.DataFrame) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    within = ev[ev["idle_h"] <= MAX_IDLE_H]
    med = S.median_ci(within["idle_h"])
    reloc = S.wilson_ci(int(ev["relocated"].sum()), len(ev))
    wd = within[~within["weekend"] & within["band"].notna()]
    groups = [g["idle_h"].values for _, g in wd.groupby("band")]
    kw = S.kruskal_epsilon2(groups)
    summary = {
        "operator": config.label(op), "idle_events": len(ev),
        "share_gaps_over_24h_pct": 100 * (ev["idle_h"] > MAX_IDLE_H).mean(),
        "median_idle_h": S.fmt_ci(*med), "mean_idle_h": within["idle_h"].mean(),
        "relocation_rate_pct": S.fmt_ci(*(100 * np.array(reloc)), 1),
        "kruskal_bands_H": kw["H"], "kruskal_bands_p": kw["p_value"], "kruskal_bands_eps2": kw["epsilon2"],
    }
    band = (wd.groupby("band")["idle_h"].agg(["median", "mean", "count",
                                             lambda x: x.quantile(0.25), lambda x: x.quantile(0.75)])
            .rename(columns={"<lambda_0>": "q1", "<lambda_1>": "q3"}).reset_index())
    band.insert(0, "operator", op)
    zone = within.groupby("zone").agg(median_idle_h=("idle_h", "median"), events=("idle_h", "size"))
    zone["relocation_rate_pct"] = 100 * ev.groupby("zone")["relocated"].mean()
    zone.loc[zone["events"] < config.MIN_EVENTS_PER_ZONE, ["median_idle_h", "relocation_rate_pct"]] = np.nan
    for b in ["AM peak 08-09", "Night 23-05"]:
        sub = wd[wd["band"] == b].groupby("zone")["idle_h"].agg(["median", "size"])
        zone[f"median_idle_h_{b.split()[0].lower()}"] = sub["median"].where(sub["size"] >= config.MIN_EVENTS_PER_ZONE)
    return summary, band, zone.reset_index()


def run(zoned: dict[str, pd.DataFrame], indicators: dict) -> dict:
    P.set_style()
    zones = load_zones()
    summ, bands, zmaps, corr = [], [], {}, []
    fig_e, ax_e = plt.subplots(figsize=(6.5, 4))
    for op, t in zoned.items():
        ev = idle_events(t)
        s, b, z = summarise(op, ev)
        summ.append(s)
        bands.append(b)
        g = zones.merge(z, on="zone", how="left")
        dens = indicators[op].set_index("zone")["origins_per_km2_day"]
        g["origin_density"] = g["zone"].map(dens)
        x = g[["median_idle_h", "origin_density", "relocation_rate_pct"]].dropna()
        r1 = st.spearmanr(x["median_idle_h"], x["origin_density"])
        r2 = st.spearmanr(x["relocation_rate_pct"], x["origin_density"])
        corr += [{"operator": op, "pair": "median idle vs origin density", "rho": r1.statistic, "p": r1.pvalue, "n": len(x)},
                 {"operator": op, "pair": "relocation rate vs origin density", "rho": r2.statistic, "p": r2.pvalue, "n": len(x)}]
        zmaps[op] = g
        h = np.sort(ev.loc[ev["idle_h"] <= 48, "idle_h"].values)
        ax_e.plot(h, np.arange(1, len(h) + 1) / len(ev), color=P.op_color(op), label=config.label(op))
    ax_e.set_xlabel("Idle time between consecutive trips (h)")
    ax_e.set_ylabel("Cumulative share of idle periods")
    ax_e.set_xlim(0, 48)
    ax_e.set_xticks(range(0, 49, 6))
    ax_e.legend(fontsize=8, loc="lower right")
    fig_e.tight_layout()
    P.save(fig_e, "idle_time_ecdf")

    fig, axes = P.map_grid(6)
    bins_idle = [0, 1, 2, 3, 4, 6, 9, 24]
    bins_rel = [0, 10, 15, 20, 25, 30, 40, 100]
    for c, (op, g) in enumerate(zmaps.items()):
        P.choropleth(axes[c], g, "median_idle_h", bins=bins_idle, legend_title="Median idle time (h)",
                     fmt="{:.0f}", title=f"{config.label(op)} — median idle time")
        P.choropleth(axes[3 + c], g, "relocation_rate_pct", bins=bins_rel,
                     legend_title=f"Idle periods ending with\na relocation > {RELOCATION_M} m (%)",
                     fmt="{:.0f}", title=f"{config.label(op)} — operator relocations")
    fig.tight_layout()
    P.save(fig, "map_idle_time_relocations", config.MAP_DIR)

    out = {"summary": pd.DataFrame(summ), "bands": pd.concat(bands), "correlations": pd.DataFrame(corr), "zones": zmaps}
    for k in ["summary", "bands", "correlations"]:
        out[k].to_csv(config.TABLE_DIR / f"idle_{k}.csv", index=False)
    return out
