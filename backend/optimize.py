"""
HeatROI — budget allocation (Component 5).

Given a candidate table (interventions.candidates) and a budget, decide how many units of each
intervention to build in each block group.

--------------------------------------------------------------------------------------------
WHY GREEDY AND NOT A SOLVER
--------------------------------------------------------------------------------------------
The problem is a bounded knapsack: one budget constraint, one variable per (block group,
intervention) pair, box bounds from capacity, linear objective. For that shape, sorting by
benefit-per-dollar and buying in order is the EXACT optimum of the fractional relaxation, and
the integer optimum can differ only by the partial unit at the cut-off -- at most one unit's
worth of benefit.

That is not an assumption here. `lp_bound()` computes the fractional optimum and
`milp_verify()` re-solves the true integer problem with scipy/HiGHS, so every plan reports a
MEASURED optimality gap instead of a claimed one.

--------------------------------------------------------------------------------------------
WHAT THE EQUITY FLOOR DOES
--------------------------------------------------------------------------------------------
Pure efficiency can concentrate all spending in a handful of block groups. `equity_floor` is
the minimum share of the budget that must be spent in PRIORITY block groups -- those in the
upper half of the social-sensitivity component, a criterion independent of the efficiency
objective. Implemented as a two-pool greedy: fill the reserved pool from priority block groups
only, then spend the remainder unrestricted. For a budget split into two independent knapsacks
this is exactly optimal within each pool.

`frontier()` sweeps the floor from 0 to 100% so the efficiency cost of equity is measured and
plotted rather than argued about.

--------------------------------------------------------------------------------------------
BASELINES
--------------------------------------------------------------------------------------------
A plan is only interesting relative to what a city would otherwise do. Three naive strategies
are implemented for comparison: even spread across eligible block groups, hottest-first
(ignores cost), and seeded random. The inherited prototype had a naive comparison but a
duplicate route made it dead code, so no comparison was ever shown.
"""

from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

try:
    import config
    import interventions as iv
except ImportError:
    from backend import config
    from backend import interventions as iv

# Trees and shade structures come in whole units; cool-roof coating is a continuous area.
INTEGRAL = {"tree": True, "shade": True, "cool_roof": False}

STRATEGIES = ["greedy", "even_spread", "hottest_first", "random"]


# ------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------

def _viable(cands: pd.DataFrame) -> pd.DataFrame:
    v = cands[cands["viable"]].copy()
    return v.reset_index(drop=True)


def _units_affordable(cost_per_unit: float, remaining: float, cap: float,
                      integral: bool) -> float:
    if cost_per_unit <= 0 or remaining <= 0 or cap <= 0:
        return 0.0
    n = remaining / cost_per_unit
    if integral:
        n = float(np.floor(n + 1e-9))
    return float(min(cap, n))


def priority_mask(cands: pd.DataFrame, scored: pd.DataFrame) -> pd.Series:
    """
    Priority block groups = upper half of `hei_sensitivity` within the AOI.

    Deliberately NOT the upper half of HEI: HEI already contains the thermal and density terms
    the efficiency objective rewards, so using it would make the equity floor nearly free and
    the frontier misleadingly flat. Social sensitivity is the dimension efficiency ignores.
    """
    cut = float(scored["hei_sensitivity"].median())
    hi = set(scored.loc[scored["hei_sensitivity"] >= cut, "GEOID"])
    return cands["GEOID"].isin(hi)


# ------------------------------------------------------------------------------------------
# core allocators
# ------------------------------------------------------------------------------------------

