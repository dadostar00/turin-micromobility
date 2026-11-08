"""Operator profit & loss with uncertainty and break-even analysis.

Changes with respect to the original spreadsheets
-------------------------------------------------
* inputs come from the corrected datasets (Voi: 2024 only, de-duplicated);
* **fleet basis**: per-vehicle-day costs (rebalancing, connectivity) and fleet
  amortisation are charged on the *average deployed fleet* (mean number of
  vehicles active in each month) rather than on every vehicle ID seen during
  the year, which counts replaced/rotated vehicles as if they were on the
  street 365 days. Both bases are reported;
* operator C energy is estimated from the battery regression fitted on A and B
  instead of the "1 % every 180 m" rule;
* revenue uses started minutes (per-minute tariffs are billed per started
  minute);
* parameter uncertainty is propagated with a Monte Carlo simulation
  (triangular distributions in ``config.MC_RANGES``) giving the probability
  of a positive result;
* break-even utilisation (trips per vehicle-day) is derived analytically.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import config, plotting as P
from .battery import estimate_drop


def operating_inputs(op: str, t: pd.DataFrame, pooled_coefs: pd.Series) -> dict:
    drop = t["batt_drop"].where(t["batt_drop"].between(0, 100)) if op != "C" else estimate_drop(t, pooled_coefs)
    monthly_active = t.groupby("month")["vehicle_id"].nunique()
    return {
        "trips": len(t),
        "billed_minutes": float(np.ceil(t["duration_min"]).sum()),
        "unique_vehicles": t["vehicle_id"].nunique(),
        "avg_deployed_fleet": float(monthly_active.mean()),
        "battery_points": float(drop.fillna(drop.mean()).sum()),
    }


def pnl(op: str, x: dict, c: config.CostAssumptions = config.COSTS, fleet_basis: str = "deployed",
        per_min_factor: float = 1.0) -> dict:
    tar = config.TARIFFS[op]
    fleet = x["avg_deployed_fleet"] if fleet_basis == "deployed" else x["unique_vehicles"]
    rev_unlock = x["trips"] * tar.unlock_eur
    rev_min = x["billed_minutes"] * tar.per_min_eur * per_min_factor
    kwh = x["battery_points"] / 100 * c.battery_kwh * (1 + c.charging_loss)
    fixed = {
        "Fleet amortisation": fleet * c.vehicle_price_eur / c.vehicle_life_years,
        "Insurance": x["unique_vehicles"] * c.insurance_eur_per_vehicle_year,
        "App / platform": x["unique_vehicles"] * c.app_eur_per_vehicle_year,
        "Warehouse": c.warehouse_m2 * c.warehouse_eur_m2_month * 12,
        "Staff": c.staff_fte * c.staff_eur_per_fte,
    }
    variable = {
        "Energy": kwh * c.energy_eur_kwh,
        "Rebalancing & logistics": fleet * 365 * c.rebalancing_eur_per_vehicle_day,
        "Maintenance": fleet * c.maintenance_eur_per_vehicle_year,
        "Connectivity": fleet * 12 * c.connectivity_eur_per_vehicle_month,
    }
    revenue = rev_unlock + rev_min
    cost = sum(fixed.values()) + sum(variable.values())
    return {"revenue_unlock": rev_unlock, "revenue_minutes": rev_min, "revenue": revenue,
            **{f"fixed: {k}": v for k, v in fixed.items()}, **{f"variable: {k}": v for k, v in variable.items()},
            "total_cost": cost, "profit": revenue - cost, "margin_pct": 100 * (revenue - cost) / revenue,
            "fleet_used": fleet}


def break_even_tvd(op: str, x: dict, c: config.CostAssumptions = config.COSTS) -> float:
    """Trips per deployed vehicle-day needed for zero profit, holding revenue,
    energy per trip and all cost parameters at their point values."""
    base = pnl(op, x, c)
    rev_per_trip = base["revenue"] / x["trips"]
    energy_per_trip = base["variable: Energy"] / x["trips"]
    fixed_like = base["total_cost"] - base["variable: Energy"]
    trips_needed = fixed_like / (rev_per_trip - energy_per_trip)
    return trips_needed / (x["avg_deployed_fleet"] * 365)


def monte_carlo(op: str, x: dict, n: int = config.N_MONTE_CARLO) -> np.ndarray:
    g = np.random.default_rng(config.RANDOM_SEED)
    draws = {k: g.triangular(*v, size=n) for k, v in config.MC_RANGES.items()}
    out = np.empty(n)
    for i in range(n):
        c = config.CostAssumptions(**{k: draws[k][i] for k in draws if k != "per_min_factor"})
        out[i] = pnl(op, x, c, per_min_factor=draws["per_min_factor"][i])["profit"]
    return out


def run(trips: dict[str, pd.DataFrame], pooled_coefs: pd.Series) -> dict:
    P.set_style()
    inputs = {op: operating_inputs(op, t, pooled_coefs) for op, t in trips.items()}
    rows, mc_rows, sims = [], [], {}
    for op, x in inputs.items():
        for basis in ["unique", "deployed"]:
            r = pnl(op, x, fleet_basis=basis)
            rows.append({"operator": config.label(op), "fleet_basis": basis, **x, **r})
        sims[op] = monte_carlo(op, x)
        tvd_obs = x["trips"] / (x["avg_deployed_fleet"] * 365)
        mc_rows.append({"operator": config.label(op), "profit_p05": np.percentile(sims[op], 5),
                        "profit_median": np.median(sims[op]), "profit_p95": np.percentile(sims[op], 95),
                        "prob_profit_positive": (sims[op] > 0).mean(),
                        "observed_trips_per_deployed_vehicle_day": tvd_obs,
                        "break_even_trips_per_vehicle_day": break_even_tvd(op, x),
                        "revenue_per_trip": pnl(op, x)["revenue"] / x["trips"]})
    table = pd.DataFrame(rows)
    mc = pd.DataFrame(mc_rows)

    # figure 1: revenue vs cost composition (deployed basis)
    dep = table[table.fleet_basis == "deployed"].set_index("operator")
    cost_cols = [c for c in dep.columns if c.startswith(("fixed:", "variable:"))]
    fig, ax = plt.subplots(figsize=(9, 4))
    x = np.arange(len(dep))
    ax.bar(x - 0.2, dep["revenue"] / 1e6, width=0.38, color=P.SEQ_BLUE[3], label="Revenue")
    bottom = np.zeros(len(dep))
    shades = ["#3d3d3a", "#6b6a66", "#898781", "#a9a8a2", "#c3c2b7", "#eb6834", "#f3a6a0", "#f5c4a8", "#fde7dd"]
    for col, sh in zip(cost_cols, shades):
        v = dep[col].values / 1e6
        ax.bar(x + 0.2, v, width=0.38, bottom=bottom, color=sh, label=col.split(": ")[1], edgecolor="white", lw=0.5)
        bottom += v
    for i, (rev, prof) in enumerate(zip(dep["revenue"], dep["profit"])):
        ax.text(i, max(rev, dep["total_cost"].iloc[i]) / 1e6 + 0.08, f"{prof / 1e6:+.2f} M€", ha="center",
                fontsize=9, fontweight="bold")
    ax.set_xticks(x, dep.index)
    ax.set_ylabel("Million € per year (2024)")
    ax.legend(fontsize=7, ncol=2, loc="upper right")
    fig.tight_layout()
    P.save(fig, "finance_revenue_vs_costs")

    # figure 2: Monte Carlo distribution
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.4))
    for ax, (op, s) in zip(axes, sims.items()):
        ax.hist(s / 1e6, bins=60, color=P.op_color(op), alpha=0.85)
        ax.axvline(0, color=P.INK, lw=1)
        ax.set_title(f"{config.label(op)} — P(profit > 0) = {(s > 0).mean():.0%}", loc="left")
        ax.set_xlabel("Annual profit (M€)")
    axes[0].set_ylabel("Simulations")
    fig.tight_layout()
    P.save(fig, "finance_monte_carlo")

    # figure 3: profit as a function of utilisation
    fig, ax = plt.subplots(figsize=(7, 4))
    for op, xin in inputs.items():
        base = pnl(op, xin)
        rpt = base["revenue"] / xin["trips"] - base["variable: Energy"] / xin["trips"]
        fixed_like = base["total_cost"] - base["variable: Energy"]
        tvd = np.linspace(0.5, 6, 100)
        prof = (tvd * xin["avg_deployed_fleet"] * 365 * rpt - fixed_like) / 1e6
        ax.plot(tvd, prof, color=P.op_color(op), label=config.label(op))
        obs = xin["trips"] / (xin["avg_deployed_fleet"] * 365)
        ax.scatter([obs], [base["profit"] / 1e6], color=P.op_color(op), s=60, zorder=5, edgecolor="white", lw=1.5)
        ax.annotate(f"observed {obs:.2f}", (obs, base["profit"] / 1e6), textcoords="offset points", xytext=(6, -12),
                    fontsize=8, color=P.INK_2)
    ax.axhline(0, color=P.INK, lw=1)
    ax.set_xlabel("Trips per deployed vehicle per day")
    ax.set_ylabel("Annual profit (M€)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    P.save(fig, "finance_break_even")

    table.to_csv(config.TABLE_DIR / "finance_pnl.csv", index=False)
    mc.to_csv(config.TABLE_DIR / "finance_monte_carlo.csv", index=False)
    return {"pnl": table, "monte_carlo": mc, "inputs": inputs}
