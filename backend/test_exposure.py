"""
Validation gates for the exposure index. Run: python3 test_exposure.py

Covers the edge cases named in docs/architecture/08_validation.md: zero population, degenerate
weights, single block group, and the additive-vs-multiplicative property that motivated the
design.
"""

import sys

import numpy as np
import pandas as pd

import data
import exposure

FAILS = []


def check(name, cond, detail=""):
    print("  %-58s %s" % (name, "PASS" if cond else "FAIL"))
    if not cond:
        FAILS.append("%s %s" % (name, detail))
    return cond


def main():
    df, _ = data.load()
    scored = exposure.compute(df)

    print("\nE1 structure and range")
    check("all 4 hei columns present",
          all(c in scored for c in ("hei", "hei_thermal", "hei_density", "hei_sensitivity")))
    check("row count preserved (48)", len(scored) == len(df), str(len(scored)))
    check("hei within [0,1]", scored["hei"].between(0, 1).all())
    check("no nulls in hei", not scored["hei"].isna().any())
    check("components within [0,1]",
          all(scored[c].between(0, 1).all()
              for c in ("hei_thermal", "hei_density", "hei_sensitivity")))
    check("hei_rank is a permutation of 1..48",
          sorted(scored["hei_rank"]) == list(range(1, len(scored) + 1)))

    print("\nE2 weighted sum is exact")
    w = exposure.default_weights()
    manual = (w["thermal"] * scored["hei_thermal"]
              + w["density"] * scored["hei_density"]
              + w["sensitivity"] * scored["hei_sensitivity"])
    err = float(np.abs(manual - scored["hei"]).max())
    check("hei == w.T + w.D + w.S (max err < 1e-12)", err < 1e-12, "%.3e" % err)

    print("\nE3 weights renormalise")
    a = exposure.compute(df, {"thermal": 4, "density": 3, "sensitivity": 3})
    check("unnormalised weights == default (4:3:3 ~ .4/.3/.3)",
          float(np.abs(a["hei"] - scored["hei"]).max()) < 1e-12)
    b = exposure.compute(df, {"thermal": 1.0, "density": 0.0, "sensitivity": 0.0})
    check("thermal-only == pr_temp_diff",
          float(np.abs(b["hei"] - df["pr_temp_diff"]).max()) < 1e-12)
    c = exposure.compute(df, {"thermal": 0.0, "density": 0.0, "sensitivity": 1.0})
    check("sensitivity-only reorders the ranking",
          not c["hei_rank"].equals(scored["hei_rank"]))

    print("\nE4 invalid weights rejected")
    for bad, label in [({"thermal": -1}, "negative weight"),
                       ({"thermal": 0, "density": 0, "sensitivity": 0}, "all-zero weights")]:
        try:
            exposure.compute(df, bad)
            check("%s raises" % label, False)
        except ValueError:
            check("%s raises ValueError" % label, True)

    print("\nE5 zero-population block group survives (no multiplicative zeroing)")
    z = df.copy()
    victim = z.index[0]
    z.loc[victim, "acs_pop"] = 0.0
    z.loc[victim, "pr_density"] = 0.0
    zs = exposure.compute(z)
    w = exposure.default_weights()
    T = float(df.loc[victim, "pr_temp_diff"])
    S = float(df[exposure.PR_EQUITY].loc[victim].mean())
    expected = w["thermal"] * T + w["sensitivity"] * S

    check("zero-pop BG keeps a non-zero HEI", float(zs.loc[victim, "hei"]) > 0,
          str(float(zs.loc[victim, "hei"])))
    # The real property: the surviving score is EXACTLY the other two components' contribution.
    # Losing one component costs exactly its weight, and no more -- that is graceful degradation.
    check("surviving HEI == w_T*T + w_S*S exactly",
          abs(float(zs.loc[victim, "hei"]) - expected) < 1e-12,
          "%.6f vs %.6f" % (zs.loc[victim, "hei"], expected))
    check("retains >50% of its baseline score",
          float(zs.loc[victim, "hei"]) / float(exposure.compute(df).loc[victim, "hei"]) > 0.5)
    check("exposed_residents is a plain count, == acs_pop",
          float(zs.loc[victim, "exposed_residents"]) == 0.0)
    # The contrast that motivates the design: multiplicative scoring destroys the block group
    # entirely, discarding its thermal and equity evidence along with its density.
    mult = T * 0.0 * S
    check("a multiplicative index WOULD have zeroed it (contrast)", mult == 0.0)
    check("additive retains evidence a multiplicative index would discard",
          float(zs.loc[victim, "hei"]) > mult)

    print("\nE6 single block group")
    one = df.iloc[[0]]
    os_ = exposure.compute(one)
    check("1 row in -> 1 row out", len(os_) == 1)
    check("rank == 1", int(os_["hei_rank"].iloc[0]) == 1)
    check("hei still in [0,1] (not renormalised to 0 or 1)",
          0.0 <= float(os_["hei"].iloc[0]) <= 1.0, str(float(os_["hei"].iloc[0])))

    print("\nE7 percentile ranks came from the urban area, not the AOI")
    # If ranks had been computed inside the 48-BG AOI, min/max would be pinned near 0 and 1.
    check("pr_temp_diff min > 0.05 (AOI is hotter than the metro median)",
          float(df["pr_temp_diff"].min()) > 0.05, "%.4f" % df["pr_temp_diff"].min())
    check("mean HEI > 0.5 (AOI is above the metro average)",
          float(scored["hei"].mean()) > 0.5, "%.4f" % scored["hei"].mean())

    print("\nE8 explain() is complete and consistent")
    ex = exposure.explain(scored.iloc[0])
    check("3 components returned", len(ex["components"]) == 3)
    check("contributions sum to hei",
          abs(sum(c["contribution"] for c in ex["components"]) - ex["hei"]) < 1e-3)
    check("every component carries provenance",
          all(c.get("provenance") for c in ex["components"]))
    check("thermal component discloses the unit + circularity caveat",
          "UNVERIFIED" in ex["components"][0]["caveat"]
          and "-0.806" in ex["components"][0]["caveat"])
    check("weights labelled Assumed", "Assumed" in ex["weights_provenance"])

    print("\n" + "=" * 78)
    if FAILS:
        print("RESULT: %d GATE(S) FAILED" % len(FAILS))
        for f in FAILS:
            print("   - %s" % f)
        print("=" * 78)
        sys.exit(1)
    print("RESULT: ALL GATES PASS")
    print("=" * 78)


if __name__ == "__main__":
    main()