def _spend(rows: pd.DataFrame, budget: float) -> List[Dict[str, object]]:
    """Buy down an already-ordered candidate list until the budget runs out."""
    picks, remaining = [], float(budget)
    for r in rows.itertuples(index=False):
        if remaining <= 1e-9:
            break
        n = _units_affordable(r.cost_per_unit, remaining, r.capacity_units,
                              INTEGRAL[r.intervention])
        if n <= 0:
            continue
        cost = n * r.cost_per_unit
        picks.append({
            "GEOID": r.GEOID,
            "intervention": r.intervention,
            "unit": r.unit,
            "units": n,
            "cost": cost,
            "benefit": n * r.benefit_per_unit,
            "radiant_m2": n * r.radiant_m2_per_unit,
            "ambient_m2": n * r.ambient_m2_per_unit,
            "benefit_per_dollar": r.benefit_per_dollar,
            "hei": r.hei,
        })
        remaining -= cost
    return picks


def greedy(cands: pd.DataFrame, budget: float) -> List[Dict[str, object]]:
    """Buy in descending benefit-per-dollar order. Exact for the fractional relaxation."""
    v = _viable(cands).sort_values(
        ["benefit_per_dollar", "hei", "GEOID"], ascending=[False, False, True])
    return _spend(v, budget)


def two_pool_greedy(cands: pd.DataFrame, scored: pd.DataFrame, budget: float,
                    equity_floor: float) -> List[Dict[str, object]]:
    """
    Reserve `equity_floor` (0-1) of the budget for priority block groups, then spend the rest
    without restriction. Each pool is solved by greedy, which is exact within the pool.
    """
    if not 0.0 <= equity_floor <= 1.0:
        raise ValueError("equity_floor must be in [0, 1], got %s" % equity_floor)
    if equity_floor <= 0:
        return greedy(cands, budget)

    v = _viable(cands)
    mask = priority_mask(v, scored)
    reserved = budget * equity_floor

    pool_a = v[mask].sort_values(["benefit_per_dollar", "hei", "GEOID"],
                                 ascending=[False, False, True])
    picks = _spend(pool_a, reserved)

    # Whatever the reserved pool could not absorb stays available to everyone.
    spent_a = sum(p["cost"] for p in picks)
    used = {}
    for p in picks:
        used[(p["GEOID"], p["intervention"])] = p["units"]

    rest = v.copy()
    rest["capacity_units"] = [
        max(0.0, c - used.get((g, k), 0.0))
        for g, k, c in zip(rest["GEOID"], rest["intervention"], rest["capacity_units"])
    ]
    rest = rest[rest["capacity_units"] > 0].sort_values(
        ["benefit_per_dollar", "hei", "GEOID"], ascending=[False, False, True])
    picks += _spend(rest, budget - spent_a)

    # Merge duplicate (block group, intervention) lines produced by the two passes.
    merged: Dict[tuple, Dict[str, object]] = {}
    for p in picks:
        key = (p["GEOID"], p["intervention"])
        if key in merged:
            m = merged[key]
            for f in ("units", "cost", "benefit", "radiant_m2", "ambient_m2"):
                m[f] += p[f]
        else:
            merged[key] = dict(p)
    return list(merged.values())


# ------------------------------------------------------------------------------------------
# baselines -- what a city might do without an optimizer
# ------------------------------------------------------------------------------------------

def baseline_even_spread(cands: pd.DataFrame, budget: float) -> List[Dict[str, object]]:
    """Equal dollars to every eligible block group, cheapest-effective intervention first."""
    v = _viable(cands)
    geoids = sorted(v["GEOID"].unique())
    if not geoids:
        return []
    per = budget / len(geoids)
    picks = []
    for g in geoids:
        rows = v[v["GEOID"] == g].sort_values("benefit_per_dollar", ascending=False)
        picks += _spend(rows, per)
    return picks


def baseline_hottest_first(cands: pd.DataFrame, scored: pd.DataFrame,
                           budget: float) -> List[Dict[str, object]]:
    """
    Spend everything on the hottest block groups first, ignoring cost-effectiveness entirely.
    This is the intuitive policy a heat map alone would suggest -- and the thing HeatROI exists
    to beat.
    """
    v = _viable(cands)
    heat = scored.set_index("GEOID")["hei_thermal"]
    v = v.assign(_t=v["GEOID"].map(heat))
    v = v.sort_values(["_t", "benefit_per_dollar"], ascending=[False, False])
    return _spend(v.drop(columns="_t"), budget)


