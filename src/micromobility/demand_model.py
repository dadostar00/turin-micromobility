"""Zonal demand model: what explains where e-scooter trips start?

Negative binomial (NB2) regression of annual trip origins per statistical zone
(all operators pooled) with log(area) as exposure offset, so coefficients refer
to trip *density*:

    log E[origins_i] = log(area_i) + b0 + b1 log(pop density) + b2 IDISE(z)
                       + b3 distance to centre + b4 log(PT passages/km2)
                       + b5 metro station in zone

Coefficients are reported as incidence-rate ratios (IRR) with 95% CIs.
The NB is preferred to Poisson because zonal counts are heavily
over-dispersed (tested with the dispersion parameter alpha). Residual spatial
autocorrelation is checked with Moran's I, and the Pearson residual map shows
zones that are over- or under-served relative to their characteristics.

This replaces the purely visual IDISE overlay of the original report with an
explicit test of the equity question: *conditional on density, centrality
and transit supply, do more deprived zones generate fewer trips?*
"""
from __future__ import annotations

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats as st
from statsmodels.stats.outliers_influence import variance_inflation_factor

from . import config, plotting as P, spatial
from .transit import day_supply, load_gtfs
from .zones import load_zones


def zone_table(indicators: dict[str, gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    z = load_zones().copy()
    origins = sum(g.set_index("zone")["origins"] for g in indicators.values())
    z["origins"] = z["zone"].map(origins).fillna(0)
    for op, g in indicators.items():
        z[f"origins_{op}"] = z["zone"].map(g.set_index("zone")["origins"]).fillna(0)

    g = load_gtfs()
    calls = day_supply(g, config.GTFS_REFERENCE_DAY)
    st_ = g["stops"].merge(calls.groupby("stop_id").agg(passages=("trip_id", "nunique"),
                                                        metro=("route_type", lambda s: (s == "1").any())),
                           on="stop_id")
    j = gpd.sjoin(st_, z[["zone", "geometry"]], predicate="within")
    z["pt_passages"] = z["zone"].map(j.groupby("zone")["passages"].sum()).fillna(0)
    z["metro"] = z["zone"].map(j.groupby("zone")["metro"].any()).fillna(False).astype(int)
    z["pt_density"] = z["pt_passages"] / z["area_km2"]
    z["trips_per_1000_inh_day"] = 1000 * z["origins"] / z["population"] / 366
    return z


def fit(z: pd.DataFrame):
    d = z[(z["population"] >= 500) & z["IDISE"].notna()].copy()
    d["log_pop_density"] = np.log(d["pop_density"])
    d["idise_z"] = (d["IDISE"] - d["IDISE"].mean()) / d["IDISE"].std()
    d["log_pt_density"] = np.log1p(d["pt_density"])
    X = sm.add_constant(d[["log_pop_density", "idise_z", "dist_centre_km", "log_pt_density", "metro"]])
    offset = np.log(d["area_km2"])
    nb = sm.NegativeBinomial(d["origins"], X, offset=offset).fit(disp=False, maxiter=500)
    pois = sm.GLM(d["origins"], X, family=sm.families.Poisson(), offset=offset).fit()
    alpha = nb.params["alpha"]
    glm = sm.GLM(d["origins"], X, family=sm.families.NegativeBinomial(alpha=alpha), offset=offset).fit()
    d["pearson_resid"] = glm.resid_pearson
    d["fitted"] = glm.fittedvalues
    vif = pd.Series([variance_inflation_factor(X.values, i) for i in range(1, X.shape[1])], index=X.columns[1:])
    return d, nb, pois, glm, vif


def fit_slx(d: pd.DataFrame):
    """Robustness check: SLX model adding spatially lagged covariates (W·X).

    Accounts for the influence of neighbouring zones (e.g. a dense or well
    served neighbour generating trips that start just across the boundary).
    """
    gd = gpd.GeoDataFrame(d.reset_index(drop=True), geometry="geometry", crs=config.CRS_METRIC)
    w = spatial.queen_weights(gd)
    cols = ["log_pop_density", "idise_z", "log_pt_density"]
    for c in cols:
        gd[f"W_{c}"] = w.sparse @ gd[c].values
    X = sm.add_constant(gd[["log_pop_density", "idise_z", "dist_centre_km", "log_pt_density", "metro"]
                           + [f"W_{c}" for c in cols]])
    m = sm.NegativeBinomial(gd["origins"], X, offset=np.log(gd["area_km2"])).fit(disp=False, maxiter=500)
    glm = sm.GLM(gd["origins"], X, family=sm.families.NegativeBinomial(alpha=m.params["alpha"]),
                 offset=np.log(gd["area_km2"])).fit()
    gd["resid_slx"] = glm.resid_pearson
    return m, spatial.moran(gd, "resid_slx")


def run(indicators: dict[str, gpd.GeoDataFrame]) -> dict:
    P.set_style()
    z = zone_table(indicators)
    d, nb, pois, glm, vif = fit(z)
    ci = nb.conf_int()
    coef = pd.DataFrame({
        "coef": nb.params, "se": nb.bse, "p_value": nb.pvalues,
        "IRR": np.exp(nb.params), "IRR_low": np.exp(ci[0]), "IRR_high": np.exp(ci[1]),
    }).drop(index="alpha")
    coef["VIF"] = vif.reindex(coef.index)
    labels = {"const": "Intercept", "log_pop_density": "log population density",
              "idise_z": "IDISE hardship index (1 SD)", "dist_centre_km": "Distance to centre (km)",
              "log_pt_density": "log(1 + PT passages per km²·day)", "metro": "Metro station in zone"}
    coef.index = coef.index.map(labels)

    zz = gpd.GeoDataFrame(z.merge(d[["zone", "pearson_resid"]], on="zone", how="left"), crs=z.crs)
    resid_moran = spatial.moran(zz, "pearson_resid")
    rho_raw = st.spearmanr(*z[["IDISE", "trips_per_1000_inh_day"]].dropna().T.values)
    rho_fit = st.spearmanr(d["origins"] / d["area_km2"], d["fitted"] / d["area_km2"])
    slx, slx_moran = fit_slx(d)
    slx_ci = slx.conf_int()
    slx_coef = pd.DataFrame({"coef": slx.params, "p_value": slx.pvalues, "IRR": np.exp(slx.params),
                             "IRR_low": np.exp(slx_ci[0]), "IRR_high": np.exp(slx_ci[1])}).drop(index="alpha")
    slx_coef.to_csv(config.TABLE_DIR / "demand_model_slx_coefficients.csv")
    fit_stats = pd.DataFrame([{
        "spearman_observed_vs_fitted_density": rho_fit.statistic,
        "slx_aic": slx.aic, "slx_idise_IRR": np.exp(slx.params["idise_z"]), "slx_idise_p": slx.pvalues["idise_z"],
        "slx_residual_moran_I": slx_moran["I"], "slx_residual_moran_p": slx_moran["p_perm"],
        "zones": len(d), "nb_alpha": nb.params["alpha"], "nb_llf": nb.llf, "nb_aic": nb.aic,
        "poisson_aic": pois.aic, "poisson_dispersion": pois.pearson_chi2 / pois.df_resid,
        "mcfadden_pseudo_r2": nb.prsquared, "residual_moran_I": resid_moran["I"],
        "residual_moran_p": resid_moran["p_perm"],
        "spearman_idise_vs_trips_per_capita": rho_raw.statistic, "spearman_p": rho_raw.pvalue,
    }])

    # maps: IDISE, trips per capita, model residuals
    fig, axes = P.map_grid(3)
    zz["IDISE_map"] = zz["IDISE"].where(zz["population"] >= 500)
    P.choropleth(axes[0], zz, "IDISE_map", bins=[95, 98, 99, 100, 101, 103, 106], fmt="{:.0f}",
                 legend_title="IDISE 2021\n(100 = Turin average)", title="Socio-economic hardship (ISTAT)",
                 cmap=plt.matplotlib.colors.LinearSegmentedColormap.from_list("h", ["#fde7dd", "#eb6834", "#7a2a0a"]))
    zz["tpc_map"] = zz["trips_per_1000_inh_day"].where(zz["population"] >= 500)
    v = zz["tpc_map"].dropna()
    P.choropleth(axes[1], zz, "tpc_map", bins=[0] + list(np.quantile(v, [0.2, 0.4, 0.6, 0.8])) + [v.max()],
                 fmt="{:.1f}", legend_title="Trip origins per\n1,000 residents per day", title="E-scooter use per resident")
    lim = np.nanmax(np.abs(zz["pearson_resid"]))
    P.choropleth(axes[2], zz, "pearson_resid", bins=[-lim, -1, -0.5, -0.2, 0.2, 0.5, 1, lim], cmap=P.DIV_CMAP,
                 fmt="{:+.1f}", legend_title="NB Pearson residual\n(+ more trips than predicted)",
                 title="Over/under-served zones (model residuals)", label_top=5)
    fig.tight_layout()
    P.save(fig, "map_demand_model_equity", config.MAP_DIR)

    # coefficient plot
    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    c = coef.drop(index="Intercept").iloc[::-1]
    y = np.arange(len(c))
    ax.errorbar(c["IRR"], y, xerr=[c["IRR"] - c["IRR_low"], c["IRR_high"] - c["IRR"]], fmt="o",
                color=P.SEQ_BLUE[4], ecolor=P.SEQ_BLUE[4], capsize=3, ms=6)
    ax.axvline(1, color=P.INK_2, lw=1)
    ax.set_yticks(y, c.index)
    ax.set_xscale("log")
    ax.set_xlabel("Incidence-rate ratio (95% CI, log scale)")
    for yi, (irr, p) in enumerate(zip(c["IRR"], c["p_value"])):
        ax.text(c["IRR_high"].iloc[yi] * 1.05, yi, f"{irr:.2f}{'*' if p < 0.05 else ''}", va="center", fontsize=8)
    fig.tight_layout()
    P.save(fig, "demand_model_irr")

    coef.to_csv(config.TABLE_DIR / "demand_model_coefficients.csv")
    fit_stats.to_csv(config.TABLE_DIR / "demand_model_fit.csv", index=False)
    z.drop(columns="geometry").to_csv(config.TABLE_DIR / "zone_attributes.csv", index=False)
    zz.to_file(config.GPKG_FILE, layer="demand_model", driver="GPKG")
    return {"coefficients": coef, "slx": slx_coef, "fit": fit_stats, "zones": zz}
