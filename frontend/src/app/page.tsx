"use client";

/**
 * HeatROI — the dashboard. Control rail (the instrument) → map (the argument) → analysis panel
 * (the evidence). It never renders a temperature reduction, never shows a figure without its
 * provenance stamp, and surfaces every backend warning verbatim.
 *
 * Live: every control recomputes the allocation on a short debounce — there is no button to
 * press. The sweeping top bar and the status pill say when a request is in flight, so a live
 * number is never mistaken for a stale one.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Analysis from "@/components/Analysis";
import Controls, { type ControlState } from "@/components/Controls";
import MapPanel, { type MapMode } from "@/components/MapPanel";
import { Figure, Stamp } from "@/components/Stamp";
import {
  type AoiResponse,
  type Baselines,
  type Frontier,
  getAoi,
  getBaselines,
  getFrontier,
  getProvenance,
  getScenario,
  m2,
  num,
  type Plan,
  type ProvenanceReport,
  usd,
  usdFull,
} from "@/lib/api";

const INITIAL: ControlState = {
  budget: 2_000_000,
  objective: "max_priority_area",
  currency: "radiant",
  interventions: ["tree", "shade"],
  equityFloor: 0,
  wThermal: 0.4,
  wDensity: 0.3,
  wSensitivity: 0.3,
};

const reducedMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/**
 * Count-up on a headline figure. Interruptible: a change mid-tween resumes from the value on
 * screen rather than snapping, so rapid slider drags read as continuous motion. Honours
 * prefers-reduced-motion by jumping straight to the target.
 */
function AnimatedNumber({
  value,
  format,
  duration = 650,
}: {
  value: number;
  format: (n: number) => string;
  duration?: number;
}) {
  const [display, setDisplay] = useState(value);
  const fromRef = useRef(value);
  const curRef = useRef(value);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (reducedMotion()) {
      curRef.current = value;
      fromRef.current = value;
      setDisplay(value);
      return;
    }
    const start = performance.now();
    const startVal = fromRef.current;
    const delta = value - startVal;
    if (Math.abs(delta) < 1e-9) {
      curRef.current = value;
      setDisplay(value);
      return;
    }
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
      const v = startVal + delta * eased;
      curRef.current = v;
      setDisplay(v);
      if (t < 1) rafRef.current = requestAnimationFrame(step);
      else fromRef.current = value;
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      fromRef.current = curRef.current; // resume from wherever the tween is
    };
  }, [value, duration]);

  return <>{format(display)}</>;
}

