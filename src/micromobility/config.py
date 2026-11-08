"""Central configuration: paths, cleaning thresholds, time bands and cost assumptions.

Every number used by the pipeline lives here so that the analysis is
reproducible and assumptions are explicit and easy to change.

The raw data are *not* shipped with the repository. Point the environment
variable ``MICROMOBILITY_DATA_ROOT`` to the folder that contains
``DATI MONOPATTINI SHARING/``, ``from_Geoportale/`` and ``IDISE/``. By default
the parent folder of the repository is used.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("MICROMOBILITY_DATA_ROOT", REPO_ROOT.parent))

SHARING_DIR = DATA_ROOT / "DATI MONOPATTINI SHARING"
LIME_DIR = SHARING_DIR / "OPERATORE A - Lime" / "Dati" / "Torino_Corse24-25_MENSILI_senza_percorso" / "2024"
VOI_DIR = SHARING_DIR / "OPERATORE B - Voi" / "Dati" / "2024"
BIRD_FILE = SHARING_DIR / "OPERATORE C - Bird" / "Dati" / "Bird Torino - 2024.csv"
GTFS_DIR = SHARING_DIR / "TPL" / "gtt_gtfs"

ZONES_FILE = DATA_ROOT / "from_Geoportale" / "zone_statistiche_geo" / "zone_statistiche_geo.shp"
IDISE_ASC_FILE = DATA_ROOT / "IDISE" / "DATI" / "Torino_IDISE_2021" / "Torino_IDISE_2021_ASC.xlsx"

OUTPUT_DIR = Path(os.environ.get("MICROMOBILITY_OUTPUT_DIR", REPO_ROOT / "outputs"))
FIG_DIR = OUTPUT_DIR / "figures"
MAP_DIR = OUTPUT_DIR / "maps"
TABLE_DIR = OUTPUT_DIR / "tables"
CACHE_DIR = OUTPUT_DIR / "cache"
GPKG_FILE = OUTPUT_DIR / "zonal_indicators.gpkg"

YEAR = 2024
CRS_WGS84 = "EPSG:4326"
CRS_METRIC = "EPSG:32632"  # UTM 32N, metres

# Reference point for the city centre (Piazza Castello)
CITY_CENTRE_LONLAT = (7.6858, 45.0712)

# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
OPERATORS = {
    "A": "Lime",
    "B": "Voi",
    "C": "Bird",
}
OPERATOR_COLORS = {"A": "#2a78d6", "B": "#eb6834", "C": "#1baf7a"}  # validated categorical slots 1-3


def label(op: str) -> str:
    """Human-readable operator label, e.g. ``'A - Lime'``."""
    return f"{op} - {OPERATORS[op]}"


# ---------------------------------------------------------------------------
# Cleaning thresholds (identical for every operator)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CleaningRules:
    min_duration_s: float = 45
    max_duration_s: float = 3 * 3600
    min_distance_m: float = 120
    max_distance_m: float = 15_000
    max_speed_kmh: float = 35
    # Bounding box of the Metropolitan City of Turin
    lat_range: tuple = (44.80, 45.40)
    lon_range: tuple = (7.30, 8.00)


CLEANING = CleaningRules()

# ---------------------------------------------------------------------------
# Time bands used for OD, net flow, tidal and dwell-time analyses (working
# days only). After converting Voi timestamps from UTC, the three operators
# share the same working-day profile: AM peak at 08:00-08:59, PM peak at
# 17:00-18:59 (maximum at 18), a trough at 10:00-11:59 and a night minimum.
# The same bands are used for every operator so results are comparable.
# ---------------------------------------------------------------------------
TIME_BANDS = {
    "AM peak 08-09": [8],
    "Inter-peak 10-12": [10, 11],
    "PM peak 17-19": [17, 18],
    "Night 23-05": [23, 0, 1, 2, 3, 4],
}
AM_BAND = "AM peak 08-09"
PM_BAND = "PM peak 17-19"

# ---------------------------------------------------------------------------
# Statistical settings
# ---------------------------------------------------------------------------
ALPHA = 0.05
N_BOOT = 1000
N_MONTE_CARLO = 20_000
RANDOM_SEED = 42
MIN_EVENTS_PER_ZONE = 30  # zones with fewer events are not mapped/tested

# ---------------------------------------------------------------------------
# GTFS reference day.
# The only GTT feed available (valid 2025-12-22 .. 2026-12-31) contains the
# complete urban timetable only until 2026-02-28 (~10,500 urban trips per
# weekday); from March onwards it holds 775 placeholder trips per day. A summer
# timetable is therefore NOT available, and the feed is used through a single
# representative winter school-period weekday as a proxy of the 2024 supply.
# ---------------------------------------------------------------------------
GTFS_REFERENCE_DAY = "20260114"  # Wednesday
STOP_BUFFER_M = 250
HEX_SIZE_M = 250

# ---------------------------------------------------------------------------
# Financial model assumptions (point values + ranges for the Monte Carlo)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Tariff:
    unlock_eur: float
    per_min_eur: float


TARIFFS = {
    "A": Tariff(1.00, 0.26),
    "B": Tariff(1.00, 0.24),
    "C": Tariff(1.00, 0.26),
}


@dataclass(frozen=True)
class CostAssumptions:
    vehicle_price_eur: float = 650.0
    vehicle_life_years: float = 2.5
    insurance_eur_per_vehicle_year: float = 10.0
    app_eur_per_vehicle_year: float = 40.0
    warehouse_m2: float = 1200.0
    warehouse_eur_m2_month: float = 6.0
    staff_fte: float = 4.0
    staff_eur_per_fte: float = 45_000.0
    battery_kwh: float = 0.46
    energy_eur_kwh: float = 0.25
    charging_loss: float = 0.12
    rebalancing_eur_per_vehicle_day: float = 2.0
    maintenance_eur_per_vehicle_year: float = 200.0
    connectivity_eur_per_vehicle_month: float = 1.2
    # share of paying minutes actually billed (discounts, passes, promo codes)
    revenue_realisation: float = 1.0


COSTS = CostAssumptions()

# Triangular (low, mode, high) ranges for the Monte Carlo sensitivity analysis
MC_RANGES = {
    "per_min_factor": (0.70, 0.85, 1.00),  # passes/discounts reduce the list price
    "rebalancing_eur_per_vehicle_day": (1.0, 2.0, 3.0),
    "maintenance_eur_per_vehicle_year": (100.0, 200.0, 350.0),
    "vehicle_price_eur": (450.0, 650.0, 850.0),
    "vehicle_life_years": (1.5, 2.5, 3.5),
    "staff_eur_per_fte": (35_000.0, 45_000.0, 55_000.0),
}


@dataclass
class Paths:
    """Convenience container created by :func:`ensure_dirs`."""

    figures: Path = FIG_DIR
    maps: Path = MAP_DIR
    tables: Path = TABLE_DIR
    cache: Path = CACHE_DIR
    extra: dict = field(default_factory=dict)


def ensure_dirs() -> Paths:
    for d in (FIG_DIR, MAP_DIR, TABLE_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    return Paths()
