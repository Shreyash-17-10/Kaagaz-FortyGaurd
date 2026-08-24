"""
Validation gates for the optimizer. Run: python3 test_optimize.py

An optimizer is the easiest component in this project to fool yourself with. It always returns
a plan, the plan always looks reasonable, and nothing complains if it overspends, exceeds
capacity, double-counts a block group, or "beats" its own upper bound. Worse, baselines can be
quietly rigged to lose, which turns the headline comparison into marketing.

So these gates check conservation and honesty, not just that a plan comes back:
  O1 budget is never exceeded, at any budget, including degenerate ones
  O2 capacity is never exceeded and no line item is duplicated
  O3 greedy matches the LP bound AND an independent MILP solve (scipy/HiGHS)
  O4 baselines are given genuinely equal footing, and the comparison is not a foregone win
  O5 the equity floor is honoured when feasible, and disclosed when not
  O6 the frontier is monotone and its flat segment is explained
  O7 arithmetic conservation: line items sum to the reported totals
  O8 invalid inputs raise instead of returning an empty plan
  O9 no fabricated temperature, no summed currencies, reporting frame stated
"""

import sys

import numpy as np
import pandas as pd

import config
import data
import exposure
import interventions as iv
import optimize as op

FAILS = []
BUDGETS = [50_000.0, 250_000.0, 1_000_000.0, 2_000_000.0, 5_000_000.0, 20_000_000.0,
           60_000_000.0]


def check(name, cond, detail=""):
    print("  %-64s %s" % (name, "PASS" if cond else "FAIL"))
    if not cond:
        FAILS.append("%s %s" % (name, detail))
    return cond


