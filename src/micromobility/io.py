"""Loaders that turn each operator's raw files into one harmonised schema.

Harmonised columns
------------------
operator, vehicle_id, start_time, end_time, lat_o, lon_o, lat_d, lon_d,
duration_min, reported_km, reserved, batt_start, batt_end, has_timestamp

``has_timestamp`` is False for records whose start time is unknown (Voi,
January 2024: every record is stamped ``2024-01-00 00:00:00``). Those trips are
kept for volume/financial totals but excluded from every time-of-day analysis.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import config

log = logging.getLogger(__name__)

COLUMNS = [
    "operator", "vehicle_id", "start_time", "end_time",
    "lat_o", "lon_o", "lat_d", "lon_d",
    "duration_min", "reported_km", "reserved",
    "batt_start", "batt_end", "has_timestamp",
]

LOCAL_TZ = "Europe/Rome"
_YES = {"si", "sì", "s�", "yes", "true", "1", "y"}


def _to_bool(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().isin(_YES)


# ---------------------------------------------------------------------------
# Operator A - Lime: monthly CSV files, second-level timestamps
# ---------------------------------------------------------------------------
def load_lime(folder: Path = config.LIME_DIR) -> pd.DataFrame:
    files = sorted(folder.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No Lime CSV files in {folder}")
    df = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
    start = pd.to_datetime(df["DATAORA_INIZIO"], errors="coerce")
    end = pd.to_datetime(df["DATAORA_FINE"], errors="coerce", format="mixed")
    out = pd.DataFrame({
        "operator": "A",
        "vehicle_id": df["ID_VEICOLO"].astype(str),
        "start_time": start,
        "end_time": end,
        "lat_o": df["LATITUDINE_INIZIO_CORSA"],
        "lon_o": df["LONGITUTIDE_INIZIO_CORSA"],  # sic: typo in the source
        "lat_d": df["LATITUDINE_FINE_CORSA"],
        "lon_d": df["LONGITUTIDE_FINE_CORSA"],
        # timestamps have second resolution, DURATA_MIN is rounded to minutes
        "duration_min": (end - start).dt.total_seconds() / 60,
        "reported_km": df["DISTANZA_KM"].astype(float),
        "reserved": _to_bool(df["RISERVATO"]),
        "batt_start": df["BATTERIA_INIZIO_CORSA"].astype(float),
        "batt_end": df["BATTERIA_FINE_CORSA"].astype(float),
        "has_timestamp": start.notna(),
    })
    return out[COLUMNS]


# ---------------------------------------------------------------------------
# Operator B - Voi: monthly Excel files, timestamps as YYYYMMDDhhmmss integers
# ---------------------------------------------------------------------------
def _parse_voi_ts(s: pd.Series) -> pd.Series:
    """Parse ``YYYYMMDDhhmmss`` integers given in UTC and return local naive time.

    The Voi export is in UTC: cross-correlating its hourly profile with the
    other operators gives a lag of exactly +1 h in winter months and +2 h
    during daylight-saving time (see ``scripts/check_time_offsets.py``).
    """
    txt = s.apply(lambda x: format(x, ".0f") if pd.notna(x) else "").str.zfill(14)
    utc = pd.to_datetime(txt, format="%Y%m%d%H%M%S", errors="coerce")
    return utc.dt.tz_localize("UTC").dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)


def load_voi(folder: Path = config.VOI_DIR) -> pd.DataFrame:
    files = sorted(folder.glob("DATINOLEGGI_2024*.xlsx"))
    if not files:
        raise FileNotFoundError(f"No Voi Excel files in {folder}")
    raw = pd.concat((pd.read_excel(f) for f in files), ignore_index=True)
    n_raw = len(raw)
    # The export repeats the same rental (same UUID, same values) several times.
    raw = raw.drop_duplicates(subset="Identificativo noleggio", keep="first")
    log.info("Voi: %d raw rows, %d unique rentals", n_raw, len(raw))

    start = _parse_voi_ts(raw["Data inizio corsa"])
    end = _parse_voi_ts(raw["Data fine corsa"])
    # January 2024 is exported as 20240100000000 (day 00) -> unparsable -> NaT
    month_from_text = raw["Data inizio corsa"].apply(lambda x: format(x, ".0f")[4:6]).astype(int)

    def _num(col):
        s = raw[col]
        return s.astype(str).str.replace(",", ".").astype(float) if s.dtype == object else s.astype(float)

    out = pd.DataFrame({
        "operator": "B",
        "vehicle_id": raw["Targa veicolo"].astype(str),
        "start_time": start,
        "end_time": end,
        "lat_o": _num("Lat inizio corsa_coordinate"),
        "lon_o": _num("Lon inizio corsa_coordinate"),
        "lat_d": _num("Lat fine corsa_coordinate"),
        "lon_d": _num("Lon fine corsa_coordinate"),
        "duration_min": raw["Tempo Tot"].astype(float),  # available also for January
        "reported_km": raw["KM Tot"].astype(float),
        "reserved": _to_bool(raw["Prenotazione"]),
        "batt_start": raw["Batteria inizio"].astype(float),
        "batt_end": raw["Batteria fine"].astype(float),
        "has_timestamp": start.notna(),
    })
    out["month_hint"] = month_from_text.values  # keeps January in monthly totals
    return out[COLUMNS + ["month_hint"]]


# ---------------------------------------------------------------------------
# Operator C - Bird: one annual CSV, minute-level timestamps "2024-1-1, 1:00"
# ---------------------------------------------------------------------------
def load_bird(path: Path = config.BIRD_FILE) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    fmt = "%Y-%m-%d, %H:%M"
    start = pd.to_datetime(df["DATAORA_INIZIO"], format=fmt, errors="coerce")
    end = pd.to_datetime(df["DATAORA_FINE"], format=fmt, errors="coerce")
    out = pd.DataFrame({
        "operator": "C",
        "vehicle_id": df["ID_VEICOLO"].astype(str),
        "start_time": start,
        "end_time": end,
        "lat_o": df["LATITUDINE_INIZIO_CORSA"],
        "lon_o": df["LONGITUTIDE_INIZIO_CORSA"],
        "lat_d": df["LATITUDINE_FINE_CORSA"],
        "lon_d": df["LONGITUTIDE_FINE_CORSA"],
        # timestamps are rounded to the minute; DURATA_MIN has 2 decimals
        "duration_min": df["DURATA_MIN"].astype(float),
        "reported_km": df["DISTANZA_KM"].astype(float),
        "reserved": _to_bool(df["RISERVATO"]),
        "batt_start": np.nan,
        "batt_end": np.nan,
        "has_timestamp": start.notna(),
    })
    return out[COLUMNS]


LOADERS = {"A": load_lime, "B": load_voi, "C": load_bird}


def load_raw(op: str) -> pd.DataFrame:
    """Load raw (harmonised but uncleaned) trips of one operator."""
    df = LOADERS[op]()
    if "month_hint" not in df:
        df["month_hint"] = df["start_time"].dt.month
    return df
