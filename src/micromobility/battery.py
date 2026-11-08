"""Battery consumption (operators A and B, the only ones reporting SoC).

* Tukey fences (Q3 + 1.5 IQR) on consumption per km and per minute define the
  four consumption regimes used in the original report;
* a linear model  drop = b0 + b1*distance_km + b2*duration_min  (OLS with
  HC3 robust standard errors) quantifies the marginal energy cost of distance
  and time. Its coefficients are also used to estimate energy for operator C,
  which reports no battery data (replacing the "1% every 180 m" rule of thumb).
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from . import config, plotting as P

QUADS = ["High efficiency", "Long but low-intensity", "Short but intense", "Stressful"]


def prepare(t: pd.DataFrame) -> pd.DataFrame:
    b = t[t["batt_start"].notna() & t["batt_end"].notna()].copy()
    b["dist_km"] = b["dist_m"] / 1000
    b["valid_drop"] = (b["batt_drop"] >= 0) & (b["batt_drop"] <= 100) & (b["batt_start"] <= 100)
    b = b[b["valid_drop"]]
    b["c_km"] = b["batt_drop"] / b["dist_km"]
    b["c_min"] = b["batt_drop"] / b["duration_min"]
    return b


def tukey(b: pd.DataFrame) -> tuple[float, float, pd.Series]:
    def fence(x):
        q1, q3 = np.percentile(x, [25, 75])
        return q3 + 1.5 * (q3 - q1)
    tk, tm = fence(b["c_km"]), fence(b["c_min"])
    hi_km, hi_min = b["c_km"] > tk, b["c_min"] > tm
    quad = np.select([~hi_km & ~hi_min, hi_km & ~hi_min, ~hi_km & hi_min], QUADS[:3], QUADS[3])
    return tk, tm, pd.Series(quad, index=b.index)


def regression(b: pd.DataFrame):
    return smf.ols("batt_drop ~ dist_km + duration_min", data=b).fit(cov_type="HC3")


def run(trips: dict[str, pd.DataFrame]) -> dict:
    P.set_style()
    rows, quad_rows, coefs = [], [], {}
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
    for ax, op in zip(axes, ["A", "B"]):
        t = trips[op]
        b = prepare(t)
        tk, tm, quad = tukey(b)
        b["quadrant"] = quad
        m = regression(b)
        coefs[op] = m.params
        ci = m.conf_int()
        rows.append({
            "operator": config.label(op), "trips_with_battery": len(t),
            "valid_drop_trips": len(b), "negative_or_invalid_drop_pct": 100 * (1 - len(b) / len(t)),
            "tukey_c_km": tk, "tukey_c_min": tm,
            "b_dist_pct_per_km": m.params["dist_km"], "b_dist_ci": f"[{ci.loc['dist_km', 0]:.2f}, {ci.loc['dist_km', 1]:.2f}]",
            "b_dur_pct_per_min": m.params["duration_min"], "b_dur_ci": f"[{ci.loc['duration_min', 0]:.3f}, {ci.loc['duration_min', 1]:.3f}]",
            "intercept": m.params["Intercept"], "r2": m.rsquared,
            "median_drop": b["batt_drop"].median(), "mean_drop": b["batt_drop"].mean(),
        })
        for q in QUADS:
            s = b.loc[b["quadrant"] == q, "batt_drop"]
            quad_rows.append({"operator": op, "quadrant": q, "trips": len(s), "share_pct": 100 * len(s) / len(b),
                              "median_drop": s.median(), "q1": s.quantile(0.25), "q3": s.quantile(0.75)})
        # hexbin keeps a million points readable
        hb = ax.hexbin(b["c_km"].clip(0.1, 100), b["c_min"].clip(0.01, 20), gridsize=70, xscale="log", yscale="log",
                       bins="log", cmap=P.SEQ_CMAP, mincnt=1, linewidths=0)
        ax.axvline(tk, color=P.INK, lw=1.2, ls="--")
        ax.axhline(tm, color=P.INK, lw=1.2, ls="--")
        ax.text(tk, ax.get_ylim()[1], f" {tk:.1f} %/km", va="top", fontsize=8)
        ax.text(ax.get_xlim()[0], tm, f" {tm:.2f} %/min", va="bottom", fontsize=8)
        ax.set_xlabel("Consumption per km (% battery / km, log)")
        ax.set_ylabel("Consumption per minute (% / min, log)")
        ax.set_title(f"{config.label(op)} — Tukey quadrants", loc="left")
        cb = fig.colorbar(hb, ax=ax, fraction=0.04, pad=0.01)
        cb.set_label("Trips (log)", fontsize=8)
        cb.outline.set_visible(False)
    fig.tight_layout()
    P.save(fig, "battery_tukey_quadrants")

    # battery drop vs distance: binned means with 95% CI
    fig, ax = plt.subplots(figsize=(6.5, 4))
    for op in ["A", "B"]:
        b = prepare(trips[op])
        b["bin"] = pd.cut(b["dist_km"], np.arange(0, 8.5, 0.5))
        g = b.groupby("bin", observed=True)["batt_drop"].agg(["mean", "std", "count"])
        x = np.arange(0.25, 0.25 + 0.5 * len(g), 0.5)[: len(g)]
        h = 1.96 * g["std"] / np.sqrt(g["count"])
        ax.plot(x, g["mean"], marker="o", ms=4, color=P.op_color(op), label=config.label(op))
        ax.fill_between(x, g["mean"] - h, g["mean"] + h, color=P.op_color(op), alpha=0.2, lw=0)
    ax.set_xlabel("Straight-line distance (km, 0.5 km bins)")
    ax.set_ylabel("Mean battery drop (% points)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    P.save(fig, "battery_drop_vs_distance")

    summary = pd.DataFrame(rows)
    quads = pd.DataFrame(quad_rows)
    summary.to_csv(config.TABLE_DIR / "battery_summary.csv", index=False)
    quads.to_csv(config.TABLE_DIR / "battery_quadrants.csv", index=False)
    pooled = pd.concat([coefs["A"], coefs["B"]], axis=1).mean(axis=1)
    return {"summary": summary, "quadrants": quads, "coefs": coefs, "pooled_coefs": pooled}


def estimate_drop(t: pd.DataFrame, coefs: pd.Series) -> pd.Series:
    """Predicted % battery drop per trip from the pooled A/B model (floored at 0)."""
    pred = coefs["Intercept"] + coefs["dist_km"] * t["dist_m"] / 1000 + coefs["duration_min"] * t["duration_min"]
    return pred.clip(lower=0)
