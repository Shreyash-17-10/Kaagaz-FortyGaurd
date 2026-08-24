"""
HeatROI — intervention capacity, cost and benefit (Component 4).

This module answers, for every (block group, intervention) pair: HOW MUCH can be built, WHAT
does it cost, and WHAT does it buy. It produces the candidate table the optimizer consumes.
It makes no allocation decisions.

--------------------------------------------------------------------------------------------
TWO CURRENCIES, NEVER SUMMED
--------------------------------------------------------------------------------------------
The inherited prototype summed `trees + m2_of_roof + shade_structures` into one "benefit
score". That is dimensionally meaningless. HeatROI reports benefit as effective treated area
in two physically distinct currencies and never adds them together:

  radiant  street-level shortwave interception -- what a pedestrian standing there feels.
           Tree crowns and shade structures deliver it. A cool roof delivers ~none of it at
           head height.
  ambient  neighbourhood-scale air-temperature effect via the surface energy balance.
           Cool-roof albedo and tree evapotranspiration deliver it. A shade sail changes
           essentially nothing about the surface energy balance.

Both are in m^2 of effective treated area, so values are additive WITHIN a currency and
interpretable ("this plan shades 61,000 m^2 of ground"). The multipliers that convert a unit
into effective area are ASSUMED and registered in config.py.

--------------------------------------------------------------------------------------------
WHAT IS REAL AND WHAT IS A PLACEHOLDER
--------------------------------------------------------------------------------------------
  tree capacity       DERIVED from measured data -- the TES canopy deficit. Real.
  cool-roof capacity  ASSUMED -- a per-capita rate. Nothing in this workspace bounds
                      retrofittable roof area. Needs building footprints.
  shade capacity      ASSUMED -- a per-capita rate. Needs MARTA GTFS stops.txt.

The prior prototype hard-coded max_roof_m2 = 1500 and max_shade = 3 for every block group with
no stated basis. These placeholders are no better as science, but they are badged, centralised,
user-editable, and reported as citation_pending, so they cannot be mistaken for measurements.

--------------------------------------------------------------------------------------------
NO TEMPERATURE CLAIM
--------------------------------------------------------------------------------------------
Nothing here converts effective area into degrees. That conversion requires a cited cooling
coefficient which this workspace does not contain. See config.REFUSED.
"""

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import config
from config import (COST, CROWN_AREA_M2, INTERVENTIONS, LAND_AREA_TO_M2,
                    ROOF_M2_PER_1000_POP, SHADE_SITES_PER_1000_POP, UNITS)

CURRENCIES = ["radiant", "ambient"]

# Objectives the optimizer may be asked to maximise. Each is a linear function of the number of
# units built, which is what keeps the allocation problem exactly solvable (see 05_optimization).
OBJECTIVES = {
    "max_effective_area": "Total effective treated area, in the chosen currency (m^2).",
    "max_priority_area": "Effective treated area weighted by each block group's HEI -- "
                         "cooling delivered where the index says it is needed most.",
    "max_people_reached": "Residents living inside the treated fraction of their own block "
                          "group. Radiant currency only. Pro-rata by area, NOT whole-cell "
                          "credit.",
}

# "Benefit per dollar" is deliberately NOT in this list. It is not a separate objective: it is
# the ratio the greedy optimizer sorts on, for whichever objective above is selected.


# ------------------------------------------------------------------------------------------
# Capacity
# ------------------------------------------------------------------------------------------

