"use client";

/**
 * The control rail. Every control maps to one parameter of the optimization in
 * `docs/architecture/05`. Controls that cannot do anything are disabled with the reason shown,
 * rather than left live to imply a choice that does not exist.
 */

import {
  type Currency,
  type Intervention,
  INTERVENTION_LABEL,
  type Objective,
  OBJECTIVE_LABEL,
  usdFull,
} from "@/lib/api";

const BUDGET_STOPS = [
  100_000, 250_000, 500_000, 1_000_000, 2_000_000, 3_500_000, 5_000_000, 7_500_000,
  10_000_000, 15_000_000, 20_000_000, 25_000_000, 30_000_000, 35_000_000, 41_000_000,
];

export interface ControlState {
  budget: number;
  objective: Objective;
  currency: Currency;
  interventions: Intervention[];
  equityFloor: number;
  wThermal: number;
  wDensity: number;
  wSensitivity: number;
}

export default function Controls({
  s,
  set,
  onRun,
  busy,
  locationBlind,
  mixPredetermined,
}: {
  s: ControlState;
  set: (patch: Partial<ControlState>) => void;
  onRun: () => void;
  busy: boolean;
  locationBlind: boolean;
  mixPredetermined: boolean;
}) {
  const stopIdx = BUDGET_STOPS.indexOf(s.budget);
  const wSum = s.wThermal + s.wDensity + s.wSensitivity;

  const toggleIv = (k: Intervention) => {
    const has = s.interventions.includes(k);
    // Never allow an empty selection: the backend rejects it with a 422 by design, and a
    // control that can put the app into a guaranteed-error state is a broken control.
    if (has && s.interventions.length === 1) return;
    set({
      interventions: has
        ? s.interventions.filter((x) => x !== k)
        : [...s.interventions, k],
    });
  };

  return (
    <div
      style={{
        width: "var(--rail)",
        flexShrink: 0,
        borderRight: "1px solid var(--rule)",
        background: "var(--panel)",
        overflowY: "auto",
        padding: "20px 18px 28px",
      }}
    >
      <div style={{ marginBottom: 22 }}>
        <div
          className="mono"
          style={{ fontSize: 21, fontWeight: 600, letterSpacing: "-0.02em" }}
        >
          HeatROI
        </div>
        {/* One accent, drawn from the heat ramp itself: the only saturated thing in the rail. */}
        <div
          style={{
            height: 3,
            borderRadius: 2,
            marginTop: 6,
            background:
              "linear-gradient(90deg, var(--heat-0), var(--heat-1), var(--heat-2), var(--heat-3), var(--heat-4))",
          }}
        />
        <div className="quiet" style={{ marginTop: 8 }}>
          Where should heat money go?
        </div>
      </div>

      <label className="field">
        <span className="eyebrow">Capital budget</span>
        <div
          className="mono"
          style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.02em" }}
        >
          {usdFull(s.budget)}
        </div>
        <input
          type="range"
          min={0}
          max={BUDGET_STOPS.length - 1}
          step={1}
          value={stopIdx < 0 ? 4 : stopIdx}
          onChange={(e) => set({ budget: BUDGET_STOPS[Number(e.target.value)] })}
          aria-label="Capital budget"
        />
        <div
          className="mono quiet"
          style={{ display: "flex", justifyContent: "space-between", fontSize: 9.5 }}
        >
          <span>$100k</span>
          <span>$41M · builds everything</span>
        </div>
      </label>

      <label className="field">
        <span className="eyebrow">Objective</span>
        <select
          value={s.objective}
          onChange={(e) => set({ objective: e.target.value as Objective })}
        >
          {(Object.keys(OBJECTIVE_LABEL) as Objective[]).map((o) => (
            <option key={o} value={o}>
              {OBJECTIVE_LABEL[o]}
            </option>
          ))}
        </select>
        {locationBlind && (
          <div className="warn" style={{ marginTop: 7 }}>
            <b>Location-blind.</b> Under this objective benefit per dollar is identical in every
            block group, so the map is an arbitrary tie-break. Kept for comparison only — use
            priority-weighted area to choose <i>where</i>.
          </div>
        )}
      </label>

      <div className="field">
        <span className="eyebrow">Benefit currency</span>
        <div className="toggle-row">
          {(["radiant", "ambient"] as Currency[]).map((c) => (
            <button
              key={c}
              aria-pressed={s.currency === c}
              onClick={() => {
                // People-reached is defined on street-level radiant coverage, so pairing it
                // with ambient is meaningless. Switch the objective with the currency instead
                // of letting the request 422.
                if (c === "ambient" && s.objective === "max_people_reached")
                  set({ currency: c, objective: "max_priority_area" });
                else set({ currency: c });
              }}
            >
              {c}
            </button>
          ))}
        </div>
        <div className="quiet" style={{ marginTop: 6 }}>
          {s.currency === "radiant"
            ? "Street-level shade a pedestrian feels. Trees and shade structures only."
            : "Neighbourhood air temperature. Cool roofs and trees only."}{" "}
          The two are never summed.
        </div>
      </div>

      <div className="field">
        <span className="eyebrow">Interventions</span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {(Object.keys(INTERVENTION_LABEL) as Intervention[]).map((k) => {
            const dead =
              (s.currency === "radiant" && k === "cool_roof") ||
              (s.currency === "ambient" && k === "shade");
            return (
              <button
                key={k}
                className="chip"
                aria-pressed={s.interventions.includes(k) && !dead}
                onClick={() => toggleIv(k)}
                disabled={dead}
                title={
                  dead
                    ? `No effect in the ${s.currency} currency, so it can never be selected here.`
                    : INTERVENTION_LABEL[k]
                }
                style={dead ? { opacity: 0.38, cursor: "not-allowed" } : undefined}
              >
                {INTERVENTION_LABEL[k]}
              </button>
            );
          })}
        </div>
        {mixPredetermined && (
          <div className="warn" style={{ marginTop: 8 }}>
            <b>These toggles change nothing at this budget.</b> One intervention outranks the
            others in every block group, so it is bought to full capacity first. That ordering
            comes from <i>assumed</i> unit costs, not from data.
          </div>
        )}
      </div>

      <label className="field">
        <span className="eyebrow">Equity floor</span>
        <div className="mono figure-sm">{(s.equityFloor * 100).toFixed(0)}%</div>
        <input
          type="range"
          min={0}
          max={100}
          step={10}
          value={s.equityFloor * 100}
          onChange={(e) => set({ equityFloor: Number(e.target.value) / 100 })}
          aria-label="Equity floor"
        />
        <div className="quiet">
          Minimum share of budget spent in the most socially sensitive half of block groups.
        </div>
      </label>

      <div className="field">
        <span className="eyebrow">Exposure index weights</span>
        {(
          [
            ["wThermal", "Thermal", s.wThermal],
            ["wDensity", "Density", s.wDensity],
            ["wSensitivity", "Sensitivity", s.wSensitivity],
          ] as const
        ).map(([key, label, val]) => (
          <div key={key} style={{ marginTop: 8 }}>
            <div
              className="mono"
              style={{ display: "flex", justifyContent: "space-between", fontSize: 11 }}
            >
              <span className="muted">{label}</span>
              <span>{(wSum > 0 ? val / wSum : 0).toFixed(2)}</span>
            </div>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={val * 100}
              onChange={(e) => set({ [key]: Number(e.target.value) / 100 } as Partial<ControlState>)}
              aria-label={`${label} weight`}
            />
          </div>
        ))}
        {wSum === 0 ? (
          <div className="warn" style={{ marginTop: 7 }}>
            All three weights are zero. Raise at least one to score the area.
          </div>
        ) : (
          <div className="quiet" style={{ marginTop: 4 }}>
            Normalised to 1 before scoring. Shown values are the normalised weights.
          </div>
        )}
      </div>

      <button
        className={`livepill ${busy ? "busy" : "idle"}`}
        onClick={onRun}
        disabled={wSum === 0}
        title="Updates automatically as you move a control. Click to force a recompute."
      >
        <span className="dot" />
        {wSum === 0 ? "Raise a weight to score" : busy ? "Computing…" : "Live · auto-updates"}
      </button>

      <div className="quiet" style={{ marginTop: 14, lineHeight: 1.55 }}>
        Atlanta downtown AOI · 48 census block groups · 59,795 residents. Costs are assumed
        placeholders; no temperature reduction is claimed anywhere in this tool.
      </div>
    </div>
  );
}