def baseline_random(cands: pd.DataFrame, budget: float, seed: int = 42
                    ) -> List[Dict[str, object]]:
    """Seeded random order. Reproducible, and a floor for 'is the optimizer doing anything?'."""
    v = _viable(cands)
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(v))
    return _spend(v.iloc[order], budget)


# ------------------------------------------------------------------------------------------
# bounds and independent verification
# ------------------------------------------------------------------------------------------

def lp_bound(cands: pd.DataFrame, budget: float) -> float:
    """
    Exact optimum of the FRACTIONAL relaxation: fill in ratio order, splitting the last item.
    A valid upper bound on any integer solution, so (bound - greedy) / bound bounds the gap.
    """
    v = _viable(cands).sort_values("benefit_per_dollar", ascending=False)
    remaining, total = float(budget), 0.0
    for r in v.itertuples(index=False):
        if remaining <= 1e-12:
            break
        take = min(r.capacity_units, remaining / r.cost_per_unit)
        total += take * r.benefit_per_unit
        remaining -= take * r.cost_per_unit
    return total


def milp_verify(cands: pd.DataFrame, budget: float,
                time_limit: float = 30.0) -> Dict[str, object]:
    """
    Re-solve the TRUE integer problem with scipy/HiGHS and compare against greedy.

    This is the independent check: greedy and a branch-and-bound solver are entirely different
    algorithms, so agreement is real evidence rather than self-confirmation. Returns
    availability info instead of raising if scipy is absent, so the API never depends on it.
    """
    try:
        from scipy.optimize import Bounds, LinearConstraint, milp
    except ImportError:
        return {"available": False,
                "note": "scipy not installed; greedy is compared against the LP bound only."}

    v = _viable(cands)
    if v.empty:
        return {"available": True, "objective": 0.0, "status": "empty"}

    c = -v["benefit_per_unit"].to_numpy(dtype=float)          # milp minimises
    cost = v["cost_per_unit"].to_numpy(dtype=float)
    ub = v["capacity_units"].to_numpy(dtype=float)
    integrality = np.array([1 if INTEGRAL[k] else 0 for k in v["intervention"]])

    res = milp(c=c,
               constraints=LinearConstraint(cost.reshape(1, -1), -np.inf, float(budget)),
               integrality=integrality,
               bounds=Bounds(np.zeros_like(ub), ub),
               options={"time_limit": time_limit, "presolve": True})
    if not res.success or res.x is None:
        return {"available": True, "status": str(res.message), "objective": None}
    x = np.asarray(res.x, dtype=float)
    return {
        "available": True,
        "status": "optimal" if res.status == 0 else str(res.message),
        "objective": float(-res.fun),
        "cost": float(x @ cost),
        "nonzero_lines": int((x > 1e-6).sum()),
    }


# ------------------------------------------------------------------------------------------
# plan assembly
# ------------------------------------------------------------------------------------------