def capacity(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-block-group buildable capacity for each intervention.

    Adds:
      land_area_m2        land_area x 1e6                          [Derived, unit gate G6]
      canopy_deficit_m2   tc_gap x land_area_m2                    [Derived from Measured]
      cap_tree            floor(canopy_deficit_m2 / crown_area)    [Derived + Assumed crown]
      cap_cool_roof       acs_pop/1000 x roof_m2_per_1000_pop      [Assumed]
      cap_shade           round(acs_pop/1000 x sites_per_1000_pop) [Assumed]
    """
    out = df.copy()

    # The factor-of-a-million trap: land_area is km^2. Omitting this makes Atlanta's entire
    # canopy deficit compute as 155 m^2 -- about four trees. Proven by gate G6.
    out["land_area_m2"] = out["land_area"].astype(float) * LAND_AREA_TO_M2.value

    # Trees: the only capacity with a measured basis. tc_gap is already max(0, goal - canopy),
    # so a block group at or above its canopy goal gets exactly zero tree capacity.
    out["canopy_deficit_m2"] = out["tc_gap"].astype(float) * out["land_area_m2"]
    out["cap_tree"] = np.floor(out["canopy_deficit_m2"] / float(CROWN_AREA_M2.value))

    pop_k = out["acs_pop"].astype(float) / 1000.0
    out["cap_cool_roof"] = pop_k * float(ROOF_M2_PER_1000_POP.value)
    out["cap_shade"] = np.round(pop_k * float(SHADE_SITES_PER_1000_POP.value))

    for c in ("cap_tree", "cap_cool_roof", "cap_shade"):
        out[c] = out[c].clip(lower=0.0)

    return out


# ------------------------------------------------------------------------------------------
# Candidate table
# ------------------------------------------------------------------------------------------

def candidates(scored: pd.DataFrame,
               interventions: Optional[List[str]] = None,
               currency: str = "radiant",
               objective: str = "max_priority_area") -> pd.DataFrame:
    """
    One row per (block group, intervention) pair with capacity, cost and benefit.

    `scored` must already carry the exposure columns (exposure.compute).

    Returned columns:
      GEOID, intervention, unit
      capacity_units        max buildable
      cost_per_unit         USD                                    [Assumed]
      max_cost              capacity_units x cost_per_unit
      radiant_m2_per_unit, ambient_m2_per_unit                     [Assumed]
      area_per_unit         the selected currency's m^2 per unit
      benefit_per_unit      the selected objective's value per unit
      benefit_per_dollar    benefit_per_unit / cost_per_unit
      hei, acs_pop, land_area_m2, capacity_basis
    """
    if currency not in CURRENCIES:
        raise ValueError("currency must be one of %s, got %r" % (CURRENCIES, currency))
    if objective not in OBJECTIVES:
        raise ValueError("objective must be one of %s, got %r"
                         % (sorted(OBJECTIVES), objective))
    if objective == "max_people_reached" and currency != "radiant":
        raise ValueError(
            "max_people_reached is defined only in the radiant currency. 'Reached' means "
            "standing inside new shade; there is no honest per-person interpretation of an "
            "ambient effect without a cited cooling coefficient.")
    if "hei" not in scored.columns:
        raise KeyError("candidates() needs exposure columns -- call exposure.compute first")

    # `None` means "not specified, use all". An explicitly EMPTY list is a different thing and
    # must not silently become "all" -- a UI that unchecks every intervention would otherwise
    # get a full plan back.
    ivs = list(INTERVENTIONS) if interventions is None else list(interventions)
    unknown = [k for k in ivs if k not in INTERVENTIONS]
    if unknown:
        raise ValueError("unknown intervention(s) %s; supported: %s" % (unknown, INTERVENTIONS))
    if not ivs:
        raise ValueError("at least one intervention must be enabled")

    cap = capacity(scored)
    radiant = config.radiant_m2_per_unit()
    ambient = config.ambient_m2_per_unit()
    cap_col = {"tree": "cap_tree", "cool_roof": "cap_cool_roof", "shade": "cap_shade"}
    basis = {
        "tree": "Derived from Measured (TES canopy deficit / assumed crown area)",
        "cool_roof": "Assumed (per-capita rate; needs building footprints)",
        "shade": "Assumed (per-capita rate; needs MARTA GTFS stops.txt)",
    }

    rows = []
    for k in ivs:
        block = pd.DataFrame({
            "GEOID": cap["GEOID"].values,
            "intervention": k,
            "unit": UNITS[k],
            "capacity_units": cap[cap_col[k]].astype(float).values,
            "cost_per_unit": float(COST[k].value),
            "radiant_m2_per_unit": float(radiant[k]),
            "ambient_m2_per_unit": float(ambient[k]),
            "hei": cap["hei"].astype(float).values,
            "acs_pop": cap["acs_pop"].astype(float).values,
            "land_area_m2": cap["land_area_m2"].astype(float).values,
            "capacity_basis": basis[k],
        })
        rows.append(block)

    c = pd.concat(rows, ignore_index=True)
    c["max_cost"] = c["capacity_units"] * c["cost_per_unit"]
    c["area_per_unit"] = c["radiant_m2_per_unit"] if currency == "radiant" \
        else c["ambient_m2_per_unit"]

    if objective == "max_effective_area":
        c["benefit_per_unit"] = c["area_per_unit"]
    elif objective == "max_priority_area":
        c["benefit_per_unit"] = c["hei"] * c["area_per_unit"]
    else:  # max_people_reached
        # Pro-rata: treating x% of a block group's land area reaches x% of its residents.
        # Per unit this is a constant -- population density times the unit's footprint -- which
        # keeps the objective linear. The saturation guard below proves the linear regime holds.
        c["benefit_per_unit"] = (c["acs_pop"] / c["land_area_m2"]) * c["area_per_unit"]

    c["benefit_per_dollar"] = np.where(c["cost_per_unit"] > 0,
                                       c["benefit_per_unit"] / c["cost_per_unit"], 0.0)

    # Drop pairs that can buy nothing (zero capacity) or deliver nothing in this currency
    # (e.g. cool roofs in the radiant currency). Keeping them would pad the candidate count
    # and let a zero-benefit line item appear in a plan.
    c["viable"] = (c["capacity_units"] > 0) & (c["benefit_per_unit"] > 0)

    c.attrs["currency"] = currency
    c.attrs["objective"] = objective
    c.attrs["interventions"] = ivs
    return c


def saturation_check(scored: pd.DataFrame,
                     interventions: Optional[List[str]] = None,
                     currency: str = "radiant") -> pd.DataFrame:
    """
    Coverage if EVERY intervention were built to capacity in every block group.

    Why this exists: `max_people_reached` is pro-rata by area, so it is only linear while
    coverage stays below 1.0. If full build-out cannot saturate any block group, the linear
    form is exact everywhere in the feasible region and greedy-by-ratio remains an exact
    solution to the fractional relaxation. This function measures that, rather than assuming it.
    """
    c = candidates(scored, interventions, currency, "max_effective_area")
    tot = (c.assign(area=c["capacity_units"] * c["area_per_unit"])
             .groupby("GEOID")["area"].sum())
    cap = capacity(scored)
    out = pd.DataFrame({
        "max_treated_m2": tot.reindex(cap["GEOID"].values).fillna(0.0).values,
        "land_area_m2": cap["land_area_m2"].values,
    }, index=cap["GEOID"].values)
    out["max_coverage"] = out["max_treated_m2"] / out["land_area_m2"]
    return out


def mix_diagnostics(scored: pd.DataFrame,
                    interventions: Optional[List[str]] = None,
                    currency: str = "radiant",
                    objective: str = "max_priority_area") -> Dict[str, object]:
    """
    Two degeneracies that a benefit-per-dollar optimizer can hide. Both are MEASURED here and
    surfaced through the API so the UI can warn instead of quietly presenting an arbitrary plan.

    1. LOCATION DEGENERACY. If `benefit_per_dollar` takes only one value per intervention, the
       objective has no opinion about WHERE to build -- it only ranks intervention types. The
       resulting map is an arbitrary tie-break, not a recommendation. This is exactly the case
       for `max_effective_area`: cost and effective area per unit are both constants, so every
       block group ties.

    2. MIX PREDETERMINATION. If the per-dollar ranges of two interventions do not overlap, the
       cheaper-per-benefit one is bought to full capacity before the other is touched at all.
       The mix is then a consequence of the ASSUMED unit costs, not of the data. With the
       inherited uncited costs this happens in three of the four (currency, objective) combos.
    """
    c = candidates(scored, interventions, currency, objective)
    v = c[c["viable"]]
    if v.empty:
        return {"currency": currency, "objective": objective, "viable_candidates": 0,
                "location_degenerate": True, "per_intervention": {}, "mix_contested": [],
                "mix_predetermined": [], "warnings": ["No viable candidates."]}

    per = {}
    for k, g in v.groupby("intervention"):
        per[k] = {
            "candidates": int(len(g)),
            "distinct_benefit_per_dollar": int(g["benefit_per_dollar"].nunique()),
            "ratio_min": float(g["benefit_per_dollar"].min()),
            "ratio_max": float(g["benefit_per_dollar"].max()),
            "cost_to_exhaust_usd": float(g["max_cost"].sum()),
        }

    location_degenerate = all(p["distinct_benefit_per_dollar"] <= 1 for p in per.values())

    ks = sorted(per)
    contested, predetermined = [], []
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            a, b = per[ks[i]], per[ks[j]]
            pair = "%s~%s" % (ks[i], ks[j])
            if a["ratio_min"] <= b["ratio_max"] and b["ratio_min"] <= a["ratio_max"]:
                contested.append(pair)
            else:
                predetermined.append(pair)

    warnings = []
    if location_degenerate:
        warnings.append(
            "LOCATION-DEGENERATE: under '%s' every block group has an identical "
            "benefit-per-dollar for a given intervention, so the spatial allocation is an "
            "arbitrary tie-break. Use 'max_priority_area' or 'max_people_reached' to make "
            "location matter." % objective)
    for pair in predetermined:
        a, b = pair.split("~")
        first, second = (a, b) if per[a]["ratio_min"] > per[b]["ratio_max"] else (b, a)
        warnings.append(
            "MIX PREDETERMINED (%s): %s outranks %s at every block group, so %s is bought to "
            "full capacity ($%s) before any %s. The ordering follows from the ASSUMED unit "
            "costs, which are citation_pending."
            % (pair, first, second, first,
               format(int(per[first]["cost_to_exhaust_usd"]), ","), second))
    return {
        "currency": currency,
        "objective": objective,
        "viable_candidates": int(len(v)),
        "per_intervention": per,
        "location_degenerate": location_degenerate,
        "mix_contested": contested,
        "mix_predetermined": predetermined,
        "warnings": warnings,
    }


def cost_flip_point(currency: str = "ambient") -> Dict[str, object]:
    """
    How wrong can the tree unit cost be before the intervention ranking flips?

    The tree cost ($500, uncited, inherited) is the project's largest credibility risk. This
    reports the cost at which trees tie the best competing intervention on benefit per dollar,
    ignoring HEI weighting, so the sensitivity can be stated as a percentage rather than
    hand-waved.
    """
    area = config.radiant_m2_per_unit() if currency == "radiant" \
        else config.ambient_m2_per_unit()
    tree_area = float(area["tree"])
    rivals = {}
    for k in INTERVENTIONS:
        if k == "tree" or area[k] <= 0:
            continue
        rival_ratio = float(area[k]) / float(COST[k].value)
        rivals[k] = round(tree_area / rival_ratio, 2) if rival_ratio > 0 else None
    cur_cost = float(COST["tree"].value)
    best = None
    for k, tie in rivals.items():
        if tie is None:
            continue
        if best is None or abs(tie - cur_cost) < abs(rivals[best] - cur_cost):
            best = k
    out = {
        "currency": currency,
        "tree_cost_assumed_usd": cur_cost,
        "tie_cost_vs": rivals,
        "binding_rival": best,
    }
    if best is not None:
        tie = rivals[best]
        out["tolerance_pct"] = round(abs(tie - cur_cost) / cur_cost * 100.0, 1)
        out["note"] = ("A %.1f%% error in the assumed tree cost flips trees and %s in the %s "
                       "currency." % (out["tolerance_pct"], best, currency))
    else:
        out["note"] = "Trees have no competing intervention in the %s currency." % currency
    return out


def aoi_totals(scored: pd.DataFrame,
               interventions: Optional[List[str]] = None) -> Dict[str, object]:
    """Headline capacity facts for the AOI, with provenance on each line."""
    cap = capacity(scored)
    c = candidates(scored, interventions, "radiant", "max_effective_area")
    r = candidates(scored, interventions, "ambient", "max_effective_area")
    return {
        "block_groups": int(len(cap)),
        "eligible_for_trees": int((cap["cap_tree"] > 0).sum()),
        "canopy_deficit_m2": round(float(cap["canopy_deficit_m2"].sum()), 1),
        "canopy_deficit_ha": round(float(cap["canopy_deficit_m2"].sum()) / 10_000.0, 2),
        "max_trees": int(cap["cap_tree"].sum()),
        "max_cool_roof_m2": round(float(cap["cap_cool_roof"].sum()), 1),
        "max_shade_sites": int(cap["cap_shade"].sum()),
        "cost_to_build_everything_usd": round(float(
            (cap["cap_tree"] * COST["tree"].value
             + cap["cap_cool_roof"] * COST["cool_roof"].value
             + cap["cap_shade"] * COST["shade"].value).sum()), 0),
        "max_radiant_m2": round(float(
            (c["capacity_units"] * c["radiant_m2_per_unit"]).sum()), 1),
        "max_ambient_m2": round(float(
            (r["capacity_units"] * r["ambient_m2_per_unit"]).sum()), 1),
        "provenance": {
            "canopy_deficit_m2": "Derived from Measured (tc_gap x land_area x 1e6)",
            "max_trees": "Derived + Assumed crown area %.0f m2" % CROWN_AREA_M2.value,
            "max_cool_roof_m2": "Assumed (%.0f m2 per 1000 residents)"
                                % ROOF_M2_PER_1000_POP.value,
            "max_shade_sites": "Assumed (%.1f sites per 1000 residents)"
                               % SHADE_SITES_PER_1000_POP.value,
            "cost_to_build_everything_usd": "Assumed unit costs, all citation_pending",
            "temperature_effect": "NOT COMPUTED -- no cited cooling coefficient",
        },
    }


if __name__ == "__main__":
    import data
    import exposure

    df, _ = data.load()
    scored = exposure.compute(df)

    print("AOI CAPACITY")
    for k, v in aoi_totals(scored).items():
        if k == "provenance":
            continue
        print("  %-32s %s" % (k, v))

    print("\nSATURATION (radiant, all interventions at full capacity)")
    sat = saturation_check(scored)
    print("  max coverage: min %.4f  median %.4f  max %.4f"
          % (sat["max_coverage"].min(), sat["max_coverage"].median(),
             sat["max_coverage"].max()))
    print("  block groups that could saturate (coverage >= 1): %d"
          % int((sat["max_coverage"] >= 1.0).sum()))

    for obj in ("max_effective_area", "max_priority_area", "max_people_reached"):
        cur = "radiant"
        c = candidates(scored, None, cur, obj)
        v = c[c["viable"]]
        print("\n%s  [%s currency]  %d viable candidates of %d"
              % (obj, cur, len(v), len(c)))
        g = (v.groupby("intervention")
              .agg(pairs=("GEOID", "size"),
                   units=("capacity_units", "sum"),
                   max_cost=("max_cost", "sum"),
                   bpd_min=("benefit_per_dollar", "min"),
                   bpd_max=("benefit_per_dollar", "max")))
        print(g.to_string(float_format=lambda x: "%.6g" % x))

    print("\n" + "=" * 86)
    print("MIX DIAGNOSTICS")
    print("=" * 86)
    for cur in CURRENCIES:
        for obj in ("max_effective_area", "max_priority_area"):
            d = mix_diagnostics(scored, None, cur, obj)
            print("\n%s / %s  -- %d viable, location_degenerate=%s"
                  % (cur, obj, d["viable_candidates"], d["location_degenerate"]))
            print("  contested: %s   predetermined: %s"
                  % (d["mix_contested"] or "none", d["mix_predetermined"] or "none"))
            for w in d["warnings"]:
                print("  ! " + w)

    print("\n" + "=" * 86)
    print("COST SENSITIVITY")
    print("=" * 86)
    for cur in CURRENCIES:
        f = cost_flip_point(cur)
        print("  %-8s %s" % (cur, f["note"]))
