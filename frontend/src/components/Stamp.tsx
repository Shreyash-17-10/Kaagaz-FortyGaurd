"use client";

/**
 * Provenance primitives. An ASSUMED number must be distinguishable from a MEASURED one without
 * reading a footnote, so `Stamp` carries a diagonal hatch and `Figure` dots the underline of any
 * value whose basis is assumed.
 */

import type { ReactNode } from "react";

export type Prov = "Measured" | "Derived" | "Assumed" | "Refused";

const CLS: Record<Prov, string> = {
  Measured: "stamp stamp-measured",
  Derived: "stamp stamp-derived",
  Assumed: "stamp stamp-assumed",
  Refused: "stamp stamp-refused",
};

const TITLE: Record<Prov, string> = {
  Measured: "Read directly from a source dataset.",
  Derived: "Computed from measured values by a stated formula.",
  Assumed: "A placeholder with no citation in this workspace. Treat with suspicion.",
  Refused: "Deliberately not computed — no defensible basis exists.",
};

export function Stamp({ p }: { p: Prov }) {
  return (
    <span className={CLS[p]} title={TITLE[p]}>
      {p}
    </span>
  );
}

/** A headline number with its provenance attached. The two are never separated. */
export function Figure({
  label,
  value,
  sub,
  prov,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  prov: Prov;
  tone?: "fund" | "heat";
}) {
  const color =
    tone === "fund" ? "var(--fund)" : tone === "heat" ? "var(--heat-4)" : "var(--ink)";
  return (
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
        <span className="eyebrow">{label}</span>
        <Stamp p={prov} />
      </div>
      <div
        className={`figure ${prov === "Assumed" ? "assumed-value" : ""}`}
        style={{ color }}
      >
        {value}
      </div>
      {sub ? (
        <div className="quiet mono" style={{ marginTop: 7, fontSize: 10.5 }}>
          {sub}
        </div>
      ) : null}
    </div>
  );
}

/** Section heading used across the analysis panels. */
export function Head({ children, note }: { children: ReactNode; note?: ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="eyebrow">{children}</div>
      {note ? (
        <div className="quiet" style={{ marginTop: 5, maxWidth: 720 }}>
          {note}
        </div>
      ) : null}
    </div>
  );
}