def summarise(picks: Sequence[Dict[str, object]], scored: pd.DataFrame, budget: float,
              currency: str, objective: str,
              equity_floor: float = 0.0) -> Dict[str, object]:
    """Turn raw line items into the payload the API and UI consume. No invented numbers."""
    cap = iv.capacity(scored)
    deficit = float(cap["canopy_deficit_m2"].sum())
    crown = float(config.CROWN_AREA_M2.value)

    df = pd.DataFrame(list(picks)) if picks else pd.DataFrame(
        columns=["GEOID", "intervention", "unit", "units", "cost", "benefit",
                 "radiant_m2", "ambient_m2", "benefit_per_dollar", "hei"])

    total_cost = float(df["cost"].sum()) if len(df) else 0.0
    total_benefit = float(df["benefit"].sum()) if len(df) else 0.0

    by_iv = {}
    for k in config.INTERVENTIONS:
        sel = df[df["intervention"] == k] if len(df) else df
        by_iv[k] = {
            "units": round(float(sel["units"].sum()), 2) if len(sel) else 0.0,
            "unit_label": config.UNITS[k],
            "cost": round(float(sel["cost"].sum()), 2) if len(sel) else 0.0,
            "block_groups": int(sel["GEOID"].nunique()) if len(sel) else 0,
        }

    trees = by_iv["tree"]["units"]
    canopy_added = trees * crown

    # People reached, recomputed from the plan rather than carried from the objective, so the
    # number is identical no matter which objective was optimised. Pro-rata by treated area,
    # capped at the block group's own population.
    reached = 0.0
    if len(df):
        pop = cap.set_index("GEOID")["acs_pop"]
        land = cap.set_index("GEOID")["land_area_m2"]
        per_bg = df.groupby("GEOID")["radiant_m2"].sum()
        for g, m2 in per_bg.items():
            if g in pop.index:
                reached += float(pop[g]) * min(1.0, float(m2) / float(land[g]))

    equity_spend = 0.0
    if len(df):
        pmask = priority_mask(df, scored)
        equity_spend = float(df.loc[pmask, "cost"].sum())

    return {
        "budget": float(budget),
        "currency": currency,
        "objective": objective,
        "equity_floor": float(equity_floor),
        "spent": round(total_cost, 2),
        "budget_utilisation": round(total_cost / budget, 4) if budget > 0 else 0.0,
        "unspent": round(float(budget) - total_cost, 2),
        "objective_value": round(total_benefit, 2),
        "block_groups_funded": int(df["GEOID"].nunique()) if len(df) else 0,
        "block_groups_total": int(len(scored)),
        "line_items": int(len(df)),
        "by_intervention": by_iv,
        "radiant_m2": round(float(df["radiant_m2"].sum()), 1) if len(df) else 0.0,
        "ambient_m2": round(float(df["ambient_m2"].sum()), 1) if len(df) else 0.0,
        "canopy_added_m2": round(canopy_added, 1),
        "canopy_gap_closed_pct_aoi": round(canopy_added / deficit * 100, 3) if deficit else 0.0,
        "aoi_canopy_deficit_m2": round(deficit, 1),
        "people_reached": int(round(reached)),
        "people_reached_pct": round(reached / float(cap["acs_pop"].sum()) * 100, 2),
        "aoi_population": int(cap["acs_pop"].sum()),
        "equity_spend": round(equity_spend, 2),
        "equity_share": round(equity_spend / total_cost, 4) if total_cost > 0 else 0.0,
        "reporting_frame": ("Percentages are against the selected AOI (%d block groups), never "
                            "the metro. $ figures use ASSUMED unit costs."
                            % int(len(scored))),
        "temperature_reduction": None,
        "temperature_note": ("NOT COMPUTED. No cited cooling coefficient exists for this "
                             "workspace; any degrees figure would be fabricated."),
    }


