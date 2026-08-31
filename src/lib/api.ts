/**
 * API client and types. Every type mirrors a response shape asserted by `backend/test_api.py` and
 * re-checked against the live server by `backend/test_contract.py`. Where the backend refuses to
 * produce a number (`temperature_reduction`), the type says `null` explicitly rather than omitting
 * the field — the refusal is part of the contract and the UI renders it.
 */

export const API = process.env.NEXT_PUBLIC_API ?? "";

export type Currency = "radiant" | "ambient";
export type Objective =
  | "max_priority_area"
  | "max_effective_area"
  | "max_people_reached";
export type Intervention = "tree" | "cool_roof" | "shade";

export interface BlockGroupProps {
  GEOID: string;
  hei: number;
  hei_rank: number;
  hei_thermal: number;
  hei_density: number;
  hei_sensitivity: number;
  temp_diff: number;
  treecanopy: number;
  tc_gap: number;
  tc_goal: number;
  acs_pop: number;
  land_area: number;
  cap_tree: number;
  cap_cool_roof: number;
  cap_shade: number;
  canopy_deficit_m2: number;
  exposed_residents: number;
}

export interface AoiResponse {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    geometry: unknown;
    properties: BlockGroupProps;
  }[];
  meta: {
    summary: Record<string, unknown>;
    totals: Record<string, number | string | Record<string, string>>;
    weights: Record<string, number>;
    hei_range: [number, number];
    caveat: string;
  };
}

export interface Allocation {
  GEOID: string;
  intervention: Intervention;
  unit: string;
  units: number;
  cost: number;
  benefit: number;
  radiant_m2: number;
  ambient_m2: number;
  benefit_per_dollar: number;
  hei: number;
}

export interface Plan {
  budget: number;
  currency: Currency;
  objective: Objective;
  equity_floor: number;
  spent: number;
  budget_utilisation: number;
  unspent: number;
  objective_value: number;
  block_groups_funded: number;
  block_groups_total: number;
  line_items: number;
  by_intervention: Record<
    Intervention,
    { units: number; unit_label: string; cost: number; block_groups: number }
  >;
  radiant_m2: number;
  ambient_m2: number;
  canopy_added_m2: number;
  canopy_gap_closed_pct_aoi: number;
  aoi_canopy_deficit_m2: number;
  people_reached: number;
  people_reached_pct: number;
  aoi_population: number;
  equity_spend: number;
  equity_share: number;
  equity_binding: boolean;
  equity_share_unconstrained: number;
  reporting_frame: string;
  /** Always null. The backend refuses to invent a cooling coefficient. */
  temperature_reduction: null;
  temperature_note: string;
  optimality: {
    lp_upper_bound: number;
    achieved: number;
    gap_pct: number;
    gap_is_float_noise: boolean;
    basis: string;
    milp_check?: {
      available: boolean;
      status?: string;
      objective?: number;
      cost?: number;
      nonzero_lines?: number;
      note?: string;
    };
  };
  warnings: string[];
  mix_contested: string[];
  mix_predetermined: string[];
  allocations: Allocation[];
  provenance_note: string;
}

export interface BaselineRow {
  objective_value: number;
  spent: number;
  budget_utilisation: number;
  block_groups_funded: number;
  people_reached: number;
  canopy_added_m2: number;
  canopy_gap_closed_pct_aoi: number;
  equity_share: number;
  vs_optimized_pct: number;
}

export interface Baselines {
  budget: number;
  strategies: Record<string, BaselineRow>;
  note: string;
}

export interface FrontierPoint {
  equity_floor: number;
  equity_share_achieved: number;
  objective_value: number;
  people_reached: number;
  canopy_added_m2: number;
  block_groups_funded: number;
  spent: number;
  efficiency_retained_pct: number;
}

export interface Frontier {
  budget: number;
  priority_definition: string;
  binding_from_floor: number;
  first_costly_floor: number | null;
  note: string;
  points: FrontierPoint[];
}

export interface ProvenanceValue {
  key: string;
  value: number | string;
  unit: string;
  provenance: string;
  source: string;
  note: string;
  citation_pending?: boolean;
  editable?: boolean;
}

export interface ProvenanceReport {
  taxonomy: string[];
  values: ProvenanceValue[];
  citation_pending: string[];
  citation_pending_count: number;
  refused: { key: string; reason: string }[];
  environment_note: string;
  legend: Record<string, string>;
}

export interface ScenarioInput {
  budget: number;
  interventions: Intervention[] | null;
  currency: Currency;
  objective: Objective;
  equity_floor: number;
  weight_thermal: number;
  weight_density: number;
  weight_sensitivity: number;
  verify?: boolean;
}

/** Surfaces the backend's own 422 message rather than a generic failure. */
async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail))
        detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* keep the status-code fallback */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body: unknown) =>
  fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => unwrap<T>(r));

export const getAoi = (w: {
  weight_thermal: number;
  weight_density: number;
  weight_sensitivity: number;
}) =>
  fetch(
    `${API}/api/aoi?weight_thermal=${w.weight_thermal}` +
      `&weight_density=${w.weight_density}&weight_sensitivity=${w.weight_sensitivity}`,
  ).then((r) => unwrap<AoiResponse>(r));

export const getScenario = (i: ScenarioInput) => post<Plan>("/api/scenario", i);
export const getBaselines = (i: ScenarioInput) =>
  post<Baselines>("/api/baselines", i);
export const getFrontier = (i: {
  budget: number;
  interventions: Intervention[] | null;
  currency: Currency;
  objective: Objective;
  steps: number;
}) => post<Frontier>("/api/frontier", i);
export const getProvenance = () =>
  fetch(`${API}/api/provenance`).then((r) => unwrap<ProvenanceReport>(r));

/* ---------------------------------------------------------------------------
   formatters
   --------------------------------------------------------------------------- */

export const usd = (n: number) =>
  n >= 1e6
    ? `$${(n / 1e6).toFixed(n >= 1e7 ? 1 : 2)}M`
    : n >= 1e3
      ? `$${Math.round(n / 1e3)}k`
      : `$${Math.round(n)}`;

export const usdFull = (n: number) =>
  `$${Math.round(n).toLocaleString("en-US")}`;

export const num = (n: number, d = 0) =>
  n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

export const m2 = (n: number) =>
  n >= 1e4 ? `${num(n / 1e4, 2)} ha` : `${num(n)} m²`;

export const INTERVENTION_LABEL: Record<Intervention, string> = {
  tree: "Street trees",
  cool_roof: "Cool roofs",
  shade: "Shade structures",
};

export const OBJECTIVE_LABEL: Record<Objective, string> = {
  max_priority_area: "Priority-weighted area",
  max_effective_area: "Raw effective area",
  max_people_reached: "People reached",
};

/** Sequential ramp, shared by the map fill and the legend so they cannot drift. */
export const HEAT_RAMP = ["#2e6e7e", "#62907c", "#e0be4c", "#d1762a", "#bf3a1f"];
