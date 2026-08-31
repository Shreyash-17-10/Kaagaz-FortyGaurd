"""
HeatROI — heat exposure index (HEI).

HEI_i = w_T * T_i + w_D * D_i + w_S * S_i        in [0, 1]

  T_i  percentile rank of TES temp_diff            thermal load
  D_i  percentile rank of population density       who is there
  S_i  mean of the percentile ranks of the six     who is least able to cope
       TES equity indices

All three components are percentile ranks taken against the FULL Atlanta urban area (3,346
block groups) at build time, never across urban areas and never within the small AOI.

TWO DELIBERATE DESIGN CHOICES, both departures from the inherited prototype:

1. ADDITIVE, not multiplicative. The prior model computed need = f(heat) * f(opportunity), so a
   single zero component zeroed the whole score -- 53.7% of Georgia collapsed onto one identical
   score. Additive weighting degrades gracefully and the weights are legible to a non-technical
   reader.

2. RE-RANKED equity indices. The six TES *norm columns are already 0-1, so averaging them
   directly is tempting. But five of them (pctpocnorm, pctpovnorm, unemplnorm, depratnorm,
   lingnorm) are min-max rescaled inside each urban area -- 31/31 urban areas hit exactly 0.0 and
   exactly 1.0 -- while health_nor is not (only 5/31 and 4/31). Averaging as supplied silently
   weights the six unequally. Re-ranking within the urban area makes them commensurable.

The weights are ASSUMED policy choices, not physical constants, and are user-adjustable.
"""

from typing import Dict, Optional

import numpy as np
import pandas as pd

try:
    import config
    from config import DEFAULT_WEIGHTS, EQUITY_COLS
except ImportError:
    from backend import config
    from backend.config import DEFAULT_WEIGHTS, EQUITY_COLS

PR_EQUITY = ["pr_" + c for c in EQUITY_COLS]


def default_weights() -> Dict[str, float]:
    return {k: float(v.value) for k, v in DEFAULT_WEIGHTS.items()}


def normalize_weights(w: Optional[Dict[str, float]]) -> Dict[str, float]:
    """Fill missing keys from defaults, reject negatives, renormalise to sum 1."""
    out = default_weights()
    if w:
        for k in out:
            if k in w and w[k] is not None:
                v = float(w[k])
                if v < 0:
                    raise ValueError("weight %r must be >= 0, got %s" % (k, v))
                out[k] = v
    total = sum(out.values())
    if total <= 0:
        raise ValueError("weights must not all be zero")
    return {k: v / total for k, v in out.items()}


def compute(df: pd.DataFrame, weights: Optional[Dict[str, float]] = None) -> pd.DataFrame:
    """
    Adds the exposure columns to a copy of df and returns it.

    Added columns:
      hei_thermal, hei_density, hei_sensitivity   the three components, each in [0, 1]
      hei                                         the weighted index, in [0, 1]
      hei_rank                                    1 = most exposed within the AOI
      exposed_residents                           acs_pop (a plain count, NOT hei * pop)
    """
    w = normalize_weights(weights)
    need = ["pr_temp_diff", "pr_density"] + PR_EQUITY
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise KeyError(
            "artifact lacks precomputed percentile ranks: %s -- rerun build/build_aoi.py"
            % missing)

    out = df.copy()
    out["hei_thermal"] = out["pr_temp_diff"].astype(float)
    out["hei_density"] = out["pr_density"].astype(float)
    out["hei_sensitivity"] = out[PR_EQUITY].astype(float).mean(axis=1)

    out["hei"] = (w["thermal"] * out["hei_thermal"]
                  + w["density"] * out["hei_density"]
                  + w["sensitivity"] * out["hei_sensitivity"])

    # Guard against a silent scaling error: a convex combination of [0,1] values must stay in
    # [0,1]. If this ever trips, a component is not a percentile rank.
    lo, hi = float(out["hei"].min()), float(out["hei"].max())
    if lo < -1e-9 or hi > 1 + 1e-9:
        raise AssertionError("hei outside [0,1]: min=%.6f max=%.6f" % (lo, hi))

    out["hei_rank"] = out["hei"].rank(ascending=False, method="min").astype(int)

    # Population is reported as a plain count. It is deliberately NOT multiplied into the index:
    # the prior prototype credited a cell's entire population as "protected" the moment one tree
    # landed in it, so one tree in a 6,572-person block group "protected" 6,572 people.
    out["exposed_residents"] = out["acs_pop"].astype(float)

    return out


def explain(row: pd.Series, weights: Optional[Dict[str, float]] = None) -> Dict[str, object]:
    """Per-block-group breakdown for the UI, with provenance on each line."""
    w = normalize_weights(weights)
    return {
        "geoid": row["GEOID"],
        "hei": round(float(row["hei"]), 4),
        "hei_rank": int(row["hei_rank"]),
        "components": [
            {"name": "Thermal load", "weight": round(w["thermal"], 3),
             "percentile": round(float(row["hei_thermal"]) * 100, 1),
             "contribution": round(w["thermal"] * float(row["hei_thermal"]), 4),
             "basis": "TES temp_diff, percentile-ranked across the Atlanta urban area",
             "provenance": "Derived from Measured",
             "caveat": ("temp_diff units are UNVERIFIED, and temp_diff correlates -0.806 with "
                        "existing canopy in Atlanta, so it is partly circular evidence for "
                        "planting trees")},
            {"name": "Population density", "weight": round(w["density"], 3),
             "percentile": round(float(row["hei_density"]) * 100, 1),
             "contribution": round(w["density"] * float(row["hei_density"]), 4),
             "basis": "acs_pop / land_area, percentile-ranked across the Atlanta urban area",
             "provenance": "Derived from Measured"},
            {"name": "Social sensitivity", "weight": round(w["sensitivity"], 3),
             "percentile": round(float(row["hei_sensitivity"]) * 100, 1),
             "contribution": round(w["sensitivity"] * float(row["hei_sensitivity"]), 4),
             "basis": ("mean of percentile ranks of 6 TES equity indices (people of colour, "
                       "poverty, unemployment, dependency, linguistic isolation, health)"),
             "provenance": "Derived from Measured"},
        ],
        "exposed_residents": int(row["exposed_residents"]),
        "weights_provenance": "Assumed - a policy choice, not a physical constant",
    }


if __name__ == "__main__":
    import data

    df, _ = data.load()
    scored = compute(df)
    w = default_weights()
    print("weights: %s\n" % w)
    print("HEI range: %.4f - %.4f  mean %.4f"
          % (scored["hei"].min(), scored["hei"].max(), scored["hei"].mean()))
    print()
    cols = ["GEOID", "hei", "hei_thermal", "hei_density", "hei_sensitivity",
            "acs_pop", "treecanopy", "tc_gap"]
    print("TOP 8 MOST EXPOSED")
    print(scored.nlargest(8, "hei")[cols].to_string(index=False))
    print()
    print("BOTTOM 5")
    print(scored.nsmallest(5, "hei")[cols].to_string(index=False))
