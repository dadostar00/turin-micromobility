"""Statistical zones of Turin, zone attributes and point-in-zone assignment."""
from __future__ import annotations

from functools import lru_cache

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from . import config

ZONE_TO_ASC = {"09bis": 93, "17bis": 94}


@lru_cache(maxsize=1)
def load_zones() -> gpd.GeoDataFrame:
    """94 official statistical zones (ZONASTAT) in a metric CRS, with attributes.

    Attributes: name, area_km2, population and IDISE (ISTAT 2021, ASC level,
    which coincides with the statistical zones) and distance to the centre.
    """
    z = gpd.read_file(config.ZONES_FILE).to_crs(config.CRS_METRIC)
    z["zone"] = z["ZONASTAT"].astype(str)
    z = z.rename(columns={"DENOM": "name"})[["zone", "name", "geometry"]]
    z["area_km2"] = z.area / 1e6

    # ISTAT sub-municipal areas (ASC) coincide with the statistical zones;
    # the two "bis" zones are numbered 93 and 94 in the ISTAT table.
    z["asc"] = z["zone"].map(ZONE_TO_ASC).fillna(pd.to_numeric(z["zone"], errors="coerce")).astype(int)
    idise = pd.read_excel(config.IDISE_ASC_FILE)
    idise = idise[idise["COD_ASC"].notna() & ~idise["ASC"].astype(str).str.startswith("Sezione Fittizia")]
    idise["asc"] = (idise["COD_ASC"].astype(int) - 1272000).astype(int)
    idise = idise.rename(columns={"POP_TOT": "population"})
    z = z.merge(idise[["asc", "population", "IDISE"]], on="asc", how="left").drop(columns="asc")
    z["pop_density"] = z["population"] / z["area_km2"]

    centre = gpd.GeoSeries([Point(config.CITY_CENTRE_LONLAT)], crs=config.CRS_WGS84).to_crs(config.CRS_METRIC).iloc[0]
    z["dist_centre_km"] = z.geometry.centroid.distance(centre) / 1000
    return z.sort_values("zone").reset_index(drop=True)


def assign_zones(trips: pd.DataFrame) -> pd.DataFrame:
    """Add ``zone_o`` and ``zone_d`` (NaN if the point falls outside the city)."""
    zones = load_zones()[["zone", "geometry"]]
    out = trips.copy()
    for end, (lon, lat) in {"o": ("lon_o", "lat_o"), "d": ("lon_d", "lat_d")}.items():
        pts = gpd.GeoDataFrame(
            index=out.index,
            geometry=gpd.points_from_xy(out[lon], out[lat]),
            crs=config.CRS_WGS84,
        ).to_crs(config.CRS_METRIC)
        joined = gpd.sjoin(pts, zones, how="left", predicate="within")
        joined = joined[~joined.index.duplicated(keep="first")]
        out[f"zone_{end}"] = joined["zone"].reindex(out.index)
    return out


def city_boundary() -> gpd.GeoSeries:
    return gpd.GeoSeries([load_zones().union_all()], crs=config.CRS_METRIC)
