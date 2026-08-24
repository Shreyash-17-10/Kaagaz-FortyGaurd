"""
HeatROI — exploratory data analysis.

Reads the scored output (data/heat_roi_scores.csv) and the source equity file,
then prints a structured set of findings: distributions, what drives the score,
the redlining (HOLC) signal, and where the priorities concentrate geographically.

Pure pandas/numpy — no network, no geo libraries. Run:  python3 analyze.py
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd

from heatroi import load_equity, ROI_SCORES_CSV  # reuse the project's loader/paths

pd.set_option("display.width", 120)
pd.set_option("display.max_columns", 40)

RULE = "=" * 72


def h(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def pct(n: int, total: int) -> str:
    return f"{n:,} ({100 * n / total:4.1f}%)"


def load() -> pd.DataFrame:
    scores = pd.read_csv(ROI_SCORES_CSV, dtype={"GEOID": str})
    eq = load_equity()[
        ["GEOID", "pctpoc", "pctpov", "unemplrate", "dep_ratio",
         "child_perc", "seniorperc", "land_area", "tc_goal"]
    ].copy()
    eq["GEOID"] = eq["GEOID"].astype(str)
    df = scores.merge(eq, on="GEOID", how="left")
    return df


def overview(df: pd.DataFrame) -> None:
    h("1. DATASET")
    n = len(df)
    print(f"Block groups : {n:,}")
    print(f"Columns      : {df.shape[1]}")

    nulls = df.isna().sum()
    nulls = nulls[nulls > 0].sort_values(ascending=False)
    if len(nulls):
        print("\nMissing values (columns with any):")
        for col, cnt in nulls.items():
            print(f"  {col:<16} {pct(int(cnt), n)}")

    const = [c for c in df.columns if df[c].nunique(dropna=True) <= 1]
    if const:
        print("\nConstant / single-value columns:", ", ".join(const))


def score_distribution(df: pd.DataFrame) -> None:
    h("2. SCORE & TIER DISTRIBUTION")
    s = df["heat_roi_score"]
    print(f"Score range  : {s.min():.1f} – {s.max():.1f}")
    print(f"Median       : {s.median():.1f}   Mean: {s.mean():.1f}")
    qs = s.quantile([0.10, 0.25, 0.50, 0.75, 0.90]).round(1)
    print("Deciles      :", ", ".join(f"p{int(q*100)}={v}" for q, v in qs.items()))

    tier_order = ["Critical", "High", "Moderate", "Low"]
    g = (df.groupby("tier")
           .agg(n=("GEOID", "size"),
                score=("heat_roi_score", "mean"),
                need=("need", "mean"),
                heat=("heat_n", "mean"),
                vuln=("vuln", "mean"),
                opp=("opp_n", "mean"),
                gap=("tc_gap", "mean"),
                pop=("acs_pop", "mean"))
           .reindex(tier_order))
    print("\nBy tier (means):")
    print("  tier        n      %   score  need  heat  vuln   opp   gap    pop")
    for tier, r in g.iterrows():
        print(f"  {tier:<9} {int(r.n):>5} {100*r.n/len(df):>5.1f}  "
              f"{r.score:>5.1f} {r.need:>5.2f} {r.heat:>5.2f} {r.vuln:>5.2f} "
              f"{r.opp:>5.2f} {r.gap:>5.3f} {r['pop']:>6.0f}")


def gating(df: pd.DataFrame) -> None:
    h("3. THE OPPORTUNITY GATE (why so many score low)")
    n = len(df)
    no_gap = df["tc_gap"] <= 0
    print(f"Block groups with no plantable canopy gap : {pct(int(no_gap.sum()), n)}")
    print("  -> opportunity is a multiplier, so these are forced toward the floor.")
    print(f"  their tiers: {df.loc[no_gap, 'tier'].value_counts().to_dict()}")
    print(f"  their score: {df.loc[no_gap, 'heat_roi_score'].min():.1f} – "
          f"{df.loc[no_gap, 'heat_roi_score'].max():.1f}")

    sat = np.isclose(df["heat_n"], 1.0)
    print(f"\nHeat component saturated at 1.0           : {pct(int(sat.sum()), n)}")
    print("  -> heat_n falls back to the equity file's coarse temp_norm")
    print("     (FortyGuard per-block-group temps are empty), so heat is a")
    print("     weak discriminator right now; opportunity + vulnerability dominate.")


def drivers(df: pd.DataFrame) -> None:
    h("4. WHAT DRIVES THE SCORE (Spearman rank correlation)")
    cols = ["heat_roi_score", "need", "opp_n", "tc_gap", "vuln",
            "heat_n", "tes", "treecanopy", "temp_diff", "acs_pop"]
    corr = df[cols].corr(method="spearman")
    sc = corr["heat_roi_score"].drop("heat_roi_score").sort_values(key=abs, ascending=False)
    print("Correlation of each variable with heat_roi_score:")
    for col, v in sc.items():
        bar = "#" * int(round(abs(v) * 30))
        print(f"  {col:<12} {v:+.2f}  {bar}")
    print("\nNote: tes is negative (low Tree Equity Score = high need), as designed.")


def redlining(df: pd.DataFrame) -> None:
    h("5. REDLINING SIGNAL (1930s HOLC grade vs today)")
    graded = df.dropna(subset=["holc_grade"])
    n = len(df)
    print(f"Block groups with a HOLC grade : {pct(len(graded), n)} "
          f"(the rest were never graded)")
    order = ["A", "B", "C", "D"]
    g = (graded[graded["holc_grade"].isin(order)]
         .groupby("holc_grade")
         .agg(n=("GEOID", "size"),
              canopy=("treecanopy", "mean"),
              gap=("tc_gap", "mean"),
              temp_diff=("temp_diff", "mean"),
              vuln=("vuln", "mean"),
              need=("need", "mean"),
              tes=("tes", "mean"),
              score=("heat_roi_score", "mean"))
         .reindex(order))
    print("\n  grade    n   canopy   gap   ΔT°C  vuln  need   tes  score   (A=best … D=redlined)")
    for grade, r in g.iterrows():
        if pd.isna(r.n):
            continue
        print(f"    {grade:<4} {int(r.n):>4}  {r.canopy:>5.1%}  {r.gap:>4.2f}  "
              f"{r.temp_diff:>5.1f} {r.vuln:>5.2f} {r.need:>5.2f} {r.tes:>5.0f}  {r.score:>5.1f}")
    if not g["canopy"].isna().all():
        a, d = g.loc["A"], g.loc["D"]
        if not (pd.isna(a.canopy) or pd.isna(d.canopy)):
            print(f"\n  D vs A: canopy {a.canopy:.1%} -> {d.canopy:.1%}, "
                  f"ΔT {a.temp_diff:+.1f} -> {d.temp_diff:+.1f}°C, "
                  f"score {a.score:.0f} -> {d.score:.0f}")


def geography(df: pd.DataFrame) -> None:
    h("6. WHERE THE PRIORITIES ARE (by county)")
    crit = df[df["tier"].isin(["Critical", "High"])]
    top = (crit.groupby("county")
               .agg(critical_high=("GEOID", "size"),
                    mean_score=("heat_roi_score", "mean"),
                    people=("acs_pop", "sum"))
               .sort_values("critical_high", ascending=False)
               .head(12))
    print("Counties with the most Critical+High block groups:")
    print("  county                    C+H   mean_score    people")
    for county, r in top.iterrows():
        name = (county or "—")[:24]
        print(f"  {name:<24} {int(r.critical_high):>4}   {r.mean_score:>6.1f}   {int(r.people):>8,}")

    h("6b. BIGGEST TOTAL BENEFIT (score x population — a budgeting view)")
    tb = df.sort_values("total_benefit", ascending=False).head(10)
    print("  rank  place                 county                score   pop   benefit")
    for _, r in tb.iterrows():
        place = (r["place"] if isinstance(r["place"], str) and r["place"] != "nan"
                 else "(unincorporated)")
        print(f"  {int(r.total_benefit_rank):>4}  {place[:20]:<20}  {str(r['county'])[:20]:<20}  "
              f"{r.heat_roi_score:>5.1f} {int(r.acs_pop):>5,} {r.total_benefit:>8.0f}")


def equity(df: pd.DataFrame) -> None:
    h("7. EQUITY & DEMOGRAPHICS BY TIER (from the source file)")
    tier_order = ["Critical", "High", "Moderate", "Low"]
    g = (df.groupby("tier")
           .agg(pctpoc=("pctpoc", "mean"),
                pctpov=("pctpov", "mean"),
                unempl=("unemplrate", "mean"),
                child=("child_perc", "mean"),
                senior=("seniorperc", "mean"),
                canopy=("treecanopy", "mean"))
           .reindex(tier_order))
    print("  tier       %POC   %poverty  %unempl  %child  %senior  canopy")
    for tier, r in g.iterrows():
        def f(x):  # some source cols are 0-1, some 0-100; show as-is with %
            return f"{x:>6.1%}" if x <= 1 else f"{x:>6.1f}"
        print(f"  {tier:<9} {f(r.pctpoc)} {f(r.pctpov)} {f(r.unempl)} "
              f"{f(r.child)} {f(r.senior)} {r.canopy:>6.1%}")


def main() -> None:
    df = load()
    print(f"Loaded {len(df):,} block groups from {Path(ROI_SCORES_CSV).name}")
    overview(df)
    score_distribution(df)
    gating(df)
    drivers(df)
    redlining(df)
    geography(df)
    equity(df)
    print(f"\n{RULE}\nDone.\n{RULE}")


if __name__ == "__main__":
    main()