export default function Page() {
  const [s, setS] = useState<ControlState>(INITIAL);
  const [mode, setMode] = useState<MapMode>("need");

  const [aoi, setAoi] = useState<AoiResponse | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [baselines, setBaselines] = useState<Baselines | null>(null);
  const [frontier, setFrontier] = useState<Frontier | null>(null);
  const [prov, setProv] = useState<ProvenanceReport | null>(null);

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const set = (patch: Partial<ControlState>) => setS((p) => ({ ...p, ...patch }));

  const wSum = s.wThermal + s.wDensity + s.wSensitivity;
  const weights = {
    weight_thermal: s.wThermal,
    weight_density: s.wDensity,
    weight_sensitivity: s.wSensitivity,
  };

  useEffect(() => {
    getProvenance().then(setProv).catch(() => {});
  }, []);

  // The AOI refetches only when the weights change, since the weights are what re-score it.
  useEffect(() => {
    if (wSum === 0) return;
    let live = true;
    getAoi(weights)
      .then((r) => live && setAoi(r))
      .catch((e) => live && setErr(String(e.message ?? e)));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.wThermal, s.wDensity, s.wSensitivity]);

  // Race guard: only the most recent run may write state. Rapid control changes fire several
  // requests; without this a slower earlier one could land last and paint a stale plan.
  const runSeq = useRef(0);
  const didInit = useRef(false);

  const run = useCallback(async () => {
    const seq = ++runSeq.current;
    setBusy(true);
    setErr(null);
    const body = {
      budget: s.budget,
      interventions: s.interventions,
      currency: s.currency,
      objective: s.objective,
      equity_floor: s.equityFloor,
      ...weights,
    };
    try {
      const p = await getScenario({ ...body, verify: true });
      if (seq !== runSeq.current) return;
      setPlan(p);
      // Snap to the Plan view once, on first load — never on later auto-runs, or a user watching
      // the Need map while dragging weights would be yanked back to Plan on every change.
      if (!didInit.current) {
        setMode("plan");
        didInit.current = true;
      }
      const b = await getBaselines(body);
      if (seq !== runSeq.current) return;
      setBaselines(b);
      const fr = await getFrontier({
        budget: s.budget,
        interventions: s.interventions,
        currency: s.currency,
        objective: s.objective,
        steps: 6,
      });
      if (seq !== runSeq.current) return;
      setFrontier(fr);
    } catch (e) {
      if (seq === runSeq.current) setErr(String((e as Error).message ?? e));
    } finally {
      if (seq === runSeq.current) setBusy(false);
    }
  }, [s]);

  // Live: recompute on any scenario-affecting control, debounced so a slider drag fires one
  // request when it settles rather than one per pixel. This replaces the old Allocate button.
  useEffect(() => {
    if (wSum === 0) return;
    const t = setTimeout(run, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    s.budget,
    s.objective,
    s.currency,
    s.interventions,
    s.equityFloor,
    s.wThermal,
    s.wDensity,
    s.wSensitivity,
  ]);

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Controls
        s={s}
        set={set}
        onRun={run}
        busy={busy}
        locationBlind={s.objective === "max_effective_area"}
        mixPredetermined={(plan?.mix_predetermined?.length ?? 0) > 0}
      />

      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {/* In-flight indicator: a live number must never be mistaken for a stale one. */}
        <div className={`progressbar ${busy ? "on" : ""}`} aria-hidden />

        {err && (
          <div className="warn" style={{ margin: 0, borderLeft: 0, borderBottom: "1px solid var(--flag)" }}>
            <b>Backend unreachable or request rejected:</b> {err}
            <div style={{ marginTop: 4, fontFamily: "var(--mono)", fontSize: 11 }}>
              Start the backend and it recomputes automatically. Health check:{" "}
              <b>http://localhost:8000/api/health</b>
            </div>
          </div>
        )}

        <Kpis plan={plan} busy={busy} />

        <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
          <MapPanel
            aoi={aoi}
            allocations={plan?.allocations ?? []}
            mode={mode}
            onModeChange={setMode}
            funded={plan?.block_groups_funded ?? 0}
            total={plan?.block_groups_total ?? aoi?.features.length ?? 48}
          />
          <div
            style={{
              width: 620,
              flexShrink: 0,
              borderLeft: "1px solid var(--rule)",
              background: "var(--panel)",
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
            }}
          >
            <Analysis plan={plan} baselines={baselines} frontier={frontier} provenance={prov} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Kpis({ plan, busy }: { plan: Plan | null; busy: boolean }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
        gap: 12,
        padding: "14px 18px",
        borderBottom: "1px solid var(--rule)",
        background: "var(--panel-2)",
        opacity: busy ? 0.9 : 1,
        transition: "opacity 160ms ease",
      }}
    >
      <Figure
        label="Allocated"
        prov="Derived"
        tone="fund"
        value={plan ? <AnimatedNumber value={plan.spent} format={usd} /> : "—"}
        sub={
          plan
            ? `${(plan.budget_utilisation * 100).toFixed(1)}% of budget · ${usdFull(
                plan.unspent,
              )} unspent`
            : ""
        }
      />
      <Figure
        label="Block groups funded"
        prov="Derived"
        value={
          plan ? (
            <>
              <AnimatedNumber value={plan.block_groups_funded} format={(n) => String(Math.round(n))} />
              /{plan.block_groups_total}
            </>
          ) : (
            "—"
          )
        }
        sub={plan ? `${plan.line_items} line items` : ""}
      />
      <Figure
        label="People reached"
        prov="Derived"
        value={plan ? <AnimatedNumber value={plan.people_reached} format={(n) => num(n)} /> : "—"}
        sub={
          plan
            ? `${plan.people_reached_pct.toFixed(1)}% of ${num(plan.aoi_population)} residents`
            : ""
        }
      />
      <Figure
        label="Canopy added"
        prov="Assumed"
        value={plan ? <AnimatedNumber value={plan.canopy_added_m2} format={m2} /> : "—"}
        sub={
          plan
            ? `closes ${plan.canopy_gap_closed_pct_aoi.toFixed(2)}% of the AOI canopy gap`
            : ""
        }
      />

      {/* Prints the refusal in the same slot, at the same weight, so the absence is as visible
          as a number would have been. */}
      <div className="panel" style={{ padding: "12px 13px", minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            gap: 8,
            marginBottom: 8,
          }}
        >
          <span className="eyebrow">Temperature reduction</span>
          <Stamp p="Refused" />
        </div>
        <div className="figure" style={{ color: "var(--ink-3)" }}>
          n/a
        </div>
        <div className="quiet" style={{ marginTop: 7, fontSize: 10.5, lineHeight: 1.45 }}>
          {plan?.temperature_note ??
            "Not computed: no validated cooling coefficient exists in this workspace."}
        </div>
      </div>

      {plan && plan.warnings.length > 0 && (
        <div style={{ gridColumn: "1 / -1", display: "grid", gap: 6 }}>
          {plan.warnings.map((w, i) => (
            <div key={i} className="warn" style={{ fontFamily: "var(--mono)", fontSize: 11 }}>
              {w}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
