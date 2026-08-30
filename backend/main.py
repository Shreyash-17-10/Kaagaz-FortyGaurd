"""
HeatROI API.

A deliberately thin shell. Every number returned here is computed by a module with its own gate
suite (`exposure`, `interventions`, `optimize`), so this file contains no analysis logic --
only request validation, caching and serialisation. If a calculation appears in this file,
it is in the wrong place.

Removed from the inherited prototype, all deliberately:
  * `generate_mock_grid(100)` -- the API served synthetic data; against the real dataset the
    inherited tree capacity is 0 everywhere, so the mock was hiding a dead pipeline.
  * the duplicate `@app.post("/api/optimize")` -- the second declaration shadowed the first,
    making the optimized-vs-naive comparison dead code that never ran.
  * `est_temp_reduction_c` -- fabricated (`benefit/n * 0.08`, capped at 3.5). No cited cooling
    coefficient exists in this workspace, so no degrees figure is served. `/api/scenario`
    returns `temperature_reduction: null` with a reason, so the refusal is visible.
  * `ortools` and `geopandas` imports -- neither is needed. Runtime is pandas + numpy only.

Run: uvicorn main:app --reload --port 8000
"""

from functools import lru_cache
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from backend import config
import data
import exposure
import interventions as iv
import optimize as op

app = FastAPI(
    title="HeatROI API",
    version="0.5.0",
    description=("Budget allocation for urban heat mitigation. Every response carries "
                 "provenance; no endpoint returns a temperature reduction."),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------------------------------
# data loading -- once, at first request, then cached
# ------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load():
    """The AOI artifact is static, so parse it once. Raises loudly if validation fails."""
    df, geom = data.load()
    return df, geom


@lru_cache(maxsize=32)
def _scored(weights_key: str):
    """
    Cached by a canonical weights string. Recomputing percentile ranks per request would be
    wasteful, and the weight vector is the only thing that changes the scores.
    """
    df, _ = _load()
    if not weights_key:
        return exposure.compute(df)
    parts = dict(p.split("=") for p in weights_key.split(","))
    return exposure.compute(df, {k: float(v) for k, v in parts.items()})


def _weights_key(thermal: Optional[float], density: Optional[float],
                 sensitivity: Optional[float]) -> str:
    if thermal is None and density is None and sensitivity is None:
        return ""
    w = config.DEFAULT_WEIGHTS
    return "thermal=%s,density=%s,sensitivity=%s" % (
        thermal if thermal is not None else w["thermal"],
        density if density is not None else w["density"],
        sensitivity if sensitivity is not None else w["sensitivity"])


# ------------------------------------------------------------------------------------------
# request models
# ------------------------------------------------------------------------------------------

class ScenarioRequest(BaseModel):
    budget: float = Field(..., gt=0, le=1e10, description="USD, must be > 0")
    interventions: Optional[List[str]] = Field(
        None, description="subset of tree/cool_roof/shade; null means all")
    currency: str = Field("radiant", description="radiant | ambient -- never summed")
    objective: str = Field("max_priority_area",
                           description="max_priority_area | max_effective_area | "
                                       "max_people_reached")
    equity_floor: float = Field(0.0, ge=0.0, le=1.0,
                                description="min share of budget in priority block groups")
    weight_thermal: Optional[float] = Field(None, ge=0.0, le=1.0)
    weight_density: Optional[float] = Field(None, ge=0.0, le=1.0)
    weight_sensitivity: Optional[float] = Field(None, ge=0.0, le=1.0)
    verify: bool = Field(False, description="also run the scipy/HiGHS MILP cross-check")

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        if v not in iv.CURRENCIES:
            raise ValueError("currency must be one of %s" % iv.CURRENCIES)
        return v

    @field_validator("objective")
    @classmethod
    def _obj(cls, v: str) -> str:
        if v not in iv.OBJECTIVES:
            raise ValueError("objective must be one of %s" % list(iv.OBJECTIVES))
        return v

    @field_validator("interventions")
    @classmethod
    def _ivs(cls, v):
        # An explicitly empty list is a user error, not "give me everything". This is the same
        # silent-coercion bug that gate I8 caught inside the model layer.
        if v is not None:
            if len(v) == 0:
                raise ValueError("interventions cannot be an empty list; omit the field for all")
            unknown = set(v) - set(config.INTERVENTIONS)
            if unknown:
                raise ValueError("unknown interventions: %s" % sorted(unknown))
        return v


class FrontierRequest(BaseModel):
    budget: float = Field(..., gt=0, le=1e10)
    interventions: Optional[List[str]] = None
    currency: str = "radiant"
    objective: str = "max_priority_area"
    steps: int = Field(11, ge=2, le=21)


# ------------------------------------------------------------------------------------------
# endpoints
# ------------------------------------------------------------------------------------------

@app.get("/api/health")
def health() -> Dict[str, object]:
    df, geom = _load()
    return {"ok": True, "block_groups": int(len(df)), "geometries": len(geom),
            "version": app.version}


@app.get("/api/aoi")
def aoi(weight_thermal: Optional[float] = Query(None, ge=0.0, le=1.0),
        weight_density: Optional[float] = Query(None, ge=0.0, le=1.0),
        weight_sensitivity: Optional[float] = Query(None, ge=0.0, le=1.0)
        ) -> Dict[str, object]:
    """
    The map payload: one GeoJSON feature per block group, carrying HEI, its three components,
    capacity and the raw measured inputs. Weight query params re-score on the fly.
    """
    df, geom = _load()
    key = _weights_key(weight_thermal, weight_density, weight_sensitivity)
    try:
        scored = _scored(key)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    cap = iv.capacity(scored)

    fields = ["GEOID", "hei", "hei_rank", "hei_thermal", "hei_density", "hei_sensitivity",
              "temp_diff", "treecanopy", "tc_gap", "tc_goal", "acs_pop", "land_area",
              "cap_tree", "cap_cool_roof", "cap_shade", "canopy_deficit_m2", "exposed_residents"]
    features = []
    for row in cap.itertuples(index=False):
        d = {f: getattr(row, f, None) for f in fields}
        d = {k: (round(float(v), 6) if isinstance(v, float) else v) for k, v in d.items()}
        g = geom.get(d["GEOID"])
        if g is None:
            continue
        features.append({"type": "Feature", "geometry": g, "properties": d})

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "summary": data.summary(df),
            "totals": iv.aoi_totals(scored),
            "weights": exposure.normalize_weights(
                None if not key else dict(
                    (p.split("=")[0], float(p.split("=")[1])) for p in key.split(","))),
            "hei_range": [round(float(scored["hei"].min()), 4),
                          round(float(scored["hei"].max()), 4)],
            "caveat": ("temp_diff units are UNVERIFIED (evidence points to degrees F) and "
                       "corr(temp_diff, treecanopy) = -0.806 within the Atlanta urban area, "
                       "so the thermal term partly encodes canopy itself."),
        },
    }


