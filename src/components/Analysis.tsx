"use client";

import { Fragment, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  type Baselines,
  type Frontier,
  INTERVENTION_LABEL,
  type Intervention,
  m2,
  num,
  type Plan,
  type ProvenanceReport,
  usd,
  usdFull,
} from "@/lib/api";
import { Head, Stamp } from "./Stamp";

type Tab = "ledger" | "baselines" | "frontier" | "provenance";

const STRATEGY_LABEL: Record<string, string> = {
  greedy: "Optimized",
  even_spread: "Even spread",
  hottest_first: "Hottest first",
  random: "Random",
};

const STRATEGY_NOTE: Record<string, string> = {
  greedy: "Buy in descending benefit-per-dollar order",
  even_spread: "Equal dollars to all 48 block groups",
  hottest_first: "Hottest block groups first, ignoring cost",
  random: "Seeded random order",
};

export default function Analysis({
  plan,
  baselines,
  frontier,
  provenance,
}: {
  plan: Plan | null;
  baselines: Baselines | null;
  frontier: Frontier | null;
  provenance: ProvenanceReport | null;
}) {
  const [tab, setTab] = useState<Tab>("ledger");

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: 0, flex: 1 }}>
      <div className="tabs" role="tablist">
        {(
          [
            ["ledger", "Ledger"],
            ["baselines", "Baselines"],
            ["frontier", "Equity frontier"],
            ["provenance", "Provenance"],
          ] as [Tab, string][]
        ).map(([t, label]) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
          >
            {label}
            {t === "provenance" && provenance
              ? ` (${provenance.citation_pending.length})`
              : ""}
          </button>
        ))}
      </div>

      <div style={{ overflowY: "auto", padding: "18px", flex: 1, minHeight: 0 }}>
        {tab === "ledger" && <Ledger plan={plan} />}
        {tab === "baselines" && <BaselinePanel b={baselines} />}
        {tab === "frontier" && <FrontierPanel f={frontier} />}
        {tab === "provenance" && <ProvenancePanel r={provenance} />}
      </div>
    </div>
  );
}

function Ledger({ plan }: { plan: Plan | null }) {
  if (!plan) return <Empty>Set a budget and allocate to see the schedule.</Empty>;

  const rows = [...plan.allocations].sort((a, b) => b.cost - a.cost);

  return (
    <>
      <Head
        note={`${plan.line_items} line items · ${plan.block_groups_funded} of ${plan.block_groups_total} block groups funded`}
      >
        Appropriation schedule
      </Head>

      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
        {(Object.keys(INTERVENTION_LABEL) as Intervention[]).map((k) => {
          const v = plan.by_intervention[k];
          return (
            <div key={k} className="panel" style={{ padding: "9px 12px", minWidth: 148 }}>
              <div className="eyebrow" style={{ marginBottom: 5 }}>
                {INTERVENTION_LABEL[k]}
              </div>
              <div className="figure-sm" style={{ color: v.units > 0 ? "var(--ink)" : "var(--ink-3)" }}>
                {num(v.units)}{" "}
                <span style={{ fontSize: 10, fontWeight: 400, color: "var(--ink-3)" }}>
                  {v.unit_label}
                </span>
              </div>
              <div className="quiet mono" style={{ marginTop: 4, fontSize: 10 }}>
                {usdFull(v.cost)} · {v.block_groups} BGs
              </div>
            </div>
          );
        })}
      </div>

      <div className="panel" style={{ maxHeight: 340, overflowY: "auto" }}>
        <table className="ledger">
          <thead>
            <tr>
              <th>Block group</th>
              <th>Intervention</th>
              <th className="num">Units</th>
              <th className="num">Cost</th>
              <th className="num">HEI</th>
              <th className="num">Benefit / $</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a, i) => (
              <tr key={`${a.GEOID}-${a.intervention}-${i}`}>
                <td>{a.GEOID}</td>
                <td style={{ color: "var(--ink-2)" }}>{INTERVENTION_LABEL[a.intervention]}</td>
                <td className="num">{num(a.units)}</td>
                <td className="num" style={{ color: "var(--fund)" }}>{usdFull(a.cost)}</td>
                <td className="num">{a.hei.toFixed(3)}</td>
                <td className="num">{a.benefit_per_dollar.toFixed(4)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} style={{ color: "var(--ink-3)", padding: 16 }}>
                  This budget cannot afford a single unit of any selected intervention.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="note mono" style={{ marginTop: 14, fontSize: 11 }}>
        Optimality: achieved {num(plan.optimality.achieved, 2)} against an LP upper bound of{" "}
        {num(plan.optimality.lp_upper_bound, 2)} — gap{" "}
        <b>{plan.optimality.gap_pct.toFixed(4)}%</b>.
        {plan.optimality.milp_check?.available &&
          plan.optimality.milp_check.objective != null && (
            <>
              {" "}
              Independently re-solved with scipy/HiGHS branch-and-bound:{" "}
              {num(plan.optimality.milp_check.objective, 2)} (
              {plan.optimality.milp_check.status}).
            </>
          )}
      </div>
    </>
  );
}

