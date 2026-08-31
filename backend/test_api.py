"""
Contract gates for the API. Run: python3 test_api.py

Uses starlette's TestClient, so no server needs to be running. These gates check the contract
the frontend depends on -- and specifically that the four defects removed from the inherited
API cannot come back: mock data, a shadowed route, a fabricated temperature, and heavy imports.
"""

import ast
import sys

from fastapi.testclient import TestClient

import config
import main

FAILS = []
client = TestClient(main.app)

# Parse main.py rather than substring-searching it. The first version of this file grepped the
# raw text and reported 5 false failures, because main.py's docstring NAMES the defects it
# removed ("Removed: generate_mock_grid, ortools, geopandas..."). Documenting a removal is the
# opposite of the offence. AST analysis distinguishes executable code from prose.
_SRC = open("main.py", encoding="utf-8").read()
_TREE = ast.parse(_SRC)

IMPORTS = set()
NAMES = set()
for _n in ast.walk(_TREE):
    if isinstance(_n, ast.Import):
        IMPORTS.update(a.name.split(".")[0] for a in _n.names)
    elif isinstance(_n, ast.ImportFrom):
        if _n.module:
            IMPORTS.add(_n.module.split(".")[0])
    elif isinstance(_n, ast.Name):
        NAMES.add(_n.id)
    elif isinstance(_n, ast.Attribute):
        NAMES.add(_n.attr)


def code_mentions(token: str) -> bool:
    """True only if the token appears in executable code -- not in a docstring or comment."""
    return token in IMPORTS or token in NAMES


def check(name, cond, detail=""):
    print("  %-64s %s" % (name, "PASS" if cond else "FAIL"))
    if not cond:
        FAILS.append("%s %s" % (name, detail))
    return cond