@app.post("/api/scenario")
def scenario(req: ScenarioRequest) -> Dict[str, object]:
    """Allocate a budget. Returns the plan, optimality evidence and every applicable warning."""
    key = _weights_key(req.weight_thermal, req.weight_density, req.weight_sensitivity)
    try:
        scored = _scored(key)
        plan = op.allocate(scored, req.budget, req.interventions, req.currency,
                           req.objective, req.equity_floor, verify=req.verify)
    except ValueError as e:
        # Model-layer refusals (bad combination, empty list, out-of-range floor) are user
        # errors, so they surface as 422 with the model's own message rather than a 500.
        raise HTTPException(status_code=422, detail=str(e))
    plan["provenance_note"] = ("Costs and two of three capacities are ASSUMED and "
                              "citation_pending; see /api/provenance.")
    return plan


@app.post("/api/baselines")
def baselines(req: ScenarioRequest) -> Dict[str, object]:
    """Optimized plan vs even spread, hottest-first and seeded random on the same budget."""
    key = _weights_key(req.weight_thermal, req.weight_density, req.weight_sensitivity)
    try:
        return op.compare_baselines(_scored(key), req.budget, req.interventions,
                                    req.currency, req.objective)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/frontier")
def frontier(req: FrontierRequest) -> Dict[str, object]:
    """Sweep the equity floor 0 -> 100% and measure the efficiency cost of each increment."""
    try:
        return op.frontier(_scored(""), req.budget, req.interventions, req.currency,
                           req.objective, req.steps)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/api/diagnostics")
def diagnostics(currency: str = Query("radiant"),
                objective: str = Query("max_priority_area")) -> Dict[str, object]:
    """
    The degeneracy checks. Exposed as a first-class endpoint because the UI must be able to warn
    that an objective is location-blind, or that the intervention mix follows from assumed costs
    rather than from data.
    """
    try:
        scored = _scored("")
        return {
            "mix": iv.mix_diagnostics(scored, None, currency, objective),
            "cost_sensitivity": iv.cost_flip_point(currency),
            "saturation": {
                "max_coverage": round(
                    float(iv.saturation_check(scored, None, "radiant")["max_coverage"].max()), 4),
                "note": ("No block group can saturate at full build-out, so pro-rata "
                         "people-reached is exactly linear across the feasible region."),
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/api/provenance")
def provenance() -> Dict[str, object]:
    """
    Every assumed number, its provenance tag, and what was explicitly refused. This endpoint is
    a product feature, not debug output -- it is what makes the rest of the numbers trustworthy.
    """
    rep = config.provenance_report()
    rep["refused"] = config.REFUSED
    rep["legend"] = {
        "Measured": "read directly from a source dataset",
        "Derived": "computed from measured values by a stated formula",
        "Assumed": "a placeholder chosen by us; no citation in this workspace",
        "External": "requires a dataset not present here",
        "Unknown": "we do not know, and say so",
    }
    return rep


@app.get("/api/config")
def get_config() -> Dict[str, object]:
    """Editable knobs plus the fixed vocabulary the UI builds its controls from."""
    return {
        "interventions": config.INTERVENTIONS,
        "units": config.UNITS,
        "currencies": iv.CURRENCIES,
        "objectives": iv.OBJECTIVES,
        "default_objective": "max_priority_area",
        "default_currency": "radiant",
        "default_weights": config.DEFAULT_WEIGHTS,
        "costs": {k: v.value for k, v in config.COST.items()},
        "strategies": op.STRATEGIES,
        "invalid_combinations": [
            {"currency": "ambient", "objective": "max_people_reached",
             "reason": ("people-reached is defined on street-level radiant coverage; cool roofs "
                        "have no radiant effect, so the pairing has no meaning")},
        ],
        "badged_objectives": {
            "max_effective_area": ("LOCATION-BLIND: benefit per dollar is identical in every "
                                   "block group under this objective, so the map is an "
                                   "arbitrary tie-break. Use max_priority_area to choose where."),
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
