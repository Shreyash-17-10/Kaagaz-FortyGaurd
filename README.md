# HeatROI

**Where should a city plant trees first?** HeatROI ranks census block groups by where tree planting delivers the most cooling to the most heat-vulnerable people — combining modeled urban heat, tree-planting opportunity, and social-equity data into a single prioritized list.

Built for Georgia (5,869 block groups) using the American Forests **Tree Equity Score**, the **WRI Cities Data API**, and the **FortyGuard** heat API.


---


## What it does

The whole pipeline lives in one file, [`heatroi.py`](heatroi.py), and runs in four stages:

| Stage | Source | Output |
|-------|--------|--------|
| 1. Heat snapshot | FortyGuard API | Per-tile modeled temperature over an Area of Interest |
| 2. Tree opportunity | WRI Cities Data API | "All-plantable" tree-opportunity layer for the city |
| 3. Unified grid | (join) | Heat × opportunity, per grid tile |
| 4. **ROI scoring** | American Forests Tree Equity Score | **Ranked planting priorities** (the deliverable) |

Stage 4 runs on its own with no API key or network — it's the part most people want.


---


## The scoring model

Each block group gets a **HeatROI score (0–100)**. Higher = plant here first.

```
need     = 0.5 · heat  +  0.5 · vulnerability      # who is suffering
heat_roi = need  ×  opportunity                     # gated by room to plant
score    = percentile(heat_roi) × 100               # 0–100, ranked
```

| Component | Meaning | Source |
|-----------|---------|--------|
| **heat** | Heat burden, normalized | FortyGuard `_tot1500` if present, else the equity file's `temp_norm` |
| **vulnerability** | Mean of 6 normalized indices: people of color, poverty, unemployment, dependency ratio, linguistic isolation, health | Tree Equity Score |
| **opportunity** | Canopy gap to goal (`tc_gap`) — no gap means no ROI | Tree Equity Score |

Because opportunity is a **multiplier**, block groups that already meet their canopy goal score low no matter how hot they are — there's nothing to plant. Results are bucketed into tiers: **Critical** (≥90), **High** (≥70), **Moderate** (≥40), **Low** (<40).

All weights live at the top of `heatroi.py` (`NEED_HEAT_WEIGHT`, `NEED_VULN_WEIGHT`, `OPPORTUNITY_MODE`, `REDLINE_BOOST`).


---


## Project structure

```
.
├─ heatroi.py            # the entire pipeline + CLI
├─ requirements.txt
├─ .env.example          # template for FORTYGUARD_API_KEY
├─ .gitignore
├─ indicators.json       # WRI indicator catalogue (reference)
├─ openapi.json          # WRI API spec (reference)
└─ data/
   ├─ ga_tree_equity.xlsx          # American Forests Tree Equity Score (Georgia)
   ├─ fortyguard_snapshot.geojson  # FortyGuard heat snapshot
   └─ final_analysis_grid.geojson  # unified heat × opportunity grid
```

---

## Setup (macOS)

**Quick — ROI scoring only** (no key, no geo libraries):

```bash
python3 -m pip install pandas numpy openpyxl
python3 heatroi.py score
```

**Full pipeline** (adds the live heat + geospatial stages):

```bash
brew install gdal                       # system lib for geopandas/rasterstats
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env                     # then add your FortyGuard key
export FORTYGUARD_API_KEY="your_key_here"
```

---

## Usage

```bash
python3 heatroi.py score          # ROI scoring only  (default deliverable)
python3 heatroi.py city Atlanta   # look up a WRI city id
python3 heatroi.py fetch          # FortyGuard heat snapshot   (needs API key)
python3 heatroi.py opportunity    # download WRI opportunity layer
python3 heatroi.py unify          # build heat × opportunity grid (needs geopandas)
python3 heatroi.py all            # run everything, skipping unavailable stages
```

Heavy dependencies are imported lazily, so `score` works with just pandas/numpy/openpyxl. `all` degrades gracefully: any stage missing a key, dependency, or network is skipped with a message, and scoring still runs.


---


## Output

Two files in `data/`:

- **`heat_roi_scores.csv`** — every block group, ranked, with the full reasoning columns (`heat_n`, `opp_n`, `vuln`, `need`, `heat_roi_raw`, `heat_roi_score`, `tier`, plus population-scaled `total_benefit`).
- **`heat_roi_top50.csv`** — the top 50 priorities.

Example console summary:

```
Block groups scored : 5869
Score range         : 26.9 – 100.0

Tier breakdown:
  Critical    590
  High       1174
  Moderate    953
  Low        3152

Top 10 priorities:
   #  place        county            tes   ΔT°C   gap  vuln  score  tier
   1  Columbus     Muscogee County    27   14.0  0.39  0.65  100.0  Critical
   2  Atlanta      Fulton County      51   18.3  0.43  0.45  100.0  Critical
   ...
```


---


## Notes

- **Heat fallback:** if FortyGuard's fine-grained per-block-group temperature (`_tot1500`) hasn't been populated, scoring falls back to the heat-disparity metric already in the equity data. Run the `fetch` → `unify` stages to sharpen the heat signal.
- **API key:** never commit it. It's read from `FORTYGUARD_API_KEY`; `.env` is git-ignored.
- **Configuration:** the Area of Interest, city, dates, and all scoring weights are constants at the top of `heatroi.py`.

## Data sources & attribution

- **Tree Equity Score** — American Forests (<https://treeequityscore.org>)
- **Cities Indicators / opportunity layers** — WRI Cities Data (<https://citiesindicators.wri.org>)
- **Urban heat modeling** — FortyGuard (<https://fortyguard.com>)