def allocate(scored: pd.DataFrame, budget: float,
             interventions: Optional[List[str]] = None,
             currency: str = "radiant",
             objective: str = "max_priority_area",
             equity_floor: float = 0.0,
             verify: bool = False) -> Dict[str, object]:
    """Full scenario: plan + optimality evidence + degeneracy warnings + line items."""
    if budget is None or budget <= 0:
        raise ValueError("budget must be > 0, got %r" % budget)

    cands = iv.candidates(scored, interventions, currency, objective)
    picks = (two_pool_greedy(cands, scored, budget, equity_floor) if equity_floor > 0
             else greedy(cands, budget))

    out = summarise(picks, scored, budget, currency, objective, equity_floor)

    bound = lp_bound(cands, budget)
    # Compare UNROUNDED values. Using the rounded display figure made the gap read as a
    # negative -1e-5%, which looks like the plan beat its own upper bound -- impossible, and
    # exactly the kind of artifact that destroys trust in a validation number.
    got_raw = float(sum(float(p["benefit"]) for p in picks))
    gap = (bound - got_raw) / bound * 100 if bound > 0 else 0.0
    out["optimality"] = {
        "lp_upper_bound": round(bound, 2),
        "achieved": out["objective_value"],
        "gap_pct": round(max(0.0, gap), 6),
        "gap_is_float_noise": bool(-1e-6 < gap < 0),
        "basis": ("Greedy-by-ratio is the exact optimum of the fractional relaxation of a "
                  "single-constraint knapsack; the LP value is therefore a valid upper bound "
                  "on any integer plan. Note the bound is computed WITHOUT the equity floor, "
                  "so a non-zero floor shows a real (intended) efficiency cost here."),
    }
    if verify:
        out["optimality"]["milp_check"] = milp_verify(cands, budget)

    diag = iv.mix_diagnostics(scored, interventions, currency, objective)
    out["warnings"] = list(diag["warnings"])
    out["mix_contested"] = diag["mix_contested"]
    out["mix_predetermined"] = diag["mix_predetermined"]

    # Is the equity floor doing anything? Measured, not assumed. Below roughly $5M in this AOI
    # the efficient plan already spends 100% in priority block groups, so every floor is
    # slack and the frontier is flat. A flat chart with no explanation looks like a broken
    # feature; saying "the constraint is not binding at this budget" is a real result.
    unconstrained_share = out["equity_share"] if equity_floor <= 0 else round(
        summarise(greedy(cands, budget), scored, budget, currency,
                  objective)["equity_share"], 4)
    out["equity_binding"] = bool(equity_floor > unconstrained_share + 1e-9)
    out["equity_share_unconstrained"] = unconstrained_share
    if equity_floor > 0 and not out["equity_binding"]:
        out["warnings"].append(
            "EQUITY FLOOR NOT BINDING: the unconstrained plan already directs %.1f%% of "
            "spending to priority block groups, which meets the %.0f%% floor on its own. "
            "Efficiency and equity are aligned at this budget, so the slider changes nothing "
            "until the floor exceeds %.1f%%."
            % (unconstrained_share * 100, equity_floor * 100, unconstrained_share * 100))
    if equity_floor > 0 and out["equity_share"] + 1e-9 < equity_floor:
        out["warnings"].append(
            "Equity floor of %.0f%% could not be met: priority block groups can only absorb "
            "%.1f%% of this budget at capacity." % (equity_floor * 100,
                                                   out["equity_share"] * 100))

    out["allocations"] = sorted(
        [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in p.items()}
         for p in picks],
        key=lambda p: -p["cost"])
    return out


def compare_baselines(scored: pd.DataFrame, budget: float,
                      interventions: Optional[List[str]] = None,
                      currency: str = "radiant",
                      objective: str = "max_priority_area") -> Dict[str, object]:
    """Optimized plan vs three naive strategies, on identical budget and objective."""
    cands = iv.candidates(scored, interventions, currency, objective)
    runs = {
        "greedy": greedy(cands, budget),
        "even_spread": baseline_even_spread(cands, budget),
        "hottest_first": baseline_hottest_first(cands, scored, budget),
        "random": baseline_random(cands, budget),
    }
    rows = {}
    for name, picks in runs.items():
        s = summarise(picks, scored, budget, currency, objective)
        rows[name] = {
            "objective_value": s["objective_value"],
            "spent": s["spent"],
            "budget_utilisation": s["budget_utilisation"],
            "block_groups_funded": s["block_groups_funded"],
            "people_reached": s["people_reached"],
            "canopy_added_m2": s["canopy_added_m2"],
            "canopy_gap_closed_pct_aoi": s["canopy_gap_closed_pct_aoi"],
            "equity_share": s["equity_share"],
        }
    best = rows["greedy"]["objective_value"]
    for name, r in rows.items():
        r["vs_optimized_pct"] = (round((r["objective_value"] - best) / best * 100, 2)
                                 if best > 0 else 0.0)
    return {"budget": float(budget), "currency": currency, "objective": objective,
            "strategies": rows,
            "note": ("All four strategies face the same budget, capacities and costs. "
                     "'vs_optimized_pct' is each strategy's objective value relative to the "
                     "greedy plan.")}


