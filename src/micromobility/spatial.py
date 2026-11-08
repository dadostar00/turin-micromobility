"""Spatial autocorrelation: global Moran's I and local LISA clusters."""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from esda.moran import Moran, Moran_Local
from libpysal.weights import Queen

from . import config

LISA_LABELS = {1: "High-High", 2: "Low-High", 3: "Low-Low", 4: "High-Low"}


def queen_weights(gdf):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        w = Queen.from_dataframe(gdf, use_index=False)
    w.transform = "r"
    return w


def moran(gdf, column: str, permutations: int = 999) -> dict:
    """Global Moran's I on the zones with non-missing values."""
    sub = gdf[gdf[column].notna()].reset_index(drop=True)
    w = queen_weights(sub)
    np.random.seed(config.RANDOM_SEED)
    m = Moran(sub[column].values, w, permutations=permutations)
    return {"variable": column, "n": len(sub), "I": m.I, "E[I]": m.EI, "z": m.z_sim, "p_perm": m.p_sim}


def lisa(gdf, column: str, alpha: float = config.ALPHA, permutations: int = 999) -> pd.Series:
    """LISA cluster label per zone (conditional permutation, pseudo p < alpha)."""
    sub = gdf[gdf[column].notna()]
    w = queen_weights(sub.reset_index(drop=True))
    ml = Moran_Local(sub[column].values, w, permutations=permutations, seed=config.RANDOM_SEED)
    lab = np.where(ml.p_sim < alpha, pd.Series(ml.q).map(LISA_LABELS).values, "Not significant")
    return pd.Series(lab, index=sub.index).reindex(gdf.index)
