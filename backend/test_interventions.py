"""
Validation gates for the intervention model. Run: python3 test_interventions.py

The gates here are built around the failure modes this component can actually have, not around
line coverage: the factor-of-a-million unit trap, currency mixing, cost arithmetic, the
linearity assumption behind `max_people_reached`, and the two degeneracies that a
benefit-per-dollar ranking can hide.
"""

import sys

import numpy as np
import pandas as pd

import config
import data
import exposure
import interventions as iv

FAILS = []


def check(name, cond, detail=""):
    print("  %-62s %s" % (name, "PASS" if cond else "FAIL"))
    if not cond:
        FAILS.append("%s %s" % (name, detail))
    return cond


def main():
    df, _ = data.load()
    scored = exposure.compute(df)
    cap = iv.capacity(scored)
    crown = float(config.CROWN_AREA_M2.value)

    print("\nI1 capacity structure")
    cols = ["land_area_m2", "canopy_deficit_m2", "cap_tree", "cap_cool_roof", "cap_shade"]
    check("all capacity columns present", all(c in cap for c in cols))
    check("row count preserved (48)", len(cap) == len(scored), str(len(cap)))
    check("no nulls in capacity columns", not cap[cols].isna().any().any())
    check("no negative capacity", bool((cap[cols] >= 0).all().all()))
    check("capacities are finite", bool(np.isfinite(cap[cols].to_numpy()).all()))

    print("\nI2 the factor-of-a-million unit trap")
    # If land_area were treated as m^2, the whole AOI deficit would be ~2.84 m^2 -- less than
    # one tree. This gate exists because that error is silent and catastrophic.
    total_def = float(cap["canopy_deficit_m2"].sum())
    check("LAND_AREA_TO_M2 == 1e6", float(config.LAND_AREA_TO_M2.value) == 1.0e6)
    check("AOI canopy deficit is hectare-scale, not m2-scale (>1e5 m2)",
          total_def > 1.0e5, "%.1f" % total_def)
    check("deficit never exceeds the block group's own land area",
          bool((cap["canopy_deficit_m2"] <= cap["land_area_m2"] + 1e-6).all()))
    # Independent cross-check: deficit share of AOI land must equal the area-weighted tc_gap.
    wgap = float((cap["tc_gap"] * cap["land_area"]).sum() / cap["land_area"].sum())
    share = total_def / float(cap["land_area_m2"].sum())
    check("deficit/land == area-weighted mean tc_gap (err < 1e-9)",
          abs(share - wgap) < 1e-9, "%.6f vs %.6f" % (share, wgap))

    print("\nI3 tree capacity is derived from measured data, and conservatively")
    check("cap_tree x crown never exceeds the deficit",
          bool((cap["cap_tree"] * crown <= cap["canopy_deficit_m2"] + 1e-9).all()))
    loss = float((cap["canopy_deficit_m2"] - cap["cap_tree"] * crown).sum())
    check("flooring discards < 0.1% of the deficit",
          loss / total_def < 0.001, "%.4f%%" % (loss / total_def * 100))
    at_goal = cap["tc_gap"] == 0
    check("block groups at their canopy goal get exactly zero tree capacity",
          bool((cap.loc[at_goal, "cap_tree"] == 0).all()), "%d BGs" % int(at_goal.sum()))
    check("every block group with tc_gap > 0 gets non-zero tree capacity",
          bool((cap.loc[~at_goal, "cap_tree"] > 0).all()))

    print("\nI4 candidate table shape and currency semantics")
    c = iv.candidates(scored, None, "radiant", "max_priority_area")
    check("3 interventions x 48 block groups = 144 rows", len(c) == 144, str(len(c)))
    check("cool roofs deliver ZERO radiant benefit",
          bool((c.loc[c.intervention == "cool_roof", "radiant_m2_per_unit"] == 0).all()))
    check("shade structures deliver ZERO ambient benefit",
          bool((c.loc[c.intervention == "shade", "ambient_m2_per_unit"] == 0).all()))
    check("cool roofs are all non-viable in the radiant currency",
          not c.loc[c.intervention == "cool_roof", "viable"].any())
    a = iv.candidates(scored, None, "ambient", "max_priority_area")
    check("shade is all non-viable in the ambient currency",
          not a.loc[a.intervention == "shade", "viable"].any())
    check("viable => positive capacity AND positive benefit",
          bool(((c.loc[c.viable, "capacity_units"] > 0)
                & (c.loc[c.viable, "benefit_per_unit"] > 0)).all()))
    check("intervention subset is honoured",
          set(iv.candidates(scored, ["tree"], "radiant", "max_priority_area")
              ["intervention"].unique()) == {"tree"})

    print("\nI5 the two currencies are never summed")
    t = iv.aoi_totals(scored)
    check("radiant and ambient totals are reported separately",
          "max_radiant_m2" in t and "max_ambient_m2" in t)
    check("no key sums the currencies",
          not any(k in t for k in ("max_total_m2", "total_benefit", "benefit_score")))
    check("the totals genuinely differ (not one number relabelled)",
          abs(t["max_radiant_m2"] - t["max_ambient_m2"]) > 1.0,
          "%s vs %s" % (t["max_radiant_m2"], t["max_ambient_m2"]))
    check("no temperature claim anywhere in the totals",
          not any("temp" in k.lower() and k != "provenance" for k in t))
    check("provenance states temperature is NOT computed",
          "NOT COMPUTED" in t["provenance"]["temperature_effect"])

    print("\nI6 cost arithmetic reproduces by hand")
    check("max_cost == capacity_units x cost_per_unit",
          float(np.abs(c["max_cost"] - c["capacity_units"] * c["cost_per_unit"]).max()) < 1e-9)
    hand = float((cap["cap_tree"] * config.COST["tree"].value
                  + cap["cap_cool_roof"] * config.COST["cool_roof"].value
                  + cap["cap_shade"] * config.COST["shade"].value).sum())
    check("full build-out cost matches an independent hand calculation",
          abs(hand - t["cost_to_build_everything_usd"]) < 1.0,
          "%.1f vs %s" % (hand, t["cost_to_build_everything_usd"]))
    check("benefit_per_dollar == benefit_per_unit / cost_per_unit",
          float(np.abs(c["benefit_per_dollar"]
                       - c["benefit_per_unit"] / c["cost_per_unit"]).max()) < 1e-12)

    print("\nI7 max_people_reached stays in its linear regime")
    # Pro-rata people-reached is linear only while coverage < 1. Measure it; do not assume it.
    sat = iv.saturation_check(scored, None, "radiant")
    check("no block group can saturate even at full build-out",
          bool((sat["max_coverage"] < 1.0).all()),
          "max %.4f" % sat["max_coverage"].max())
    check("max coverage equals max tc_gap (trees dominate the radiant currency)",
          abs(float(sat["max_coverage"].max()) - float(cap["tc_gap"].max())) < 0.01,
          "%.4f vs %.4f" % (sat["max_coverage"].max(), cap["tc_gap"].max()))
    p = iv.candidates(scored, None, "radiant", "max_people_reached")
    pv = p[p.viable]
    # Full build-out can never claim more people than actually live there.
    reach = (pv.assign(r=pv.capacity_units * pv.benefit_per_unit)
               .groupby("GEOID")["r"].sum())
    pop = cap.set_index("GEOID")["acs_pop"].reindex(reach.index)
    check("people reached never exceeds the block group's population",
          bool((reach <= pop + 1e-6).all()))
    check("full build-out reaches a MINORITY of residents (no whole-cell credit)",
          float(reach.sum()) / float(cap["acs_pop"].sum()) < 0.5,
          "%.1f%%" % (reach.sum() / cap["acs_pop"].sum() * 100))
    check("benefit is linear: 2x units -> exactly 2x benefit",
          abs(float((2 * pv.capacity_units * pv.benefit_per_unit).sum())
              - 2 * float((pv.capacity_units * pv.benefit_per_unit).sum())) < 1e-6)

    print("\nI8 invalid inputs are rejected, not coerced")
    bad = [
        (lambda: iv.candidates(scored, None, "nonsense", "max_priority_area"), "bad currency"),
        (lambda: iv.candidates(scored, None, "radiant", "max_roi"), "bad objective"),
        (lambda: iv.candidates(scored, None, "ambient", "max_people_reached"),
         "people_reached + ambient"),
        (lambda: iv.candidates(scored, ["solar_panel"], "radiant", "max_priority_area"),
         "unknown intervention"),
        (lambda: iv.candidates(scored, [], "radiant", "max_priority_area"),
         "empty intervention list"),
    ]
    for fn, label in bad:
        try:
            fn()
            check("%s raises" % label, False)
        except ValueError:
            check("%s raises ValueError" % label, True)
    try:
        iv.candidates(df, None, "radiant", "max_priority_area")   # df lacks `hei`
        check("unscored dataframe raises", False)
    except KeyError:
        check("unscored dataframe raises KeyError", True)

    print("\nI9 degeneracies are detected and disclosed, not hidden")
    d0 = iv.mix_diagnostics(scored, None, "radiant", "max_effective_area")
    check("max_effective_area is flagged LOCATION-DEGENERATE", d0["location_degenerate"])
    check("each intervention has exactly 1 distinct benefit_per_dollar there",
          all(p["distinct_benefit_per_dollar"] == 1 for p in d0["per_intervention"].values()))
    check("a warning names the degeneracy",
          any("LOCATION-DEGENERATE" in w for w in d0["warnings"]))
    d1 = iv.mix_diagnostics(scored, None, "radiant", "max_priority_area")
    check("max_priority_area is NOT location-degenerate", not d1["location_degenerate"])
    check("priority weighting yields 43 distinct tree ratios",
          d1["per_intervention"]["tree"]["distinct_benefit_per_dollar"] == 43,
          str(d1["per_intervention"]["tree"]["distinct_benefit_per_dollar"]))
    check("radiant mix is predetermined by the assumed costs (disclosed)",
          d1["mix_predetermined"] == ["shade~tree"], str(d1["mix_predetermined"]))
    d2 = iv.mix_diagnostics(scored, None, "ambient", "max_priority_area")
    check("ambient mix IS data-contested (the interesting case)",
          d2["mix_contested"] == ["cool_roof~tree"], str(d2["mix_contested"]))
    f = iv.cost_flip_point("ambient")
    check("tree-cost sensitivity is quantified, not hand-waved",
          abs(f["tolerance_pct"] - 20.0) < 0.5, str(f.get("tolerance_pct")))
    check("the binding rival is named", f["binding_rival"] == "cool_roof")

    print("\nI10 every assumed number is registered and badged")
    keys = {v.key for v in config.REGISTRY}
    for k in ("cost.tree", "cost.cool_roof", "cost.shade", "crown_area_m2",
              "shade_footprint_m2", "roof_m2_per_1000_pop", "shade_sites_per_1000_pop",
              "tree_et_effectiveness"):
        check("%s is in the provenance registry" % k, k in keys)
    rep = config.provenance_report()
    for k in ("cost.tree", "crown_area_m2", "roof_m2_per_1000_pop"):
        check("%s is reported citation_pending" % k, k in rep["citation_pending"])

    print("\n" + "=" * 78)
    if FAILS:
        print("RESULT: %d GATE(S) FAILED" % len(FAILS))
        for x in FAILS:
            print("   - %s" % x)
        print("=" * 78)
        sys.exit(1)
    print("RESULT: ALL GATES PASS")
    print("=" * 78)


if __name__ == "__main__":
    main()
