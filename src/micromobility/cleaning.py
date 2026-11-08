"""Trip validation with a sequential ("waterfall") cleaning log.

Filters are applied one after the other, so each removed trip is counted
exactly once and the rows of the log add up to the total number of removed
records (the original scripts counted every filter against the raw data,
which double-counts trips failing more than one rule).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pyproj import Geod

from . import config
from .io import load_raw

_GEOD = Geod(ellps="WGS84")


def geodesic_m(lon1, lat1, lon2, lat2) -> np.ndarray:
    """Vectorised WGS84 geodesic distance in metres."""
    _, _, d = _GEOD.inv(np.asarray(lon1), np.asarray(lat1), np.asarray(lon2), np.asarray(lat2))
    return d


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    ok = df[["lon_o", "lat_o", "lon_d", "lat_d"]].notna().all(axis=1)
    df["dist_m"] = np.nan
    df.loc[ok, "dist_m"] = geodesic_m(df.loc[ok, "lon_o"], df.loc[ok, "lat_o"],
                                      df.loc[ok, "lon_d"], df.loc[ok, "lat_d"])
    dur_h = df["duration_min"] / 60
    df["speed_kmh"] = (df["dist_m"] / 1000) / dur_h.where(dur_h > 0)
    df["month"] = df["start_time"].dt.month.fillna(df["month_hint"]).astype(int)
    df["date"] = df["start_time"].dt.normalize()
    df["hour"] = df["start_time"].dt.hour
    df["weekday"] = df["start_time"].dt.dayofweek  # 0 = Monday
    df["is_weekend"] = df["weekday"] >= 5
    df["batt_drop"] = df["batt_start"] - df["batt_end"]
    return df


def clean(df: pd.DataFrame, rules: config.CleaningRules = config.CLEANING) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (clean trips, cleaning log)."""
    df = add_derived(df)
    steps: list[tuple[str, pd.Series]] = []

    in_year = (df["start_time"].dt.year == config.YEAR) | (~df["has_timestamp"])
    steps.append(("Outside reference year", ~in_year))

    dup = df["has_timestamp"] & df.duplicated(subset=["vehicle_id", "start_time"], keep="first")
    steps.append(("Duplicated record (same vehicle and start time)", dup))

    (lat0, lat1), (lon0, lon1) = rules.lat_range, rules.lon_range
    in_box = (df["lat_o"].between(lat0, lat1) & df["lon_o"].between(lon0, lon1)
              & df["lat_d"].between(lat0, lat1) & df["lon_d"].between(lon0, lon1))
    steps.append(("Coordinates missing or outside metropolitan area", ~in_box))

    steps.append((f"Duration < {rules.min_duration_s:.0f} s", ~(df["duration_min"] * 60 >= rules.min_duration_s)))
    steps.append((f"Duration > {rules.max_duration_s / 3600:.0f} h", df["duration_min"] * 60 > rules.max_duration_s))
    steps.append((f"Straight-line distance < {rules.min_distance_m:.0f} m", df["dist_m"] < rules.min_distance_m))
    steps.append((f"Straight-line distance > {rules.max_distance_m / 1000:.0f} km", df["dist_m"] > rules.max_distance_m))
    steps.append((f"Speed > {rules.max_speed_kmh:.0f} km/h", df["speed_kmh"] > rules.max_speed_kmh))

    keep = pd.Series(True, index=df.index)
    rows = []
    n0 = len(df)
    for name, bad in steps:
        removed = keep & bad.fillna(False)
        rows.append({"step": name, "removed": int(removed.sum())})
        keep &= ~removed
    log = pd.DataFrame(rows)
    log["removed_pct_of_raw"] = 100 * log["removed"] / n0
    log = pd.concat([
        pd.DataFrame([{"step": "Raw records", "removed": n0, "removed_pct_of_raw": np.nan}]),
        log,
        pd.DataFrame([{"step": "Valid trips", "removed": int(keep.sum()),
                       "removed_pct_of_raw": 100 * keep.sum() / n0}]),
    ], ignore_index=True).rename(columns={"removed": "records"})
    return df[keep].reset_index(drop=True), log


def load_clean(op: str, use_cache: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load, harmonise and clean one operator (cached as parquet/pickle)."""
    config.ensure_dirs()
    trips_f = config.CACHE_DIR / f"trips_{op}.pkl"
    log_f = config.CACHE_DIR / f"cleaning_log_{op}.csv"
    if use_cache and trips_f.exists() and log_f.exists():
        return pd.read_pickle(trips_f), pd.read_csv(log_f)
    trips, log = clean(load_raw(op))
    trips["operator"] = op
    trips.to_pickle(trips_f)
    log.to_csv(log_f, index=False)
    return trips, log
