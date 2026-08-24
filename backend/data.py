"""
HeatROI — data layer.

Loads the build artifact into a pandas DataFrame (plus geometry kept aside for the API) and
validates it on every load. Depends only on pandas + numpy: no geopandas, no shapely, no GDAL.

Fails loudly. A silent bad load is how the previous prototype ended up serving mock data.
"""

import json
import os
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ARTIFACT = os.path.join(HERE, "data", "aoi_blockgroups.geojson")

REQUIRED = [
    "GEOID", "ua_name", "acs_pop", "land_area", "treecanopy", "tc_goal", "tc_gap",
    "temp_diff", "pctpocnorm", "pctpovnorm", "unemplnorm", "depratnorm", "lingnorm",
    "health_nor",
]

# Pre-stated plausible ranges. Violations raise -- they indicate a bad join or a unit error,
# both of which are silent failures otherwise.
RANGES = {
    "acs_pop": (0.0, 50_000.0),
    "land_area": (0.0001, 100.0),      # km^2
    "treecanopy": (0.0, 1.0),
    "tc_goal": (0.0, 1.0),
    "tc_gap": (0.0, 1.0),
    "pctpocnorm": (0.0, 1.0),
    "pctpovnorm": (0.0, 1.0),
    "unemplnorm": (0.0, 1.0),
    "depratnorm": (0.0, 1.0),
    "lingnorm": (0.0, 1.0),
    "health_nor": (0.0, 1.0),
}


class DataError(RuntimeError):
    pass


def load(path: str = ARTIFACT) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Returns (df, geometry_by_geoid).

    df is indexed by GEOID. geometry_by_geoid maps GEOID -> GeoJSON geometry dict, kept out of
    the DataFrame so all numeric work stays vectorised.
    """
    if not os.path.exists(path):
        raise DataError(
            "Artifact not found: %s\nRun:  python3 build/build_aoi.py" % path)

    with open(path) as fh:
        fc = json.load(fh)

    feats = fc.get("features") or []
    if not feats:
        raise DataError("Artifact contains no features: %s" % path)

    rows, geoms = [], {}
    for f in feats:
        p = dict(f["properties"])
        gid = str(p["GEOID"])
        geoms[gid] = f["geometry"]
        rows.append(p)

    df = pd.DataFrame(rows)
    df["GEOID"] = df["GEOID"].astype(str)
    df = df.set_index("GEOID", drop=False)

    _validate(df)
    return df, geoms


def _validate(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise DataError("Artifact missing required columns: %s" % missing)

    nulls = {c: int(df[c].isna().sum()) for c in REQUIRED if df[c].isna().any()}
    if nulls:
        raise DataError(
            "Nulls in load-bearing columns: %s. Never fill these silently -- fix the build."
            % nulls)

    if df.index.duplicated().any():
        dupes = df.index[df.index.duplicated()].tolist()
        raise DataError("Duplicate GEOIDs: %s" % dupes[:5])

    bad_len = df.loc[df["GEOID"].str.len() != 12, "GEOID"].tolist()
    if bad_len:
        raise DataError("GEOIDs are not 12-char block groups: %s" % bad_len[:5])

    for c, (lo, hi) in RANGES.items():
        if c not in df.columns:
            continue
        off = df.loc[(df[c] < lo) | (df[c] > hi), c]
        if len(off):
            raise DataError(
                "%s outside plausible range [%s, %s]: %d rows, e.g. %s"
                % (c, lo, hi, len(off), off.head(3).to_dict()))

    # Scoring is only valid within a single urban area (temp_norm and 5 of 6 equity indices are
    # rescaled per urban area). Enforce it structurally rather than trusting the caller.
    uas = df["ua_name"].dropna().unique().tolist()
    if len(uas) != 1:
        raise DataError(
            "Artifact spans %d urban areas (%s). Scoring across urban areas is invalid because "
            "temp_norm and 5 of the 6 equity indices are min-max rescaled within each. Filter "
            "to one urban area at build time." % (len(uas), uas[:4]))

    # The TES identity must survive any join.
    err = float(np.abs(df["tc_gap"] - np.maximum(0.0, df["tc_goal"] - df["treecanopy"])).max())
    if err > 1e-9:
        raise DataError(
            "tc_gap identity broken (max err %.3e). tc_gap must equal "
            "max(0, tc_goal - treecanopy); a mismatch means the join corrupted attributes." % err)


def summary(df: pd.DataFrame) -> Dict[str, Any]:
    """Descriptive facts about the loaded AOI. All Measured."""
    elig = int((df["tc_gap"] > 0).sum())
    return {
        "urban_area": df["ua_name"].iloc[0],
        "block_groups": int(len(df)),
        "population": int(df["acs_pop"].sum()),
        "land_area_km2": round(float(df["land_area"].sum()), 3),
        "mean_canopy": round(float(df["treecanopy"].mean()), 4),
        "mean_canopy_goal": round(float(df["tc_goal"].mean()), 4),
        "mean_tc_gap": round(float(df["tc_gap"].mean()), 4),
        "eligible_block_groups": elig,
        "eligible_share": round(elig / len(df), 4),
        "zero_population_block_groups": int((df["acs_pop"] == 0).sum()),
        "temp_diff_min": round(float(df["temp_diff"].min()), 2),
        "temp_diff_max": round(float(df["temp_diff"].max()), 2),
        "temp_diff_unit": "UNVERIFIED - do not render as a temperature",
        "fortyguard_temp_c_min": (round(float(df["fg_temp_c_mean"].min()), 4)
                                  if "fg_temp_c_mean" in df else None),
        "fortyguard_temp_c_max": (round(float(df["fg_temp_c_mean"].max()), 4)
                                  if "fg_temp_c_mean" in df else None),
        "provenance": "Measured (TES/ACS + FortyGuard API response)",
    }


if __name__ == "__main__":
    df, geoms = load()
    print("loaded %d block groups, %d geometries" % (len(df), len(geoms)))
    print()
    for k, v in summary(df).items():
        print("  %-32s %s" % (k, v))
