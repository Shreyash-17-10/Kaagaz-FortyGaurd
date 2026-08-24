#!/usr/bin/env python3
"""
HeatROI — single-file urban-heat tree-planting ROI pipeline.
============================================================

Combines three data sources to rank *where planting trees delivers the most
benefit to the most heat-vulnerable people*:

  * FortyGuard API        — modeled urban heat over an Area of Interest
  * WRI Cities Data API   — tree-planting "opportunity" layer
  * American Forests      — Tree Equity Score + demographics (data/ga_tree_equity.xlsx)

Everything below used to be spread across FortyGuard_Heatmap_Snapshot.py, WRI.py,
download_raster.py, Unified_grid.py, config.py and run_pipeline.py. It is now one
file. The fixes vs. the original:

  * no hard-coded API key (read from FORTYGUARD_API_KEY env var)
  * bounded polling (was an infinite loop); saves the discarded stats_data
  * WRI download uses the real endpoint GET /layers/{layer_id}/{city_id}
    (the old download_url/url/s3_url keys exist on none of the 355 indicators)
  * the heat x opportunity join actually runs (was silently defaulting to 0.0)
  * the missing ROI scoring is implemented and is the primary deliverable

Usage
-----
    python3 heatroi.py score            # ROI scoring only  (needs pandas/numpy/openpyxl)
    python3 heatroi.py city [Name]      # WRI city-id lookup (needs requests + network)
    python3 heatroi.py fetch            # FortyGuard snapshot (needs FORTYGUARD_API_KEY)
    python3 heatroi.py opportunity      # download WRI opportunity layer
    python3 heatroi.py unify            # build heat x opportunity grid (needs geopandas)
    python3 heatroi.py all              # whole pipeline, skipping unavailable stages

Heavy/optional deps (requests, geopandas, rasterstats) are imported lazily, so
`score` runs with just pandas + numpy + openpyxl.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# =========================================================================== #
# 1. CONFIGURATION                                                            #
# =========================================================================== #

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# Files
SNAPSHOT_GEOJSON = DATA_DIR / "fortyguard_snapshot.geojson"
SNAPSHOT_STATS = DATA_DIR / "fortyguard_stats.json"
OPPORTUNITY_PATH = DATA_DIR / "wri_tree_opportunity.geojson"
OPPORTUNITY_RASTER = DATA_DIR / "wri_tree_opportunity.tif"
UNIFIED_GRID = DATA_DIR / "final_analysis_grid.geojson"
EQUITY_XLSX = DATA_DIR / "ga_tree_equity.xlsx"
EQUITY_SHEET = "Sheet 1 - ga_tes"
ROI_SCORES_CSV = DATA_DIR / "heat_roi_scores.csv"
ROI_TOP_CSV = DATA_DIR / "heat_roi_top50.csv"

# Secret (never hard-code; export FORTYGUARD_API_KEY=...)
FORTYGUARD_API_KEY = os.environ.get("FORTYGUARD_API_KEY", "")

# FortyGuard
FORTYGUARD_BASE = "https://api.fortyguard.com/v1"
AOI_POLYGON = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [-84.415, 33.745], [-84.375, 33.745], [-84.375, 33.775],
            [-84.415, 33.775], [-84.415, 33.745],
        ]],
    },
}
HEATMAP_REQUEST = {
    "start_date": "2024-07-15", "start_time": "14:00", "filter_type": 1,
    "granularity": 100, "analytic_type": "tcm",
}
POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 60

# WRI
WRI_API = "https://cities-data-api.wri.org"
WRI_APPLICATION_ID = "ccl"
WRI_CITY_ID = "USA-Atlanta"
WRI_OPPORTUNITY_LAYER_ID = "opportunity__trees__all-plantable"

# ROI scoring weights
NEED_HEAT_WEIGHT = 0.5
NEED_VULN_WEIGHT = 0.5
OPPORTUNITY_MODE = "multiply"        # "multiply" (true ROI) | "add"
OPPORTUNITY_ADD_WEIGHT = 0.5         # used only when OPPORTUNITY_MODE == "add"
REDLINE_BOOST = 0.0                  # e.g. 0.10 -> +10% for HOLC C/D block groups
VULNERABILITY_COLUMNS = [
    "pctpocnorm", "pctpovnorm", "unemplnorm", "depratnorm", "lingnorm", "health_nor",
]
TIER_THRESHOLDS = [("Critical", 90), ("High", 70), ("Moderate", 40), ("Low", 0)]


# =========================================================================== #
# 2. STAGE 1 — FortyGuard heat snapshot                                       #
# =========================================================================== #

def _fg_headers() -> dict:
    if not FORTYGUARD_API_KEY:
        sys.exit("❌ FORTYGUARD_API_KEY is not set. `export FORTYGUARD_API_KEY=...` first.")
    return {"api-key": FORTYGUARD_API_KEY, "Content-Type": "application/json"}


def _extract_activity_id(data):
    if not isinstance(data, dict):
        return None
    candidates = ("activity_id", "activityId", "id", "job_id", "jobId")
    for key in candidates:
        if data.get(key):
            return data[key]
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in candidates:
            if nested.get(key):
                return nested[key]
    return None


def _extract_status(payload):
    return (payload.get("status") or payload.get("state")
            or payload.get("data", {}).get("status"))


def fetch_heat_snapshot():
    """Submit the AOI, poll (with a timeout), save the response + stats_data."""
    import requests  # lazy

    headers = _fg_headers()
    r = HEATMAP_REQUEST
    payload = {
        "polygon_aoi": AOI_POLYGON,
        "date_time": {"start_date": r["start_date"], "filter_type": r["filter_type"],
                      "start_time": r["start_time"]},
        "granularity": r["granularity"], "analytic_type": r["analytic_type"],
    }

    print("Submitting heatmap request to FortyGuard...")
    init = requests.post(f"{FORTYGUARD_BASE}/heatmap", json=payload,
                         headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
    init.raise_for_status()
    activity_id = _extract_activity_id(init.json())
    if not activity_id:
        print("❌ Could not find an activity id:", json.dumps(init.json())[:500])
        return None
    print(f"Activity created. ID: {activity_id}")

    status_url = f"{FORTYGUARD_BASE}/status/{activity_id}"
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while True:
        resp = requests.get(status_url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        status = str(_extract_status(data)).lower()

        if status in ("completed", "complete", "success"):
            print("✅ Heatmap analysis complete!")
            _save_snapshot(data)
            return data
        if status in ("failed", "error"):
            print(f"❌ Analysis failed: {json.dumps(data)[:500]}")
            return None
        if time.monotonic() >= deadline:
            print(f"❌ Timed out after {POLL_TIMEOUT_SECONDS}s (last status: {status}).")
            return None
        print(f"Status: {status}. Waiting {POLL_INTERVAL_SECONDS}s...")
        time.sleep(POLL_INTERVAL_SECONDS)


def _save_snapshot(data: dict) -> None:
    with open(SNAPSHOT_GEOJSON, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved snapshot -> {SNAPSHOT_GEOJSON}")
    result = data.get("result") or data.get("data") or data
    stats = result.get("stats_data") if isinstance(result, dict) else None
    if stats:
        with open(SNAPSHOT_STATS, "w") as f:
            json.dump(stats, f, indent=2)
        print(f"Saved temperature stats -> {SNAPSHOT_STATS}")


# =========================================================================== #
# 3. STAGE 2a — WRI city-id lookup                                            #
# =========================================================================== #

def get_wri_city_id(city_name: str = "Atlanta"):
    import requests  # lazy

    print(f"Querying WRI API for {city_name}...")
    resp = requests.get(f"{WRI_API}/cities",
                        params={"application_id": WRI_APPLICATION_ID},
                        timeout=HTTP_TIMEOUT_SECONDS)
    resp.raise_for_status()
    cities = resp.json()
    if isinstance(cities, dict):
        cities = cities.get("data") or cities.get("cities") or list(cities.keys())
    for city in cities:
        if isinstance(city, dict):
            name, city_id = city.get("name", ""), city.get("id", city.get("name", ""))
        else:
            name = city_id = str(city)
        if city_name.lower() in str(name).lower():
            print(f"✅ Found {name}  (id: {city_id})")
            return city_id
    print(f"❌ {city_name} not found in the Cool Cities Lab database.")
    return None


# =========================================================================== #
# 4. STAGE 2b — WRI tree-opportunity layer download                           #
# =========================================================================== #

_RASTER_URL_KEYS = ("cog_url", "raster_url", "tif_url", "download_url", "s3_url", "url", "href")


def _looks_like_geojson(obj) -> bool:
    return isinstance(obj, dict) and (
        obj.get("type") in ("FeatureCollection", "Feature") or "features" in obj)


def _find_raster_url(obj):
    if isinstance(obj, str):
        low = obj.lower()
        return obj if (low.startswith("http") and (".tif" in low or "cog" in low)) else None
    if isinstance(obj, dict):
        for key in _RASTER_URL_KEYS:
            val = obj.get(key)
            if isinstance(val, str) and val.lower().startswith("http"):
                return val
        for val in obj.values():
            found = _find_raster_url(val)
            if found:
                return found
    if isinstance(obj, list):
        for val in obj:
            found = _find_raster_url(val)
            if found:
                return found
    return None


def download_opportunity_layer(city_id=None, layer_id=None, year=None):
    """GET /layers/{layer_id}/{city_id}; save GeoJSON or follow to a raster."""
    import requests  # lazy

    city_id = city_id or WRI_CITY_ID
    layer_id = layer_id or WRI_OPPORTUNITY_LAYER_ID
    url = f"{WRI_API}/layers/{layer_id}/{city_id}"
    params = {"year": year} if year is not None else {}

    print(f"Fetching layer '{layer_id}' for {city_id}...")
    res = requests.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
    res.raise_for_status()

    try:
        body = res.json()
    except ValueError:
        OPPORTUNITY_RASTER.write_bytes(res.content)
        print(f"✅ Saved raster -> {OPPORTUNITY_RASTER}")
        return OPPORTUNITY_RASTER

    if _looks_like_geojson(body):
        with open(OPPORTUNITY_PATH, "w") as f:
            json.dump(body, f)
        print(f"✅ Saved GeoJSON opportunity layer "
              f"({len(body.get('features', []))} features) -> {OPPORTUNITY_PATH}")
        return OPPORTUNITY_PATH

    raster_url = _find_raster_url(body)
    if raster_url:
        print(f"Layer points at a raster; downloading {raster_url}...")
        with requests.get(raster_url, stream=True, timeout=HTTP_TIMEOUT_SECONDS) as rr:
            rr.raise_for_status()
            with open(OPPORTUNITY_RASTER, "wb") as f:
                for chunk in rr.iter_content(chunk_size=8192):
                    f.write(chunk)
        print(f"✅ Saved raster -> {OPPORTUNITY_RASTER}")
        return OPPORTUNITY_RASTER

    print("⚠️ Unrecognised layer response:", json.dumps(body, indent=2)[:800])
    return None


# =========================================================================== #
# 5. STAGE 3 — Unified heat x opportunity grid (needs geopandas/rasterstats)  #
# =========================================================================== #

def _load_spatial_data(filepath):
    import geopandas as gpd  # lazy

    with open(filepath, "r") as f:
        data = json.load(f)
    if not data:
        raise ValueError(f"'{filepath}' is empty or null.")
    if isinstance(data, dict):
        for key in ("result", "data"):
            container = data.get(key)
            if isinstance(container, dict) and "map_data" in container:
                data = container["map_data"]
                break
    if isinstance(data, dict) and (data.get("type") in ("FeatureCollection", "Feature")
                                   or "features" in data):
        return gpd.GeoDataFrame.from_features(data["features"], crs="EPSG:4326")
    raise ValueError(f"Unrecognised data format in {filepath}. Sample: {str(data)[:200]}")


def _detect_value_field(gdf):
    preferred = ("opportunity", "value", "plantable", "tree", "percent", "pct", "score")
    numeric = [c for c in gdf.columns if c != "geometry" and gdf[c].dtype.kind in "fiu"]
    for key in preferred:
        for col in numeric:
            if key in col.lower():
                return col
    return numeric[0] if numeric else None


def _attach_from_vector(grid, opp_path):
    import geopandas as gpd  # lazy

    opp = gpd.read_file(opp_path).to_crs("EPSG:4326")
    field = _detect_value_field(opp)
    if field is None:
        print("⚠️ No numeric value field in opportunity layer; using 0.0.")
        grid["tree_opportunity"] = 0.0
        return grid
    print(f"Overlaying opportunity layer on field '{field}' (area-weighted)...")
    ea = "EPSG:5070"  # equal-area for meaningful intersection areas
    grid_ea = grid.to_crs(ea).reset_index(names="_tile_ix")
    opp_ea = opp.to_crs(ea)[[field, "geometry"]]
    pieces = gpd.overlay(grid_ea, opp_ea, how="intersection")
    pieces["_w"] = pieces.geometry.area
    pieces["_wv"] = pieces["_w"] * pieces[field]
    agg = pieces.groupby("_tile_ix").agg(_wv=("_wv", "sum"), _w=("_w", "sum"))
    agg["tree_opportunity"] = agg["_wv"] / agg["_w"].replace(0, float("nan"))
    grid = grid_ea.merge(agg[["tree_opportunity"]], on="_tile_ix", how="left")
    grid["tree_opportunity"] = grid["tree_opportunity"].fillna(0.0)
    return grid.drop(columns="_tile_ix").to_crs("EPSG:4326")


def _attach_from_raster(grid, raster_path):
    from rasterstats import zonal_stats  # lazy

    print(f"Computing zonal statistics against {raster_path}...")
    stats = zonal_stats(grid.geometry, str(raster_path), stats="mean", nodata=-9999)
    grid["tree_opportunity"] = [s["mean"] if (s and s["mean"] is not None) else 0.0
                                for s in stats]
    return grid


def build_unified_grid():
    print("Loading base geometry...")
    grid = _load_spatial_data(SNAPSHOT_GEOJSON)
    print(f"Loaded {len(grid)} grid cells.")
    if grid.crs is None:
        grid = grid.set_crs(epsg=4326)

    if OPPORTUNITY_PATH.exists():
        grid = _attach_from_vector(grid, OPPORTUNITY_PATH)
    elif OPPORTUNITY_RASTER.exists():
        grid = _attach_from_raster(grid, OPPORTUNITY_RASTER)
    else:
        print(f"⚠️  No opportunity layer ({OPPORTUNITY_PATH.name} / {OPPORTUNITY_RASTER.name}). "
              "Run the 'opportunity' stage first. Setting tree_opportunity = 0.0 (NO-OP).")
        grid["tree_opportunity"] = 0.0

    grid.to_file(UNIFIED_GRID, driver="GeoJSON")
    covered = (grid["tree_opportunity"] > 0).sum()
    print(f"✅ Unified grid -> {UNIFIED_GRID} ({covered}/{len(grid)} tiles non-zero)")


# =========================================================================== #
# 6. STAGE 4 — HeatROI priority scoring  (the deliverable)                    #
# =========================================================================== #

def _minmax(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    lo, hi = s.min(), s.max()
    if pd.isna(lo) or pd.isna(hi) or hi == lo:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - lo) / (hi - lo)


def load_equity(path=None) -> pd.DataFrame:
    """Row 1 of the sheet is a title; row 2 is the real header."""
    path = path or EQUITY_XLSX
    df = pd.read_excel(path, sheet_name=EQUITY_SHEET, skiprows=1, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]
    if "GEOID" in df.columns:
        df["GEOID"] = df["GEOID"].astype(str).str.replace(r"\.0$", "", regex=True)
    return df


def _heat_component(df: pd.DataFrame):
    if "_tot1500" in df.columns and pd.to_numeric(df["_tot1500"], errors="coerce").notna().any():
        return _minmax(df["_tot1500"]), "FortyGuard _tot1500 (3 p.m. total temp)"
    if "temp_norm" in df.columns:
        return (pd.to_numeric(df["temp_norm"], errors="coerce").fillna(0.0),
                "temp_norm (source heat disparity)")
    return _minmax(df.get("temp_diff", pd.Series(np.zeros(len(df))))), "temp_diff (raw)"


def _tier(score: float) -> str:
    for label, threshold in TIER_THRESHOLDS:
        if score >= threshold:
            return label
    return TIER_THRESHOLDS[-1][0]


def score_equity(df: pd.DataFrame):
    out = df.copy()

    heat_n, heat_source = _heat_component(out)
    out["heat_n"] = heat_n
    out["opp_n"] = _minmax(out["tc_gap"])
    vuln_cols = [c for c in VULNERABILITY_COLUMNS if c in out.columns]
    out["vuln"] = (out[vuln_cols].apply(pd.to_numeric, errors="coerce")
                   .mean(axis=1, skipna=True).fillna(0.0))

    out["need"] = NEED_HEAT_WEIGHT * out["heat_n"] + NEED_VULN_WEIGHT * out["vuln"]
    if OPPORTUNITY_MODE == "add":
        w = OPPORTUNITY_ADD_WEIGHT
        out["heat_roi_raw"] = (1 - w) * out["need"] + w * out["opp_n"]
    else:
        out["heat_roi_raw"] = out["need"] * out["opp_n"]

    if REDLINE_BOOST and "holc_grade" in out.columns:
        boosted = out["holc_grade"].astype(str).str.upper().isin(["C", "D"])
        out.loc[boosted, "heat_roi_raw"] *= (1 + REDLINE_BOOST)

    out["heat_roi_score"] = (out["heat_roi_raw"].rank(pct=True) * 100).round(1)
    out["tier"] = out["heat_roi_score"].apply(_tier)

    pop = pd.to_numeric(out.get("acs_pop", 0), errors="coerce").fillna(0.0)
    out["total_benefit"] = (out["heat_roi_raw"] * pop).round(2)
    out["total_benefit_rank"] = out["total_benefit"].rank(ascending=False, method="min").astype(int)

    out = out.sort_values(["heat_roi_score", "need"], ascending=False).reset_index(drop=True)
    out["priority_rank"] = out.index + 1
    return out, {"heat_source": heat_source, "vuln_cols": vuln_cols, "n": len(out)}


_REPORT_COLUMNS = [
    "priority_rank", "GEOID", "place", "county", "acs_pop", "treecanopy", "tc_gap",
    "temp_diff", "temp_norm", "tes", "holc_grade", "heat_n", "opp_n", "vuln", "need",
    "heat_roi_raw", "heat_roi_score", "tier", "total_benefit", "total_benefit_rank",
]


def run_scoring():
    print("Loading Tree Equity Score data...")
    scored, meta = score_equity(load_equity())
    cols = [c for c in _REPORT_COLUMNS if c in scored.columns]
    scored[cols].to_csv(ROI_SCORES_CSV, index=False)
    scored[cols].head(50).to_csv(ROI_TOP_CSV, index=False)

    print("\n" + "=" * 70)
    print("HeatROI — tree-planting priority score")
    print("=" * 70)
    print(f"Block groups scored : {meta['n']}")
    print(f"Heat component      : {meta['heat_source']}")
    print(f"Vulnerability index : mean of {meta['vuln_cols']}")
    print(f"Weights             : need = {NEED_HEAT_WEIGHT}*heat + {NEED_VULN_WEIGHT}*vuln;  "
          f"opportunity mode = {OPPORTUNITY_MODE}")
    print(f"Score range         : {scored['heat_roi_score'].min()} – {scored['heat_roi_score'].max()}")

    print("\nTier breakdown:")
    counts = scored["tier"].value_counts()
    for tier, _ in TIER_THRESHOLDS:
        print(f"  {tier:9} {int(counts.get(tier, 0)):5}")

    print("\nTop 10 priorities:")
    print(f"  {'#':>2}  {'place':22} {'county':20} {'tes':>4} {'ΔT°C':>6} "
          f"{'gap':>5} {'vuln':>5} {'score':>6}  tier")
    for _, r in scored.head(10).iterrows():
        place = r["place"] if isinstance(r["place"], str) and r["place"] else None
        place = place or ("(unincorporated)" if isinstance(r["county"], str) else str(r["GEOID"]))
        print(f"  {int(r['priority_rank']):>2}  {str(place)[:22]:22} {str(r['county'])[:20]:20} "
              f"{r['tes']:>4.0f} {r['temp_diff']:>6.1f} {r['tc_gap']:>5.2f} "
              f"{r['vuln']:>5.2f} {r['heat_roi_score']:>6.1f}  {r['tier']}")

    print(f"\n✅ Wrote {ROI_SCORES_CSV}")
    print(f"✅ Wrote {ROI_TOP_CSV}")


# =========================================================================== #
# 7. ORCHESTRATOR + CLI                                                       #
# =========================================================================== #

def _stage(name: str, fn) -> bool:
    print(f"\n{'=' * 70}\n▶ {name}\n{'=' * 70}")
    try:
        fn()
        return True
    except Exception as exc:  # keep the pipeline going
        print(f"⏭  Skipped/failed: {type(exc).__name__}: {exc}")
        return False


def run_all():
    if FORTYGUARD_API_KEY:
        _stage("Stage 1 · FortyGuard heat snapshot", fetch_heat_snapshot)
    else:
        print("\n⏭  Stage 1 skipped — FORTYGUARD_API_KEY not set (using existing snapshot if present).")
    _stage("Stage 2 · WRI tree-opportunity layer", download_opportunity_layer)
    _stage("Stage 3 · Unified heat × opportunity grid", build_unified_grid)
    _stage("Stage 4 · HeatROI priority scoring", run_scoring)


def main(argv=None):
    parser = argparse.ArgumentParser(description="HeatROI urban-heat tree-planting ROI pipeline.")
    parser.add_argument(
        "command", nargs="?", default="all",
        choices=["all", "score", "city", "fetch", "opportunity", "unify"],
        help="which stage to run (default: all)",
    )
    parser.add_argument("name", nargs="?", default="Atlanta", help="city name for the 'city' command")
    args = parser.parse_args(argv)

    if args.command == "score":
        run_scoring()
    elif args.command == "city":
        get_wri_city_id(args.name)
    elif args.command == "fetch":
        fetch_heat_snapshot()
    elif args.command == "opportunity":
        download_opportunity_layer()
    elif args.command == "unify":
        build_unified_grid()
    else:
        run_all()


if __name__ == "__main__":
    main()