def main_():
    print("\nA1 health and AOI payload")
    h = client.get("/api/health")
    check("/api/health returns 200", h.status_code == 200, str(h.status_code))
    check("48 block groups loaded", h.json()["block_groups"] == 48, str(h.json()))
    check("48 geometries loaded", h.json()["geometries"] == 48)

    r = client.get("/api/aoi")
    check("/api/aoi returns 200", r.status_code == 200, str(r.status_code))
    j = r.json()
    check("valid GeoJSON FeatureCollection", j["type"] == "FeatureCollection")
    check("one feature per block group", len(j["features"]) == 48, str(len(j["features"])))
    f0 = j["features"][0]
    check("every feature carries geometry", all(x["geometry"] for x in j["features"]))
    for k in ("GEOID", "hei", "hei_rank", "hei_thermal", "hei_density", "hei_sensitivity",
              "cap_tree", "tc_gap", "acs_pop"):
        check("feature property `%s` present" % k, k in f0["properties"])
    check("HEI is in [0,1]", all(0 <= x["properties"]["hei"] <= 1 for x in j["features"]))
    check("hei_rank is a dense 1..48 permutation",
          sorted(x["properties"]["hei_rank"] for x in j["features"]) == list(range(1, 49)))
    check("the temp_diff / canopy circularity caveat travels with the payload",
          "-0.806" in j["meta"]["caveat"])
    check("meta carries AOI totals", "cost_to_build_everything_usd" in j["meta"]["totals"])

    print("\nA2 no mock data anywhere (the inherited API served generate_mock_grid)")
    for bad in ("generate_mock_grid", "test_optimizer", "uniform", "randint"):
        check("`%s` is not called in executable code" % bad, not code_mentions(bad))
    check("`random` is not imported", "random" not in IMPORTS)
    # A synthetic grid would be a round 100 features on a regular lattice, not 48 real BGs.
    check("feature count is the real AOI (48), not a synthetic grid",
          len(j["features"]) == 48)
    check("GEOIDs are real 12-digit census block group IDs",
          all(len(str(x["properties"]["GEOID"])) == 12 for x in j["features"]))
    check("tree capacity is NOT zero everywhere (the inherited pipeline's failure)",
          sum(x["properties"]["cap_tree"] for x in j["features"]) > 1000)

    print("\nA3 no duplicate routes (the inherited /api/optimize was declared twice)")
    paths = [r.path for r in main.app.routes]
    check("no duplicated route paths", len(paths) == len(set(paths)),
          str([p for p in paths if paths.count(p) > 1]))
    for p in ("/api/aoi", "/api/scenario", "/api/baselines", "/api/frontier",
              "/api/provenance", "/api/diagnostics", "/api/config", "/api/health"):
        check("route %s is registered exactly once" % p, paths.count(p) == 1)

    print("\nA4 scenario contract")
    s = client.post("/api/scenario", json={"budget": 2_000_000})
    check("/api/scenario returns 200", s.status_code == 200, s.text[:200])
    p = s.json()
    check("spend never exceeds the budget", p["spent"] <= 2_000_000 + 1e-6)
    check("plan reports an optimality gap", "gap_pct" in p["optimality"])
    check("gap is non-negative", p["optimality"]["gap_pct"] >= 0)
    check("allocations are itemised", len(p["allocations"]) > 0)
    check("per-intervention breakdown present",
          set(p["by_intervention"]) == set(config.INTERVENTIONS))
    check("both currencies reported separately", "radiant_m2" in p and "ambient_m2" in p)
    check("reporting frame states AOI not metro", "AOI" in p["reporting_frame"])
    check("provenance note travels with the plan", "citation_pending" in p["provenance_note"])
    check("MILP verification is available on request",
          client.post("/api/scenario", json={"budget": 1_000_000, "verify": True})
          .json()["optimality"]["milp_check"]["available"])

    print("\nA5 no fabricated temperature (the inherited API served est_temp_reduction_c)")
    check("`est_temp_reduction_c` is not computed in executable code",
          not code_mentions("est_temp_reduction_c"))
    check("temperature_reduction is explicitly null", p["temperature_reduction"] is None)
    check("the refusal is explained, not silent", "NOT COMPUTED" in p["temperature_note"])
    blob = s.text.lower()
    for bad in ("est_temp", "degrees_c", "temp_reduction_c", "°c"):
        check("no `%s` key in the response body" % bad, bad not in blob)
    check("refusals are published via /api/provenance",
          "est_temp_reduction_c" in str(client.get("/api/provenance").json()["refused"]))

    print("\nA6 invalid requests are rejected with 422, not silently coerced")
    bad = [
        ({"budget": 0}, "zero budget"),
        ({"budget": -5}, "negative budget"),
        ({}, "missing budget"),
        ({"budget": 1e6, "interventions": []}, "empty intervention list"),
        ({"budget": 1e6, "interventions": ["solar_panel"]}, "unknown intervention"),
        ({"budget": 1e6, "currency": "nonsense"}, "bad currency"),
        ({"budget": 1e6, "objective": "max_roi"}, "bad objective"),
        ({"budget": 1e6, "equity_floor": 1.5}, "equity floor > 1"),
        ({"budget": 1e6, "equity_floor": -0.1}, "negative equity floor"),
        ({"budget": 1e6, "currency": "ambient", "objective": "max_people_reached"},
         "ambient + people_reached"),
    ]
    for body, label in bad:
        rr = client.post("/api/scenario", json=body)
        check("%s -> 422" % label, rr.status_code == 422, str(rr.status_code))
    check("a rejection explains itself",
          "empty" in client.post("/api/scenario",
                                 json={"budget": 1e6, "interventions": []}).text.lower())

    print("\nA7 baselines and frontier")
    b = client.post("/api/baselines", json={"budget": 2_000_000})
    check("/api/baselines returns 200", b.status_code == 200, b.text[:200])
    st = b.json()["strategies"]
    check("all four strategies returned", len(st) == 4, str(list(st)))
    check("greedy wins on its own objective",
          all(st["greedy"]["objective_value"] >= v["objective_value"] for v in st.values()))
    check("the naive heat-only policy is materially worse",
          st["hottest_first"]["vs_optimized_pct"] < -5)

    fr = client.post("/api/frontier", json={"budget": 20_000_000, "steps": 6})
    check("/api/frontier returns 200", fr.status_code == 200, fr.text[:200])
    pts = fr.json()["points"]
    check("frontier returns the requested number of points", len(pts) == 6, str(len(pts)))
    check("efficiency is monotone non-increasing",
          all(pts[i]["objective_value"] >= pts[i + 1]["objective_value"] - 1e-6
              for i in range(len(pts) - 1)))
    check("the flat-segment threshold is published", "binding_from_floor" in fr.json())

    print("\nA8 provenance, diagnostics and config are first-class")
    pr = client.get("/api/provenance").json()
    check("provenance lists citation_pending values", len(pr["citation_pending"]) >= 3)
    check("cost.tree is flagged citation_pending", "cost.tree" in pr["citation_pending"])
    check("all five provenance tiers are explained", len(pr["legend"]) == 5)
    check("refused calculations are published", len(pr["refused"]) == 5, str(len(pr["refused"])))

    dg = client.get("/api/diagnostics",
                    params={"currency": "radiant", "objective": "max_effective_area"}).json()
    check("location degeneracy is exposed to the UI", dg["mix"]["location_degenerate"])
    check("cost sensitivity is exposed", "tolerance_pct" in dg["cost_sensitivity"])
    check("saturation note is exposed", "linear" in dg["saturation"]["note"])

    cf = client.get("/api/config").json()
    check("config publishes the badge for the location-blind objective",
          "LOCATION-BLIND" in cf["badged_objectives"]["max_effective_area"])
    check("config publishes the invalid currency/objective pairing",
          len(cf["invalid_combinations"]) == 1)
    check("config publishes editable costs", set(cf["costs"]) == set(config.INTERVENTIONS))

    print("\nA9 weights propagate, and heavy dependencies stay out")
    d = client.get("/api/aoi", params={"weight_thermal": 1.0, "weight_density": 0.0,
                                       "weight_sensitivity": 0.0}).json()
    check("thermal-only weights change the ranking",
          [x["properties"]["GEOID"] for x in d["features"][:5]]
          != [x["properties"]["GEOID"] for x in j["features"][:5]]
          or d["features"][0]["properties"]["hei"] != j["features"][0]["properties"]["hei"])
    check("normalised weights are echoed back",
          abs(sum(d["meta"]["weights"].values()) - 1.0) < 1e-9)
    check("weights also propagate into a scenario",
          client.post("/api/scenario",
                      json={"budget": 2e6, "weight_thermal": 1.0, "weight_density": 0.0,
                            "weight_sensitivity": 0.0}).json()["objective_value"]
          != p["objective_value"])
    for heavy in ("ortools", "geopandas", "shapely", "pyproj", "pywraplp"):
        check("`%s` is not imported by the API" % heavy, heavy not in IMPORTS)
    check("the API imports only its own tested modules plus fastapi/pydantic",
          IMPORTS <= {"functools", "typing", "fastapi", "pydantic", "config", "data",
                      "exposure", "interventions", "optimize", "uvicorn"},
          str(sorted(IMPORTS)))
    check("no analysis maths is defined in the API layer itself",
          not any(isinstance(n, ast.FunctionDef) and n.name.startswith("_compute")
                  for n in ast.walk(_TREE)))

    print("\n" + "=" * 80)
    if FAILS:
        print("RESULT: %d GATE(S) FAILED" % len(FAILS))
        for x in FAILS:
            print("   - %s" % x)
        print("=" * 80)
        sys.exit(1)
    print("RESULT: ALL GATES PASS")
    print("=" * 80)


if __name__ == "__main__":
    main_()
