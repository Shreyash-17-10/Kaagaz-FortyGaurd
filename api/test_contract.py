"""
Frontend/backend contract test (gates C1-C9).

The TypeScript types in `frontend/src/lib/api.ts` are a *claim* about what the API returns. This
file checks that claim against the running API instead of trusting it. It parses the interfaces
straight out of the .ts source, so the gate cannot go stale when either side changes: rename a
field in Python and the gate fails, add a field to TypeScript that Python never sends and the gate
fails too.

Why this and not a linter: a type error inside the frontend is caught by `tsc`. What `tsc` cannot
see is whether the JSON on the wire actually has the shape the types assert. That gap is exactly
where a dashboard silently renders `undefined`.

Run:  python test_contract.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import main

API_TS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "api.ts"

PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    (PASS if ok else FAIL).append(f"{label}{(' -- ' + detail) if detail and not ok else ''}")
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if detail and not ok else ""))
    return ok


# ---------------------------------------------------------------------------
# a very small TypeScript interface reader
# ---------------------------------------------------------------------------

def parse_interfaces(src: str) -> dict[str, dict[str, bool]]:
    """{interface_name: {field: is_optional}} for TOP-LEVEL fields only.

    Deliberately shallow. Nested inline object types are checked by hand below where they matter,
    because a general TS parser is a project of its own and would be a worse use of the budget
    than the checks it enables.
    """
    out: dict[str, dict[str, bool]] = {}
    for m in re.finditer(r"export interface (\w+)\s*\{", src):
        name = m.group(1)
        i = m.end()
        depth = 1
        body_start = i
        while i < len(src) and depth:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        body = src[body_start : i - 1]

        fields: dict[str, bool] = {}
        depth = 0
        for line in body.splitlines():
            stripped = line.strip()
            if depth == 0:
                fm = re.match(r"(?:/\*\*.*)?([A-Za-z_]\w*)(\?)?\s*:", stripped)
                if fm and not stripped.startswith("//") and not stripped.startswith("*"):
                    fields[fm.group(1)] = bool(fm.group(2))
            depth += line.count("{") - line.count("}")
        out[name] = fields
    return out


TS = parse_interfaces(API_TS.read_text(encoding="utf-8"))


def contract(label: str, iface: str, obj: dict, *, allow_extra: bool = True) -> None:
    """Assert every non-optional field declared in `iface` is present in `obj`."""
    declared = TS.get(iface)
    if declared is None:
        check(f"{label}: interface {iface} found in api.ts", False, "not parsed")
        return
    missing = [f for f, opt in declared.items() if not opt and f not in obj]
    check(
        f"{label}: every required {iface} field is present ({len(declared)} declared)",
        not missing,
        f"missing from the API response: {missing}",
    )
    if not allow_extra:
        extra = [k for k in obj if k not in declared]
        check(f"{label}: API sends nothing {iface} does not declare", not extra, f"undeclared: {extra}")


# ---------------------------------------------------------------------------

BODY = {
    "budget": 2_000_000,
    "interventions": ["tree", "shade"],
    "currency": "radiant",
    "objective": "max_priority_area",
    "equity_floor": 0.0,
    "weight_thermal": 0.4,
    "weight_density": 0.3,
    "weight_sensitivity": 0.3,
}


def main_() -> int:
    c = TestClient(main.app)
    print("\n=== C: frontend/backend contract ===\n")

    print("C1  api.ts parsed")
    for want in (
        "BlockGroupProps",
        "AoiResponse",
        "Allocation",
        "Plan",
        "BaselineRow",
        "Baselines",
        "FrontierPoint",
        "Frontier",
        "ProvenanceValue",
        "ProvenanceReport",
        "ScenarioInput",
    ):
        check(f"interface {want} is declared", want in TS)

    print("\nC2  /api/aoi")
    aoi = c.get("/api/aoi", params={k: BODY[k] for k in BODY if k.startswith("weight")}).json()
    check("returns a FeatureCollection", aoi.get("type") == "FeatureCollection")
    check("has 48 block groups", len(aoi["features"]) == 48, f"got {len(aoi['features'])}")
    contract("aoi", "BlockGroupProps", aoi["features"][0]["properties"])
    meta = aoi.get("meta", {})
    for k in ("summary", "totals", "weights", "hei_range", "caveat"):
        check(f"meta.{k} present", k in meta)
    check(
        "meta.hei_range is a 2-tuple of numbers, ascending",
        isinstance(meta["hei_range"], list)
        and len(meta["hei_range"]) == 2
        and meta["hei_range"][0] < meta["hei_range"][1],
        str(meta.get("hei_range")),
    )

    print("\nC3  /api/scenario")
    plan = c.post("/api/scenario", json={**BODY, "verify": True}).json()
    contract("scenario", "Plan", plan)
    contract("scenario", "Allocation", plan["allocations"][0])

    print("\nC4  the KPI strip's fields are all real")
    # Every field the dashboard prints, named explicitly. If one of these disappears the KPI
    # renders "undefined", which is the single most embarrassing failure mode for this product.
    for f in (
        "spent",
        "budget_utilisation",
        "unspent",
        "block_groups_funded",
        "block_groups_total",
        "line_items",
        "people_reached",
        "people_reached_pct",
        "aoi_population",
        "canopy_added_m2",
        "canopy_gap_closed_pct_aoi",
        "temperature_note",
        "warnings",
        "mix_predetermined",
    ):
        check(f"plan.{f}", f in plan and plan[f] is not None, "absent or null")

    print("\nC5  by_intervention covers exactly the three interventions")
    bi = plan["by_intervention"]
    check("keys are tree/cool_roof/shade", set(bi) == {"tree", "cool_roof", "shade"}, str(sorted(bi)))
    for k, v in bi.items():
        check(
            f"by_intervention.{k} has units/unit_label/cost/block_groups",
            {"units", "unit_label", "cost", "block_groups"} <= set(v),
            str(sorted(v)),
        )

    print("\nC6  the refusal is a null, not a zero and not a missing key")
    check("temperature_reduction key exists", "temperature_reduction" in plan)
    check(
        "temperature_reduction is null, matching `: null` in api.ts",
        plan["temperature_reduction"] is None,
        repr(plan["temperature_reduction"]),
    )
    # A 0.0 would render as "0.0 C" in the UI -- a claim of no cooling, which is a different and
    # equally false statement to the one we removed.
    check("temperature_reduction is not 0", plan["temperature_reduction"] != 0)
    check("a note explains the refusal", len(plan.get("temperature_note", "")) > 20)

    print("\nC7  optimality block")
    o = plan["optimality"]
    for f in ("lp_upper_bound", "achieved", "gap_pct", "gap_is_float_noise", "basis"):
        check(f"optimality.{f}", f in o)
    check("gap_pct is never negative", o["gap_pct"] >= 0, str(o["gap_pct"]))
    check(
        "milp_check present when verify=true, with an `available` flag",
        isinstance(o.get("milp_check"), dict) and "available" in o["milp_check"],
        str(o.get("milp_check")),
    )

    print("\nC8  /api/baselines")
    b = c.post("/api/baselines", json=BODY).json()
    contract("baselines", "Baselines", b)
    check(
        "all four strategies present",
        set(b["strategies"]) == {"greedy", "even_spread", "hottest_first", "random"},
        str(sorted(b["strategies"])),
    )
    for name, row in b["strategies"].items():
        contract(f"baselines.{name}", "BaselineRow", row)
    check("greedy is the reference (0% vs itself)", abs(b["strategies"]["greedy"]["vs_optimized_pct"]) < 1e-9)
    check(
        "every naive strategy is worse on the objective",
        all(b["strategies"][k]["vs_optimized_pct"] < 0 for k in b["strategies"] if k != "greedy"),
        str({k: v["vs_optimized_pct"] for k, v in b["strategies"].items()}),
    )

    print("\nC9  /api/frontier")
    f = c.post(
        "/api/frontier",
        json={
            "budget": BODY["budget"],
            "interventions": BODY["interventions"],
            "currency": BODY["currency"],
            "objective": BODY["objective"],
            "steps": 6,
        },
    ).json()
    contract("frontier", "Frontier", f)
    check("6 points requested, 6 returned", len(f["points"]) == 6, str(len(f["points"])))
    for p in f["points"]:
        contract("frontier.point", "FrontierPoint", p)
    check(
        "first_costly_floor is a number or null, matching `number | null`",
        f["first_costly_floor"] is None or isinstance(f["first_costly_floor"], (int, float)),
        repr(f["first_costly_floor"]),
    )
    # The chart shades [0, binding_from_floor] as slack. If that value were outside 0..1 the
    # ReferenceArea would be drawn off-axis and the explanation would silently vanish.
    check(
        "binding_from_floor is in 0..1 so the chart's slack band is drawable",
        0.0 <= f["binding_from_floor"] <= 1.0,
        repr(f["binding_from_floor"]),
    )

    print("\nC10  /api/provenance")
    pr = c.get("/api/provenance").json()
    contract("provenance", "ProvenanceReport", pr)
    for v in pr["values"]:
        contract("provenance.value", "ProvenanceValue", v)
    check("at least one value still lacks a citation, and says so", len(pr["citation_pending"]) > 0)
    check(
        "every citation_pending key exists in values",
        set(pr["citation_pending"]) <= {v["key"] for v in pr["values"]},
    )
    check("refused list is non-empty", len(pr["refused"]) > 0)
    check(
        "every refusal carries a reason",
        all(len(x.get("reason", "")) > 10 for x in pr["refused"]),
    )
    check(
        "temperature reduction is on the refused list, not the values list",
        any("temp" in x["key"].lower() for x in pr["refused"]),
        str([x["key"] for x in pr["refused"]]),
    )

    print("\nC11  the ambient/radiant separation survives the wire")
    amb = c.post("/api/scenario", json={**BODY, "currency": "ambient", "interventions": ["tree", "cool_roof"]}).json()
    check("ambient plan buys no shade structures", amb["by_intervention"]["shade"]["units"] == 0)
    check("radiant plan buys no cool roofs", plan["by_intervention"]["cool_roof"]["units"] == 0)
    check(
        "the two currencies are reported separately, never summed",
        "radiant_m2" in plan and "ambient_m2" in plan,
    )

    print("\nC12  the frontend's guard rails match the backend's refusals")
    r = c.post("/api/scenario", json={**BODY, "interventions": []})
    check("an empty intervention list is rejected", r.status_code == 422, f"got {r.status_code}")
    r = c.post("/api/scenario", json={**BODY, "currency": "ambient", "objective": "max_people_reached"})
    check(
        "ambient + people-reached is rejected, which is why Controls.tsx switches the objective",
        r.status_code == 422,
        f"got {r.status_code}",
    )
    r = c.post("/api/scenario", json={**BODY, "weight_thermal": 0, "weight_density": 0, "weight_sensitivity": 0})
    check(
        "all-zero weights are rejected, which is why the Allocate button disables",
        r.status_code == 422,
        f"got {r.status_code}",
    )

    print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
    if FAIL:
        print("\nFAILURES")
        for x in FAIL:
            print("  -", x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