function BaselinePanel({ b }: { b: Baselines | null }) {
  if (!b) return <Empty>Allocate a budget to compare strategies.</Empty>;

  const keys = ["greedy", "even_spread", "hottest_first", "random"].filter((k) => b.strategies[k]);
  const chart = keys.map((k) => ({
    name: STRATEGY_LABEL[k],
    value: b.strategies[k].objective_value,
    key: k,
  }));
  const g = b.strategies.greedy;

  return (
    <>
      <Head
        note={
          <>
            Hottest-first — what a heat map alone suggests — is{" "}
            <b>{Math.abs(b.strategies.hottest_first?.vs_optimized_pct ?? 0).toFixed(1)}% worse</b>.
          </>
        }
      >
        Optimized vs naive strategies · {usd(b.budget)}
      </Head>

      <div style={{ height: 168, marginBottom: 18 }} className="panel">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chart} margin={{ top: 16, right: 18, left: 8, bottom: 8 }}>
            <CartesianGrid stroke="#d7d9d1" vertical={false} />
            <XAxis
              dataKey="name"
              tick={{ fontSize: 10, fill: "#4a5570", fontFamily: "var(--mono)" }}
              stroke="#c3c6bd"
            />
            <YAxis
              tick={{ fontSize: 9.5, fill: "#7d869b", fontFamily: "var(--mono)" }}
              stroke="#c3c6bd"
              tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
            />
            <Tooltip
              contentStyle={{
                background: "#f7f7f4",
                border: "1px solid #16233d",
                borderRadius: 2,
                fontFamily: "var(--mono)",
                fontSize: 11,
              }}
              formatter={(v) => [num(Number(v), 0), "objective"]}
            />
            <Bar
              dataKey="value"
              radius={[2, 2, 0, 0]}
              isAnimationActive
              animationDuration={700}
              animationEasing="ease-out"
            >
              {chart.map((c) => (
                <Cell key={c.key} fill={c.key === "greedy" ? "#1c5b3c" : "#a9b0a6"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="panel" style={{ overflowX: "auto" }}>
        <table className="ledger">
          <thead>
            <tr>
              <th>Strategy</th>
              <th className="num">Objective</th>
              <th className="num">vs optimized</th>
              <th className="num">BGs funded</th>
              <th className="num">People reached</th>
              <th className="num">Canopy</th>
              <th className="num">Budget used</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => {
              const r = b.strategies[k];
              const best = k === "greedy";
              const beatsOnPeople = !best && r.people_reached > g.people_reached;
              return (
                <tr key={k} style={best ? { background: "var(--fund-soft)" } : undefined}>
                  <td style={{ fontWeight: best ? 600 : 400 }}>
                    {STRATEGY_LABEL[k]}
                    <div className="quiet" style={{ fontSize: 9.5, fontFamily: "var(--sans)" }}>
                      {STRATEGY_NOTE[k]}
                    </div>
                  </td>
                  <td className="num">{num(r.objective_value)}</td>
                  <td
                    className="num"
                    style={{ color: best ? "var(--fund)" : "var(--flag)" }}
                  >
                    {best ? "—" : `${r.vs_optimized_pct.toFixed(1)}%`}
                  </td>
                  <td className="num">{r.block_groups_funded}</td>
                  <td
                    className="num"
                    style={
                      beatsOnPeople
                        ? { color: "var(--flag)", fontWeight: 600 }
                        : undefined
                    }
                    title={beatsOnPeople ? "This naive strategy reaches more people" : undefined}
                  >
                    {num(r.people_reached)}
                  </td>
                  <td className="num">{m2(r.canopy_added_m2)}</td>
                  <td className="num">{(r.budget_utilisation * 100).toFixed(1)}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function FrontierPanel({ f }: { f: Frontier | null }) {
  if (!f) return <Empty>Allocate a budget to sweep the equity floor.</Empty>;

  const data = f.points.map((p) => ({
    floor: Math.round(p.equity_floor * 100),
    efficiency: p.efficiency_retained_pct,
    people: p.people_reached,
    funded: p.block_groups_funded,
  }));
  const slackTo = Math.round(f.binding_from_floor * 100);
  const last = f.points[f.points.length - 1];

  return (
    <>
      <Head note={f.note}>Cost of the equity floor · {usd(f.budget)}</Head>

      <div style={{ height: 210, marginBottom: 16 }} className="panel">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 18, right: 22, left: 4, bottom: 10 }}>
            <CartesianGrid stroke="#d7d9d1" vertical={false} />
            {/* Shading the slack region makes a flat curve read as "constraint not binding"
                rather than "chart is broken". */}
            {slackTo > 0 && (
              <ReferenceArea
                x1={0}
                x2={slackTo}
                fill="#c3c6bd"
                fillOpacity={0.32}
                label={{
                  value: "floor not binding",
                  fontSize: 9.5,
                  fill: "#4a5570",
                  fontFamily: "var(--mono)",
                  position: "insideTopLeft",
                }}
              />
            )}
            <XAxis
              dataKey="floor"
              tick={{ fontSize: 10, fill: "#4a5570", fontFamily: "var(--mono)" }}
              stroke="#c3c6bd"
              tickFormatter={(v) => `${v}%`}
              label={{
                value: "equity floor",
                fontSize: 9.5,
                fill: "#7d869b",
                position: "insideBottom",
                offset: -4,
                fontFamily: "var(--mono)",
              }}
            />
            <YAxis
              domain={["dataMin - 1", 101]}
              tick={{ fontSize: 9.5, fill: "#7d869b", fontFamily: "var(--mono)" }}
              stroke="#c3c6bd"
              tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
            />
            <Tooltip
              contentStyle={{
                background: "#f7f7f4",
                border: "1px solid #16233d",
                borderRadius: 2,
                fontFamily: "var(--mono)",
                fontSize: 11,
              }}
              formatter={(v, k) =>
                k === "efficiency"
                  ? [`${Number(v).toFixed(2)}%`, "efficiency retained"]
                  : [num(Number(v)), String(k)]
              }
              labelFormatter={(l) => `floor ${l}%`}
            />
            <Line
              type="stepAfter"
              dataKey="efficiency"
              stroke="#bf3a1f"
              strokeWidth={2}
              dot={{ r: 2.5, fill: "#bf3a1f" }}
              isAnimationActive
              animationDuration={700}
              animationEasing="ease-out"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 14 }}>
        <Stat label="Free up to" value={`${slackTo}%`} note="already achieved unconstrained" />
        <Stat
          label="First costly floor"
          value={f.first_costly_floor == null ? "none" : `${Math.round(f.first_costly_floor * 100)}%`}
          note={f.first_costly_floor == null ? "slack at every level" : "efficiency starts falling"}
        />
        <Stat
          label="Cost at 100%"
          value={`${(100 - last.efficiency_retained_pct).toFixed(2)}%`}
          note="of modelled benefit"
        />
        <Stat
          label="People at 100%"
          value={num(last.people_reached)}
          note={`vs ${num(f.points[0].people_reached)} unconstrained`}
        />
      </div>
    </>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="panel" style={{ padding: "9px 12px", minWidth: 132 }}>
      <div className="eyebrow" style={{ marginBottom: 4 }}>
        {label}
      </div>
      <div className="figure-sm">{value}</div>
      <div className="quiet" style={{ fontSize: 10, marginTop: 3 }}>
        {note}
      </div>
    </div>
  );
}

function ProvenancePanel({ r }: { r: ProvenanceReport | null }) {
  if (!r) return <Empty>Loading the provenance registry…</Empty>;

  const order = ["Measured", "Derived", "Assumed", "External", "Unknown"];
  const sorted = [...r.values].sort(
    (a, b) => order.indexOf(a.provenance) - order.indexOf(b.provenance),
  );

  return (
    <>
      <Head
        note={
          <>
            <b>
              {r.citation_pending.length} of {r.values.length}
            </b>{" "}
            values still lack a citation — placeholders, not measurements.
          </>
        }
      >
        Provenance registry
      </Head>

      {/* No overflowX: the note used to be a fifth column and was clipped mid-word. It now spans
          its own full-width row. */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <table className="ledger prov">
          {/* Widths measured against live /api/provenance content in the 581px usable panel, at a
              0.55em advance for the 11.5px mono face — 13-50px of headroom per column. See 07 §10.2. */}
          <colgroup>
            <col style={{ width: "44%" }} />
            <col style={{ width: "18%" }} />
            <col style={{ width: "22%" }} />
            <col style={{ width: "16%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>Key</th>
              <th className="num">Value</th>
              <th className="wrap">Unit</th>
              <th>Basis</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((v) => (
              <Fragment key={v.key}>
                <tr className="prov-head">
                  <td className="wrap">{v.key}</td>
                  <td
                    className={`num ${v.citation_pending ? "assumed-value" : ""}`}
                    style={{ fontWeight: 600 }}
                  >
                    {typeof v.value === "number" ? num(v.value, 2) : v.value}
                  </td>
                  <td className="wrap" style={{ color: "var(--ink-3)" }}>
                    {v.unit}
                  </td>
                  <td>
                    <Stamp
                      p={
                        v.provenance === "Measured"
                          ? "Measured"
                          : v.provenance === "Derived"
                            ? "Derived"
                            : "Assumed"
                      }
                    />
                  </td>
                </tr>
                <tr className="prov-note">
                  <td colSpan={4}>{v.note || v.source || "—"}</td>
                </tr>
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      <Head>Deliberately not computed</Head>
      <div style={{ display: "grid", gap: 8 }}>
        {r.refused.map((x) => (
          <div key={x.key} className="panel" style={{ padding: "10px 12px" }}>
            <div style={{ display: "flex", gap: 9, alignItems: "center", marginBottom: 5 }}>
              <Stamp p="Refused" />
              <span className="mono" style={{ fontSize: 11.5, fontWeight: 600 }}>
                {x.key}
              </span>
            </div>
            <div style={{ fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.5 }}>
              {x.reason}
            </div>
          </div>
        ))}
      </div>

      {/* Verbatim, not paraphrased — paraphrasing a disclosure is how disclosures get softened. */}
      <div className="warn" style={{ marginTop: 16 }}>
        {r.environment_note}
      </div>
    </>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="quiet"
      style={{ padding: "34px 4px", fontFamily: "var(--mono)", fontSize: 11.5 }}
    >
      {children}
    </div>
  );
}
