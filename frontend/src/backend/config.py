"""
HeatROI — single source of truth for every non-measured number.

RULE (from the brief): never present an assumption as a real-world measurement.

Every value here that is not Measured carries a Provenance tag and a `source` field. A `source`
of "" means NO CITATION EXISTS YET -- the value is a placeholder that must be replaced or
confirmed before any external claim is made. The API exposes this registry verbatim at
/api/provenance so the UI can badge every number it renders.

Nothing in this file was verified against external documentation: web access was unavailable in
the build environment (egress blocked, allowlist = agentrouter.org only). That is precisely why
each entry is tagged rather than silently inlined.
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


# ----------------------------------------------------------------------------------------
# Provenance taxonomy
# ----------------------------------------------------------------------------------------

class Provenance:
    MEASURED = "Measured"    # observed value from a cited dataset
    DERIVED = "Derived"      # arithmetic on Measured values only
    ASSUMED = "Assumed"      # a number we chose; user-editable; must be badged in the UI
    EXTERNAL = "External"    # requires a source we do not have
    UNKNOWN = "Unknown"      # a known unknown, stated openly

    ALL = [MEASURED, DERIVED, ASSUMED, EXTERNAL, UNKNOWN]


@dataclass
class Value:
    """A single auditable number."""
    key: str
    value: Any
    unit: str
    provenance: str
    source: str = ""          # "" => citation pending. Never fabricate one.
    note: str = ""
    editable: bool = False    # exposed as a user control in the UI

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["citation_pending"] = (self.provenance in (Provenance.ASSUMED, Provenance.EXTERNAL)
                                 and not self.source)
        return d


REGISTRY: List[Value] = []


def register(v: Value) -> Value:
    REGISTRY.append(v)
    return v


# ----------------------------------------------------------------------------------------
# Interventions
# ----------------------------------------------------------------------------------------

INTERVENTIONS = ["tree", "cool_roof", "shade"]

UNITS = {
    "tree": "tree",
    "cool_roof": "m2 of roof",
    "shade": "structure",
}

# --- Unit costs -------------------------------------------------------------------------
# These three numbers are inherited from the previous prototype's optimizer.py, where they were
# presented as "benchmark" costs with no citation. They are carried forward UNCHANGED but
# correctly labelled. They are the single largest credibility risk in the project.
COST = {
    "tree": register(Value(
        "cost.tree", 500.0, "USD per tree", Provenance.ASSUMED, "",
        "Installed and established cost. Inherited from prior prototype, uncited. "
        "Replace with a municipal bid tab or urban-forestry program figure.", True)),
    "cool_roof": register(Value(
        "cost.cool_roof", 20.0, "USD per m2", Provenance.ASSUMED, "",
        "High-albedo coating retrofit. Inherited from prior prototype, uncited.", True)),
    "shade": register(Value(
        "cost.shade", 5000.0, "USD per structure", Provenance.ASSUMED, "",
        "Engineered pedestrian/transit shade structure. Inherited, uncited.", True)),
}

# --- Benefit, expressed as EFFECTIVE AREA in two separate currencies ---------------------
# Two currencies are kept strictly separate and are NEVER summed:
#
#   radiant  -- street-level shortwave interception. What a pedestrian feels. Trees and shade
#               structures dominate; a cool roof does essentially nothing at head height.
#   ambient  -- neighbourhood-scale air-temperature effect via surface energy balance. Cool
#               roofs (albedo) and trees (evapotranspiration) contribute; shade ~nothing.
#
# Both are in m^2 of "effective treated area" so that values are physically interpretable and
# additive WITHIN a currency. The effectiveness multipliers are ASSUMED.
CROWN_AREA_M2 = register(Value(
    "crown_area_m2", 40.0, "m2 per mature tree", Provenance.ASSUMED, "",
    "Mature street-tree crown projection. Drives both tree capacity and tree benefit, so it is "
    "the most load-bearing assumption in the model.", True))

SHADE_FOOTPRINT_M2 = register(Value(
    "shade_footprint_m2", 30.0, "m2 per structure", Provenance.ASSUMED, "",
    "Shaded ground area of one engineered shade structure.", True))

TREE_ET_EFFECTIVENESS = register(Value(
    "tree_et_effectiveness", 0.5, "dimensionless", Provenance.ASSUMED, "",
    "Ambient-cooling effectiveness of tree canopy per m2, relative to a cool roof per m2. "
    "Represents evapotranspiration plus shading of the surface.", True))

COOL_ROOF_ALBEDO_EFFECTIVENESS = register(Value(
    "cool_roof_albedo_effectiveness", 1.0, "dimensionless", Provenance.ASSUMED, "",
    "Reference value (1.0 by definition) for ambient effect per m2 of coated roof. All other "
    "ambient coefficients are expressed relative to this.", True))


def radiant_m2_per_unit() -> Dict[str, float]:
    """Effective street-level shaded area gained per unit of each intervention."""
    return {
        "tree": CROWN_AREA_M2.value,
        "shade": SHADE_FOOTPRINT_M2.value,
        "cool_roof": 0.0,          # a roof does not shade the sidewalk
    }


def ambient_m2_per_unit() -> Dict[str, float]:
    """Effective albedo/ET-modified area gained per unit of each intervention."""
    return {
        "tree": CROWN_AREA_M2.value * TREE_ET_EFFECTIVENESS.value,
        "cool_roof": 1.0 * COOL_ROOF_ALBEDO_EFFECTIVENESS.value,
        "shade": 0.0,              # a shade sail does not change the surface energy balance
    }


# --- Capacity, where no data exists -----------------------------------------------------
# Trees have a real, Derived capacity (canopy deficit). Cool roofs and shade structures have
# NOTHING in the workspace to bound them. Rather than silently defaulting -- which is what the
# prior prototype did with max_roof_m2 = 1500 and max_shade = 3 -- these are explicit,
# badged, user-editable rates, and the UI must show they are assumed.
ROOF_M2_PER_1000_POP = register(Value(
    "roof_m2_per_1000_pop", 4000.0, "m2 per 1000 residents", Provenance.ASSUMED, "",
    "Placeholder for retrofittable flat-roof area. REPLACE by intersecting building footprints "
    "with block groups (Microsoft Building Footprints or OSM).", True))

SHADE_SITES_PER_1000_POP = register(Value(
    "shade_sites_per_1000_pop", 1.5, "sites per 1000 residents", Provenance.ASSUMED, "",
    "Placeholder for shade-worthy nodes. REPLACE by counting MARTA GTFS stops.txt per block "
    "group -- a small public download that turns this into a Measured value.", True))

# --- Exposure index weights -------------------------------------------------------------
DEFAULT_WEIGHTS = {
    "thermal": register(Value(
        "weight.thermal", 0.40, "dimensionless", Provenance.ASSUMED, "",
        "Weight on TES temp_diff percentile. A policy choice, not a physical constant.", True)),
    "density": register(Value(
        "weight.density", 0.30, "dimensionless", Provenance.ASSUMED, "",
        "Weight on population density percentile.", True)),
    "sensitivity": register(Value(
        "weight.sensitivity", 0.30, "dimensionless", Provenance.ASSUMED, "",
        "Weight on the mean of the six TES equity index percentiles.", True)),
}

EQUITY_COLS = ["pctpocnorm", "pctpovnorm", "unemplnorm", "depratnorm", "lingnorm", "health_nor"]

# --- Units and identities established by measurement, not documentation -----------------
LAND_AREA_TO_M2 = register(Value(
    "land_area_to_m2", 1.0e6, "m2 per unit of land_area", Provenance.DERIVED,
    "docs/architecture/02_geospatial.md gate G6",
    "land_area is in km^2. Established from geometry alone: interior block groups in a fully "
    "tiled AOI show 0.938 coverage under the km^2 hypothesis; mi^2 would imply 0.362 and m^2 "
    "would imply 9.4e5, both impossible. Omitting this factor makes Atlanta's entire canopy "
    "deficit compute as 155 m^2."))

TEMP_DIFF_UNIT = register(Value(
    "temp_diff_unit", None, "unknown", Provenance.UNKNOWN, "",
    "TES temp_diff units are NOT verified. Atlanta spread is 31.87, which is more plausible as "
    "degrees F than C. The prior prototype's README labelled it 'deltaT C' without support. "
    "Never render this field with a temperature unit until the TES data dictionary confirms it."))


# ----------------------------------------------------------------------------------------
# Things we deliberately do NOT compute
# ----------------------------------------------------------------------------------------

REFUSED: List[Dict[str, str]] = [
    {"key": "est_temp_reduction_c",
     "reason": "No cited cooling coefficient exists in this workspace. The prior prototype "
               "computed min(3.5, benefit/n_cells * 0.08) and rendered it as 'Est. -X C'. Both "
               "the 0.08 and the 3.5 cap were invented, and `benefit` summed trees + m2 + "
               "structures. HeatROI reports no temperature reduction at all until a coefficient "
               "is cited."},
    {"key": "tile_level_recommendation",
     "reason": "1,175 tiles carry only 48 independent attribute values. Tile-level output would "
               "overstate spatial precision by ~24x."},
    {"key": "fortyguard_as_ranking_variable",
     "reason": "Measured range across the AOI is 0.489 C with an interquartile range of 0.038 C. "
               "Normalising that to 0-1 amplifies noise into a priority signal. Used as absolute "
               "context and map backdrop only."},
    {"key": "cross_urban_area_ranking",
     "reason": "temp_norm and 5 of the 6 equity indices are min-max rescaled WITHIN each urban "
               "area (31/31 urban areas hit exactly 0.0 and 1.0), so values are not comparable "
               "across urban areas. All scoring happens within one urban area."},
    {"key": "wri_opportunity_layers",
     "reason": "Atlanta appears in 0 of 355 WRI indicator records; every needed layer has "
               "city_ids: []; licence is 'TBD'."},
]


# ----------------------------------------------------------------------------------------
# Introspection for /api/provenance
# ----------------------------------------------------------------------------------------

def provenance_report() -> Dict[str, Any]:
    entries = [v.as_dict() for v in REGISTRY]
    pending = [e["key"] for e in entries if e["citation_pending"]]
    return {
        "taxonomy": Provenance.ALL,
        "values": entries,
        "citation_pending": pending,
        "citation_pending_count": len(pending),
        "refused": REFUSED,
        "environment_note": (
            "No value in this registry was verified against external documentation: the build "
            "environment had no network egress. Values tagged Assumed with an empty source are "
            "placeholders, not measurements."
        ),
    }


if __name__ == "__main__":
    import json
    r = provenance_report()
    print(json.dumps(r, indent=2, default=str))
    print("\n%d registered values, %d awaiting citation:" % (len(r["values"]), r["citation_pending_count"]))
    for k in r["citation_pending"]:
        print("   - %s" % k)