def frontier(scored: pd.DataFrame, budget: float,
             interventions: Optional[List[str]] = None,
             currency: str = "radiant",
             objective: str = "max_priority_area",
             steps: int = 11) -> Dict[str, object]:
    """
    Sweep the equity floor from 0 to 100% and measure what each increment costs in efficiency.
    This is the honest way to present an equity constraint: as a trade-off with a price tag.
    """
    cands = iv.candidates(scored, interventions, currency, objective)
    pts = []
    for i in range(steps):
        p = i / (steps - 1) if steps > 1 else 0.0
        picks = two_pool_greedy(cands, scored, budget, p) if p > 0 else greedy(cands, budget)
        s = summarise(picks, scored, budget, currency, objective, p)
        pts.append({
            "equity_floor": round(p, 4),
            "equity_share_achieved": s["equity_share"],
            "objective_value": s["objective_value"],
            "people_reached": s["people_reached"],
            "canopy_added_m2": s["canopy_added_m2"],
            "block_groups_funded": s["block_groups_funded"],
            "spent": s["spent"],
        })
    base = pts[0]["objective_value"] or 1.0
    for p in pts:
        p["efficiency_retained_pct"] = round(p["objective_value"] / base * 100, 3)
    # The floor only starts costing anything above the share the unconstrained plan already
    # achieves. Reporting that threshold lets the UI explain the flat segment of the curve.
    binding_from = pts[0]["equity_share_achieved"]
    knee = next((p["equity_floor"] for p in pts
                 if p["efficiency_retained_pct"] < 99.999), None)
    return {"budget": float(budget), "currency": currency, "objective": objective,
            "priority_definition": ("upper half of hei_sensitivity within the AOI -- the "
                                    "dimension the efficiency objective ignores"),
            "binding_from_floor": binding_from,
            "first_costly_floor": knee,
            "note": ("Floors at or below %.0f%% are free: the unconstrained plan already sends "
                     "that share to priority block groups. The curve is flat there because the "
                     "constraint is slack, not because the model is insensitive."
                     % (binding_from * 100)),
            "points": pts}


if __name__ == "__main__":
    import data
    import exposure

    df, _ = data.load()
    scored = exposure.compute(df)

    B = 2_000_000.0
    print("=" * 86)
    print("SCENARIO  budget $%s  radiant  max_priority_area" % format(int(B), ","))
    print("=" * 86)
    r = allocate(scored, B, verify=True)
    for k in ("spent", "budget_utilisation", "objective_value", "block_groups_funded",
              "canopy_added_m2", "canopy_gap_closed_pct_aoi", "people_reached",
              "people_reached_pct", "equity_share"):
        print("  %-28s %s" % (k, r[k]))
    print("  trees: %s   cool_roof m2: %s   shade: %s"
          % (r["by_intervention"]["tree"]["units"],
             r["by_intervention"]["cool_roof"]["units"],
             r["by_intervention"]["shade"]["units"]))
    o = r["optimality"]
    print("\n  LP bound %.2f   achieved %.2f   gap %.6f%%"
          % (o["lp_upper_bound"], o["achieved"], o["gap_pct"]))
    print("  MILP (scipy/HiGHS): %s" % o.get("milp_check"))
    for w in r["warnings"]:
        print("  ! %s" % w)

    print("\n" + "=" * 86)
    print("BASELINES")
    print("=" * 86)
    cb = compare_baselines(scored, B)
    print(pd.DataFrame(cb["strategies"]).T.to_string())

    print("\n" + "=" * 86)
    print("EQUITY-EFFICIENCY FRONTIER")
    print("=" * 86)
    fr = frontier(scored, B)
    print(pd.DataFrame(fr["points"]).to_string(index=False))