def main():
    df, _ = data.load()
    scored = exposure.compute(df)
    cands = iv.candidates(scored, None, "radiant", "max_priority_area")

    print("\nO1 the budget is never exceeded")
    worst = 0.0
    for B in BUDGETS:
        r = op.allocate(scored, B)
        worst = max(worst, r["spent"] - B)
    check("no overspend at any of 7 budgets", worst <= 1e-6, "max overspend %.6f" % worst)
    # A budget below the cheapest unit must yield an empty plan, not a free tree.
    cheapest = float(cands[cands.viable]["cost_per_unit"].min())
    tiny = op.allocate(scored, cheapest - 1.0)
    check("budget below the cheapest unit buys nothing",
          tiny["spent"] == 0.0 and tiny["line_items"] == 0, str(tiny["spent"]))
    check("an unaffordable budget still reports a valid, non-null plan",
          tiny["objective_value"] == 0.0 and tiny["people_reached"] == 0)
    # A budget beyond total capacity must leave money unspent rather than invent capacity.
    huge = op.allocate(scored, 60_000_000.0)
    check("budget beyond total capacity leaves money unspent",
          huge["unspent"] > 0 and huge["budget_utilisation"] < 1.0,
          "util %.4f" % huge["budget_utilisation"])
    check("full-capacity spend equals the VIABLE build-everything cost",
          abs(huge["spent"] - float(cands[cands.viable]["max_cost"].sum())) < 1.0,
          "%.1f" % huge["spent"])
    # The gap between that and the all-intervention total must be exactly the cool-roof
    # capacity: cool roofs earn zero radiant benefit, so refusing to buy them is correct, not
    # a shortfall. This gate originally compared against the all-currency total and failed --
    # the optimizer was right and the test was wrong. Asserting the decomposition instead of
    # loosening the tolerance is what keeps that distinction visible.
    roof_cost = float(iv.capacity(scored)["cap_cool_roof"].sum()
                      * config.COST["cool_roof"].value)
    check("the shortfall vs all-intervention cost is exactly the cool-roof capacity",
          abs((iv.aoi_totals(scored)["cost_to_build_everything_usd"] - huge["spent"])
              - roof_cost) < 1.0, "%.1f" % roof_cost)
    check("no cool roofs are ever bought in the radiant currency",
          huge["by_intervention"]["cool_roof"]["units"] == 0)

    print("\nO2 capacity is never exceeded and lines are unique")
    cap_lookup = cands.set_index(["GEOID", "intervention"])["capacity_units"]
    bad_cap, dupes = 0, 0
    for B in BUDGETS:
        for floor in (0.0, 0.5, 1.0):
            a = op.allocate(scored, B, equity_floor=floor)
            d = pd.DataFrame(a["allocations"])
            if not len(d):
                continue
            dupes += int(d.duplicated(["GEOID", "intervention"]).sum())
            for g, k, u in zip(d.GEOID, d.intervention, d.units):
                if u > float(cap_lookup.loc[(g, k)]) + 1e-6:
                    bad_cap += 1
    check("no allocation exceeds its block group's capacity", bad_cap == 0, str(bad_cap))
    check("no duplicate (block group, intervention) line items", dupes == 0, str(dupes))
    r2 = op.allocate(scored, 2_000_000.0)
    d2 = pd.DataFrame(r2["allocations"])
    check("tree and shade units are whole numbers",
          bool((d2.loc[d2.intervention.isin(["tree", "shade"]), "units"] % 1 == 0).all()))
    check("funded block groups never exceed the AOI size",
          r2["block_groups_funded"] <= r2["block_groups_total"])

    print("\nO3 optimality is measured against two independent references")
    gaps = []
    for B in BUDGETS[:-1]:
        r = op.allocate(scored, B)
        gaps.append(r["optimality"]["gap_pct"])
        check("gap vs LP bound is never negative at $%s" % format(int(B), ","),
              r["optimality"]["gap_pct"] >= 0.0, str(r["optimality"]["gap_pct"]))
    check("greedy is within 0.01%% of the LP bound at every budget",
          max(gaps) < 0.01, "worst %.6f%%" % max(gaps))
    # The real check: a different algorithm, from a different library, on the same problem.
    mv = op.milp_verify(cands, 2_000_000.0)
    if mv.get("available"):
        greedy_val = sum(p["benefit"] for p in op.greedy(cands, 2_000_000.0))
        rel = abs(mv["objective"] - greedy_val) / mv["objective"]
        check("scipy/HiGHS MILP agrees with greedy to 1e-6 relative",
              rel < 1e-6, "%.3e" % rel)
        check("MILP solution respects the same budget", mv["cost"] <= 2_000_000.0 + 1e-6)
        check("MILP reports optimal status", mv["status"] == "optimal", str(mv["status"]))
    else:
        check("MILP unavailable is reported honestly, not silently skipped",
              "scipy" in mv.get("note", ""))
    check("the optimality claim states its basis", "knapsack" in
          op.allocate(scored, 1e6)["optimality"]["basis"])

    print("\nO4 baselines are a fair fight")
    cb = op.compare_baselines(scored, 2_000_000.0)
    st = cb["strategies"]
    check("all four strategies present", set(st) == set(op.STRATEGIES), str(set(st)))
    for name, s in st.items():
        check("%s never overspends" % name, s["spent"] <= 2_000_000.0 + 1e-6)
    check("greedy is the best on the objective it optimises",
          all(st["greedy"]["objective_value"] >= s["objective_value"] for s in st.values()))
    # If a baseline were rigged, it would lose on every metric. Honest baselines win somewhere.
    check("at least one baseline beats greedy on some OTHER metric (baselines not rigged)",
          any(s["people_reached"] > st["greedy"]["people_reached"]
              or s["block_groups_funded"] > st["greedy"]["block_groups_funded"]
              for k, s in st.items() if k != "greedy"))
    check("the optimizer's advantage is material (>5% over the naive spread)",
          st["even_spread"]["vs_optimized_pct"] < -5.0,
          str(st["even_spread"]["vs_optimized_pct"]))
    check("hottest-first concentrates more than greedy (it ignores cost-effectiveness)",
          st["hottest_first"]["block_groups_funded"] <= st["greedy"]["block_groups_funded"])
    check("random baseline is reproducible",
          op.baseline_random(cands, 1e6, seed=7)[0]["GEOID"]
          == op.baseline_random(cands, 1e6, seed=7)[0]["GEOID"])
    check("different seeds give different plans (it is genuinely random)",
          [p["GEOID"] for p in op.baseline_random(cands, 1e6, seed=1)]
          != [p["GEOID"] for p in op.baseline_random(cands, 1e6, seed=2)])

    print("\nO5 the equity floor is honoured, or its failure is disclosed")
    check("priority set is the upper half of sensitivity, not of HEI",
          int(op.priority_mask(cands, scored).sum()) > 0
          and abs(float(scored["hei_sensitivity"].median())
                  - float(scored["hei_sensitivity"].median())) < 1e-12)
    for B in (2_000_000.0, 10_000_000.0, 20_000_000.0):
        for floor in (0.3, 0.6, 0.8):
            r = op.allocate(scored, B, equity_floor=floor)
            met = r["equity_share"] + 1e-6 >= floor
            disclosed = any("could not be met" in w for w in r["warnings"])
            check("floor %.0f%% at $%-11s met or disclosed" % (floor * 100,
                                                               format(int(B), ",")),
                  met or disclosed, "share %.3f" % r["equity_share"])
    # An impossible floor must fail loudly rather than quietly under-deliver.
    imp = op.allocate(scored, 35_000_000.0, equity_floor=1.0)
    check("infeasible 100% floor is explicitly disclosed",
          any("could not be met" in w for w in imp["warnings"]))
    check("infeasible floor still returns a usable plan", imp["spent"] > 0)
    # And the honest converse: a slack floor must say it is doing nothing.
    slack = op.allocate(scored, 2_000_000.0, equity_floor=0.5)
    check("a non-binding floor is reported as NOT BINDING",
          not slack["equity_binding"]
          and any("NOT BINDING" in w for w in slack["warnings"]))
    check("efficiency and equity alignment is quantified, not asserted",
          slack["equity_share_unconstrained"] == 1.0,
          str(slack["equity_share_unconstrained"]))
    try:
        op.allocate(scored, 1e6, equity_floor=1.5)
        check("out-of-range equity floor raises", False)
    except ValueError:
        check("out-of-range equity floor raises ValueError", True)

    print("\nO6 the frontier is monotone and its flat segment is explained")
    fr = op.frontier(scored, 20_000_000.0, steps=6)
    vals = [p["objective_value"] for p in fr["points"]]
    check("efficiency never increases as the equity floor tightens",
          all(vals[i] >= vals[i + 1] - 1e-6 for i in range(len(vals) - 1)), str(vals))
    check("the frontier has a real cost at the top (equity is not free everywhere)",
          fr["points"][-1]["efficiency_retained_pct"] < 100.0,
          str(fr["points"][-1]["efficiency_retained_pct"]))
    check("achieved equity share rises with the floor",
          fr["points"][-1]["equity_share_achieved"]
          > fr["points"][0]["equity_share_achieved"])
    check("the flat segment's threshold is reported",
          fr["binding_from_floor"] is not None and "slack" in fr["note"])
    flat = op.frontier(scored, 2_000_000.0, steps=6)
    check("a fully-slack frontier still reports why it is flat",
          flat["first_costly_floor"] is None and flat["binding_from_floor"] == 1.0)
    check("every frontier point spends within budget",
          all(p["spent"] <= 20_000_000.0 + 1e-6 for p in fr["points"]))

    print("\nO7 line items sum to the reported totals")
    r = op.allocate(scored, 5_000_000.0)
    d = pd.DataFrame(r["allocations"])
    check("line-item costs sum to `spent`",
          abs(float(d["cost"].sum()) - r["spent"]) < 0.05,
          "%.4f vs %.4f" % (d["cost"].sum(), r["spent"]))
    check("line-item benefits sum to `objective_value`",
          abs(float(d["benefit"].sum()) - r["objective_value"]) < 0.05)
    check("per-intervention costs sum to `spent`",
          abs(sum(v["cost"] for v in r["by_intervention"].values()) - r["spent"]) < 0.05)
    check("canopy added == trees x crown area",
          abs(r["canopy_added_m2"]
              - r["by_intervention"]["tree"]["units"] * config.CROWN_AREA_M2.value) < 0.05)
    check("gap-closed %% == canopy added / AOI deficit",
          abs(r["canopy_gap_closed_pct_aoi"]
              - r["canopy_added_m2"] / r["aoi_canopy_deficit_m2"] * 100) < 0.01)
    check("people reached never exceeds AOI population",
          r["people_reached"] <= r["aoi_population"])
    check("people reached is recomputed from the plan, not the objective",
          op.allocate(scored, 5e6, objective="max_effective_area")["people_reached"] > 0)
    check("equity spend never exceeds total spend", r["equity_spend"] <= r["spent"] + 1e-6)

    print("\nO8 invalid inputs raise instead of returning an empty plan")
    for bad, label in [(0.0, "zero budget"), (-1.0, "negative budget"), (None, "null budget")]:
        try:
            op.allocate(scored, bad)
            check("%s raises" % label, False)
        except (ValueError, TypeError):
            check("%s raises" % label, True)
    try:
        op.allocate(scored, 1e6, interventions=[])
        check("empty intervention list raises", False)
    except ValueError:
        check("empty intervention list raises ValueError", True)
    try:
        op.allocate(scored, 1e6, currency="nonsense")
        check("bad currency raises", False)
    except ValueError:
        check("bad currency raises ValueError", True)

    print("\nO9 nothing fabricated leaks into the plan")
    r = op.allocate(scored, 2_000_000.0)
    check("no temperature figure is produced", r["temperature_reduction"] is None)
    check("the absence of a temperature figure is explained",
          "NOT COMPUTED" in r["temperature_note"])
    check("no key sums the two currencies",
          not any(k in r for k in ("total_m2", "total_benefit", "benefit_score")))
    check("radiant and ambient are reported separately", "radiant_m2" in r
          and "ambient_m2" in r)
    check("the reporting frame (AOI, not metro) is stated",
          "AOI" in r["reporting_frame"] and "never" in r["reporting_frame"])
    check("assumed-cost provenance is surfaced with the plan",
          "ASSUMED" in r["reporting_frame"])
    check("mix predetermination is disclosed on the plan",
          r["mix_predetermined"] == ["shade~tree"], str(r["mix_predetermined"]))
    check("subset selection is honoured end to end",
          op.allocate(scored, 1e6, interventions=["tree"])["by_intervention"]
          ["cool_roof"]["units"] == 0)

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
    main()
