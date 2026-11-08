"""Descriptive statistics: demand profiles, trip characteristics and fleet use.

Improvements over the original analysis
---------------------------------------
* weekday demand is expressed as *mean trips per day* (2024 contains 53
  Mondays and Tuesdays but 52 of every other weekday) with 95% CIs, and
  tested with Kruskal-Wallis on the daily counts;
* hourly profiles are split into working days and weekends;
* fleet use is measured with trips per vehicle-day (TVD) and the Gini
  coefficient / Lorenz curve of trips per vehicle;
* operators are compared with Kruskal-Wallis + epsilon-squared and pairwise
  Cliff's delta (effect sizes, since with ~1.6 M trips every p-value is ~0).
"""
from __future__ import annotations

import itertools

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats as st

from . import config, plotting as P, stats as S

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def daily_counts(t: pd.DataFrame) -> pd.Series:
    """Trips per calendar day (timestamped trips only), zero-filled."""
    d = t[t["has_timestamp"]].groupby("date").size()
    lo, hi = d.index.min(), d.index.max()
    return d.reindex(pd.date_range(lo, hi, freq="D"), fill_value=0)


def vehicle_days(t: pd.DataFrame) -> pd.DataFrame:
    """One row per (vehicle, day) with at least one trip."""
    return (t[t["has_timestamp"]].groupby(["vehicle_id", "date"]).size()
            .rename("trips").reset_index())


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
def summary_table(trips: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for op, t in trips.items():
        vd = vehicle_days(t)
        active_per_day = vd.groupby("date").size()
        per_vehicle = t.groupby("vehicle_id").size()
        med_dur = S.median_ci(t["duration_min"])
        med_dist = S.median_ci(t["dist_m"] / 1000)
        med_speed = S.median_ci(t["speed_kmh"])
        res = S.wilson_ci(int(t["reserved"].sum()), len(t))
        tvd = S.mean_ci(vd.groupby("date")["trips"].sum() / active_per_day)
        rows.append({
            "operator": config.label(op),
            "valid_trips": len(t),
            "unique_vehicles": t["vehicle_id"].nunique(),
            "mean_daily_active_vehicles": active_per_day.mean(),
            "trips_per_vehicle_day": S.fmt_ci(*tvd),
            "median_duration_min": S.fmt_ci(*med_dur),
            "median_distance_km": S.fmt_ci(*med_dist),
            "median_speed_kmh": S.fmt_ci(*med_speed),
            "reserved_share_pct": S.fmt_ci(*(100 * np.array(res)), digits=1),
            "gini_trips_per_vehicle": S.gini(per_vehicle),
            "mean_trips_per_day": daily_counts(t).mean(),
        })
    return pd.DataFrame(rows)


def operator_tests(trips: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Kruskal-Wallis across operators + pairwise Cliff's delta."""
    rows = []
    for var, lab in [("duration_min", "Duration (min)"), ("dist_m", "Distance (m)"), ("speed_kmh", "Speed (km/h)")]:
        kw = S.kruskal_epsilon2([t[var].dropna().values for t in trips.values()])
        row = {"variable": lab, "H": kw["H"], "p_value": kw["p_value"], "epsilon2": kw["epsilon2"]}
        for a, b in itertools.combinations(trips, 2):
            row[f"cliffs_delta_{a}_vs_{b}"] = S.cliffs_delta(trips[a][var].dropna(), trips[b][var].dropna())
        rows.append(row)
    return pd.DataFrame(rows)


def monthly_table(trips: dict[str, pd.DataFrame]) -> pd.DataFrame:
    out = []
    for op, t in trips.items():
        m = t.groupby("month").size().rename("trips")
        days = pd.Series([pd.Period(f"{config.YEAR}-{i:02d}").days_in_month for i in range(1, 13)], index=range(1, 13))
        vd = vehicle_days(t)
        vd["month"] = vd["date"].dt.month
        act = t.groupby("month")["vehicle_id"].nunique().rename("active_vehicles")
        tvd = (vd.groupby("month")["trips"].sum() / vd.groupby("month").size()).rename("trips_per_vehicle_day")
        df = pd.concat([m, act, tvd], axis=1).reindex(range(1, 13))
        df["trips_per_day"] = df["trips"] / days
        df["operator"] = op
        out.append(df.reset_index(names="month"))
    return pd.concat(out, ignore_index=True)


def weekday_table(trips: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Weekday effect net of seasonality.

    * ``index`` = daily trips / centred 7-day moving average (classical
      seasonal index; 1.0 = an average day of that week), mean and 95% CI per
      weekday;
    * Friedman test with ISO weeks as blocks (each complete Mon-Sun week is a
      block), which removes the strong month-to-month variation that masks the
      weekday pattern in a test on raw daily counts.
    """
    rows, tests = [], []
    for op, t in trips.items():
        d = daily_counts(t)
        dfd = pd.DataFrame({"trips": d.values, "wd": d.index.dayofweek,
                            "week": d.index.isocalendar().week.values,
                            "year": d.index.isocalendar().year.values}, index=d.index)
        dfd["index"] = dfd["trips"] / dfd["trips"].rolling(7, center=True).mean()
        for wd in range(7):
            sel = dfd[dfd.wd == wd]
            m, lo, hi = S.mean_ci(sel["index"])
            rows.append({"operator": op, "weekday": WEEKDAYS[wd], "n_days": len(sel),
                         "mean_trips_per_day": sel["trips"].mean(),
                         "seasonal_index": m, "ci_low": lo, "ci_high": hi})
        wide = dfd.pivot_table(index=["year", "week"], columns="wd", values="trips").dropna()
        fr = st.friedmanchisquare(*[wide[c].values for c in range(7)])
        kendall_w = fr.statistic / (len(wide) * (7 - 1))
        we = dfd.loc[dfd.wd >= 5, "index"].dropna()
        wk = dfd.loc[dfd.wd < 5, "index"].dropna()
        tests.append({"operator": op, "n_complete_weeks": len(wide), "friedman_chi2": fr.statistic,
                      "friedman_p": fr.pvalue, "kendall_W": kendall_w,
                      "weekend_index": we.mean(), "workday_index": wk.mean(),
                      "mannwhitney_weekend_vs_workday_p": st.mannwhitneyu(we, wk).pvalue})
    return pd.DataFrame(rows), pd.DataFrame(tests)


def hourly_table(trips: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for op, t in trips.items():
        tt = t[t["has_timestamp"]]
        for wk, lab in [(False, "Working day"), (True, "Weekend")]:
            h = tt[tt["is_weekend"] == wk].groupby("hour").size().reindex(range(24), fill_value=0)
            share = 100 * h / h.sum()
            rows += [{"operator": op, "day_type": lab, "hour": int(i), "trips": int(h[i]), "share_pct": share[i]} for i in range(24)]
    df = pd.DataFrame(rows)
    return df


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def fig_monthly(mt: pd.DataFrame):
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.6))
    for ax, (col, lab) in zip(axes, [("trips_per_day", "Mean trips per day"),
                                      ("active_vehicles", "Active vehicles (≥1 trip in month)"),
                                      ("trips_per_vehicle_day", "Trips per active vehicle-day")]):
        for op in config.OPERATORS:
            d = mt[mt.operator == op]
            ax.plot(d["month"], d[col], marker="o", ms=4, color=P.op_color(op), label=config.label(op))
        ax.set_title(lab, loc="left")
        ax.set_xticks(range(1, 13), [m[0] for m in MONTHS])
        ax.set_ylim(bottom=0)
    axes[0].legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    return fig


def fig_weekday(wt: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    x = np.arange(7)
    w = 0.26
    for i, op in enumerate(config.OPERATORS):
        d = wt[wt.operator == op].set_index("weekday").reindex(WEEKDAYS)
        y = 100 * d["seasonal_index"]
        err = 100 * np.vstack([d["seasonal_index"] - d["ci_low"], d["ci_high"] - d["seasonal_index"]])
        ax.bar(x + (i - 1) * w, y - 100, bottom=100, width=w - 0.03, color=P.op_color(op), label=config.label(op))
        ax.errorbar(x + (i - 1) * w, y, yerr=err, fmt="none", ecolor=P.INK_2, elinewidth=1, capsize=2)
    ax.axhline(100, color=P.INK_2, lw=1)
    ax.set_xticks(x, WEEKDAYS)
    ax.set_ylabel("Weekday index (95% CI)\n100 = average day of the same week")
    ax.legend(ncol=3, fontsize=8, loc="upper left")
    fig.tight_layout()
    return fig


def fig_hourly(ht: pd.DataFrame):
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.6), sharey=True)
    for ax, dt in zip(axes, ["Working day", "Weekend"]):
        for op in config.OPERATORS:
            d = ht[(ht.operator == op) & (ht.day_type == dt)]
            ax.plot(d["hour"], d["share_pct"], color=P.op_color(op), label=config.label(op))
        ax.set_title(dt, loc="left")
        ax.set_xticks(range(0, 24, 2))
        ax.set_xlabel("Hour of departure (local time)")
    axes[0].set_ylabel("Share of trips (%)")
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    return fig


def fig_daily(trips: dict[str, pd.DataFrame]):
    fig, axes = plt.subplots(3, 1, figsize=(12, 7), sharex=True)
    for ax, (op, t) in zip(axes, trips.items()):
        d = daily_counts(t)
        ax.plot(d.index, d.values, color=P.op_color(op), lw=0.8, alpha=0.45)
        ax.plot(d.index, d.rolling(7, center=True).mean(), color=P.op_color(op), lw=2)
        ax.set_title(f"{config.label(op)} — daily trips (thin) and 7-day moving average", loc="left")
        ax.set_ylim(bottom=0)
    fig.tight_layout()
    return fig


def fig_lorenz(trips: dict[str, pd.DataFrame]):
    fig, ax = plt.subplots(figsize=(5, 4.6))
    ax.plot([0, 1], [0, 1], color=P.MUTED, lw=1, ls="--", label="Perfect equality")
    for op, t in trips.items():
        per_vehicle = t.groupby("vehicle_id").size()
        p, y = S.lorenz(per_vehicle)
        ax.plot(p, y, color=P.op_color(op), label=f"{config.label(op)} (Gini = {S.gini(per_vehicle):.2f})")
    ax.set_xlabel("Cumulative share of vehicles")
    ax.set_ylabel("Cumulative share of trips")
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    return fig


def fig_vehicle_month_box(trips: dict[str, pd.DataFrame]):
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.6), sharey=False)
    for ax, (op, t) in zip(axes, trips.items()):
        vm = t[t["has_timestamp"]].groupby(["vehicle_id", "month"]).size().rename("trips").reset_index()
        data = [vm.loc[vm.month == m, "trips"].values for m in range(1, 13)]
        bp = ax.boxplot(data, showfliers=False, patch_artist=True, widths=0.6,
                        medianprops=dict(color=P.INK, lw=1.5))
        for b in bp["boxes"]:
            b.set(facecolor=P.op_color(op), alpha=0.55, edgecolor=P.op_color(op))
        ax.set_xticks(range(1, 13), [m[0] for m in MONTHS])
        ax.set_title(config.label(op), loc="left")
    axes[0].set_ylabel("Trips per vehicle in the month")
    fig.tight_layout()
    return fig


def fig_distributions(trips: dict[str, pd.DataFrame]):
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.6))
    for op, t in trips.items():
        axes[0].hist(t["duration_min"].clip(upper=45), bins=np.arange(0, 45.5, 1), density=True,
                     histtype="step", lw=2, color=P.op_color(op), label=config.label(op))
        axes[1].hist((t["dist_m"] / 1000).clip(upper=8), bins=np.arange(0, 8.05, 0.2), density=True,
                     histtype="step", lw=2, color=P.op_color(op), label=config.label(op))
    axes[0].set_xlabel("Trip duration (min, values > 45 pooled in last bin)")
    axes[1].set_xlabel("Straight-line O-D distance (km, values > 8 pooled)")
    for ax in axes:
        ax.set_ylabel("Density")
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    return fig


def run(trips: dict[str, pd.DataFrame]) -> dict:
    P.set_style()
    out = {}
    out["summary"] = summary_table(trips)
    out["operator_tests"] = operator_tests(trips)
    out["monthly"] = monthly_table(trips)
    out["weekday"], out["weekday_tests"] = weekday_table(trips)
    out["hourly"] = hourly_table(trips)
    for name, df in out.items():
        df.to_csv(config.TABLE_DIR / f"desc_{name}.csv", index=False)
    P.save(fig_monthly(out["monthly"]), "desc_monthly")
    P.save(fig_weekday(out["weekday"]), "desc_weekday")
    P.save(fig_hourly(out["hourly"]), "desc_hourly")
    P.save(fig_daily(trips), "desc_daily_series")
    P.save(fig_lorenz(trips), "fleet_lorenz")
    P.save(fig_vehicle_month_box(trips), "fleet_trips_per_vehicle_month")
    P.save(fig_distributions(trips), "desc_duration_distance")
    return out
