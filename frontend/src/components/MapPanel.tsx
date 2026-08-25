"use client";

/**
 * The map. NEED shows the heat exposure index; PLAN shows where the budget actually goes.
 * Flipping between them is the thesis: knowing where it is hot does not tell you where to spend,
 * so block groups that receive nothing stay visible as outlines rather than disappearing.
 */

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import {
  type Allocation,
  type AoiResponse,
  HEAT_RAMP,
  num,
  usdFull,
} from "@/lib/api";

export type MapMode = "need" | "plan";

const GROUND = "#e7e8e2";
const PLAN_RAMP = ["#b9cfc0", "#84ab90", "#4f8560", "#356e4e", "#1c5b3c"];

type Bbox = [[number, number], [number, number]];

/**
 * Bounding box of the AOI. `f.geometry` is a GeoJSON geometry *object*, so the walk starts at
 * `.coordinates` — walking the object itself fails `Array.isArray` on the first call and yields
 * null bounds, which is what kept the view at metro scale.
 */
function aoiBounds(aoi: AoiResponse): Bbox | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  const walk = (node: unknown): void => {
    if (!Array.isArray(node)) return;
    if (typeof node[0] === "number" && typeof node[1] === "number") {
      const x = node[0] as number;
      const y = node[1] as number;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
      return;
    }
    for (const child of node) walk(child);
  };

  for (const f of aoi.features) {
    const g = f.geometry as { coordinates?: unknown } | null;
    if (g && typeof g === "object") walk(g.coordinates);
  }

  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;
  return [
    [minX, minY],
    [maxX, maxY],
  ];
}

/** Attach the plan to each feature so one source drives both modes. */
function withPlan(aoi: AoiResponse, allocations: Allocation[]) {
  const byGeoid = new Map<string, { cost: number; trees: number; roof: number; shade: number }>();
  for (const a of allocations) {
    const e = byGeoid.get(a.GEOID) ?? { cost: 0, trees: 0, roof: 0, shade: 0 };
    e.cost += a.cost;
    if (a.intervention === "tree") e.trees += a.units;
    if (a.intervention === "cool_roof") e.roof += a.units;
    if (a.intervention === "shade") e.shade += a.units;
    byGeoid.set(a.GEOID, e);
  }

  const maxCost = Math.max(1, ...[...byGeoid.values()].map((v) => v.cost));

  return {
    type: "FeatureCollection" as const,
    features: aoi.features.map((f) => {
      const a = byGeoid.get(f.properties.GEOID);
      return {
        ...f,
        properties: {
          ...f.properties,
          cost: a?.cost ?? 0,
          trees: a?.trees ?? 0,
          roof_m2: a?.roof ?? 0,
          shade_n: a?.shade ?? 0,
          cost_share: a ? a.cost / maxCost : 0,
          funded: a ? 1 : 0,
        },
      };
    }),
  };
}

function paintMode(m: maplibregl.Map, mode: MapMode, aoi: AoiResponse) {
  if (!m.getLayer("bg-fill")) return;

  if (mode === "need") {
    const [lo, hi] = aoi.meta.hei_range;
    const stops = HEAT_RAMP.flatMap((c, i) => [lo + ((hi - lo) * i) / 4, c]);
    m.setPaintProperty("bg-fill", "fill-color", [
      "interpolate",
      ["linear"],
      ["get", "hei"],
      ...stops,
    ]);
    m.setPaintProperty("bg-fill", "fill-opacity", 0.8);
    m.setPaintProperty("bg-funded", "line-opacity", 0);
    return;
  }

  // Unfunded block groups fade to the paper ground rather than vanishing: they received nothing,
  // and that is the point being made.
  m.setPaintProperty("bg-fill", "fill-color", [
    "case",
    ["==", ["get", "funded"], 0],
    GROUND,
    ["interpolate", ["linear"], ["get", "cost_share"], 0, PLAN_RAMP[0], 0.5, PLAN_RAMP[2], 1, PLAN_RAMP[4]],
  ]);
  m.setPaintProperty("bg-fill", "fill-opacity", ["case", ["==", ["get", "funded"], 0], 0.35, 0.85]);
  m.setPaintProperty("bg-funded", "line-opacity", 0.9);
}

function popupHtml(p: Record<string, number | string>) {
  const rows: [string, string][] = [
    ["HEI", `${Number(p.hei).toFixed(4)}  (rank ${p.hei_rank}/48)`],
    ["thermal", Number(p.hei_thermal).toFixed(3)],
    ["density", Number(p.hei_density).toFixed(3)],
    ["sensitivity", Number(p.hei_sensitivity).toFixed(3)],
    ["population", num(Number(p.acs_pop))],
    [
      "canopy",
      `${(Number(p.treecanopy) * 100).toFixed(1)}% → goal ${(Number(p.tc_goal) * 100).toFixed(0)}%`,
    ],
    ["plantable", `${num(Number(p.cap_tree))} trees`],
  ];

  const funded = Number(p.funded) === 1;
  const money = funded
    ? `<div style="font-size:10.5px;color:#1c5b3c;line-height:1.7">
         <b>${usdFull(Number(p.cost))}</b> allocated<br/>
         ${Number(p.trees) > 0 ? `${num(Number(p.trees))} trees<br/>` : ""}
         ${Number(p.roof_m2) > 0 ? `${num(Number(p.roof_m2))} m² cool roof<br/>` : ""}
         ${Number(p.shade_n) > 0 ? `${num(Number(p.shade_n))} shade structures` : ""}
       </div>`
    : `<div style="font-size:10.5px;color:#8f3a0c">Not funded at this budget</div>`;

  return `<div style="padding:10px 12px">
    <div style="font-size:11px;font-weight:600;letter-spacing:.06em">${p.GEOID}</div>
    <div style="height:1px;background:#c3c6bd;margin:8px 0"></div>
    ${rows
      .map(
        ([k, v]) =>
          `<div style="display:flex;justify-content:space-between;gap:14px;font-size:10.5px;line-height:1.7">
             <span style="color:#7d869b">${k}</span><span>${v}</span></div>`,
      )
      .join("")}
    <div style="height:1px;background:#c3c6bd;margin:8px 0"></div>
    ${money}
  </div>`;
}

export default function MapPanel({
  aoi,
  allocations,
  mode,
  onModeChange,
  funded,
  total,
}: {
  aoi: AoiResponse | null;
  allocations: Allocation[];
  mode: MapMode;
  onModeChange: (m: MapMode) => void;
  funded: number;
  total: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const pop = useRef<maplibregl.Popup | null>(null);
  const fitted = useRef(false);
  const hovered = useRef(false);

  const [ready, setReady] = useState(false);
  const [basemap, setBasemap] = useState(true);
  // A blank basemap must never pass as "no data here" — that is a claim about Atlanta, not a
  // report of a failed render.
  const [layerError, setLayerError] = useState<string | null>(null);

  /* ---------------- init ---------------- */
  useEffect(() => {
    if (map.current || !box.current) return;

    const m = new maplibregl.Map({
      container: box.current,
      style: {
        version: 8,
        // Flat ledger-paper ground, so the map still reads as designed with no tiles at all.
        sources: {
          osm: {
            type: "raster",
            tiles: [
              "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
              "https://b.tile.openstreetmap.org/{z}/{x}/{y}.png",
              "https://c.tile.openstreetmap.org/{z}/{x}/{y}.png",
            ],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [
          { id: "bg", type: "background", paint: { "background-color": GROUND } },
          {
            id: "osm",
            type: "raster",
            source: "osm",
            paint: { "raster-opacity": 0.5, "raster-saturation": -0.75 },
          },
        ],
      },
      center: [-84.3947, 33.7607],
      zoom: 13.4,
      attributionControl: { compact: true },
    });

    map.current = m;
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    pop.current = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      className: "hr-popup",
      maxWidth: "290px",
    });

    const ro = new ResizeObserver(() => map.current?.resize());
    ro.observe(box.current);
    m.on("load", () => setReady(true));

    return () => {
      ro.disconnect();
      map.current?.remove();
      map.current = null;
      setReady(false);
      fitted.current = false;
      hovered.current = false;
    };
  }, []);

  /* ---------------- data + layers ---------------- */
  useEffect(() => {
    const m = map.current;
    if (!m || !ready || !aoi) return;

    const fc = withPlan(aoi, allocations);
    let installed = false;

    /**
     * Attempt the install, and report whether it stuck. Deliberately does NOT gate on
     * `isStyleLoaded()`: that also requires every tile manager to be loaded (maplibre-gl 6.5.0,
     * src/style/style.ts:595), so a streaming raster basemap keeps it false long after the style
     * spec is parsed — which is the only precondition `addSource` really has.
     */
    const install = (): boolean => {
      const mm = map.current;
      if (installed || !mm) return true;

      try {
        const src = mm.getSource("bg-src") as maplibregl.GeoJSONSource | undefined;
        if (src) {
          src.setData(fc as never);
        } else {
          mm.addSource("bg-src", { type: "geojson", data: fc as never });
          mm.addLayer({
            id: "bg-fill",
            type: "fill",
            source: "bg-src",
            paint: {
              "fill-color": HEAT_RAMP[2],
              "fill-opacity": 0.8,
              // Recolours (mode flip, new weights, new budget) glide rather than snapping, so a
              // live update reads as motion instead of a jump-cut.
              "fill-color-transition": { duration: 500, delay: 0 },
              "fill-opacity-transition": { duration: 500, delay: 0 },
            },
          });
          mm.addLayer({
            id: "bg-line",
            type: "line",
            source: "bg-src",
            paint: { "line-color": "#16233d", "line-width": 0.6, "line-opacity": 0.5 },
          });
          // Funded block groups get a heavier keyline in PLAN mode.
          mm.addLayer({
            id: "bg-funded",
            type: "line",
            source: "bg-src",
            filter: ["==", ["get", "funded"], 1],
            paint: {
              "line-color": "#1c5b3c",
              "line-width": 2.2,
              "line-opacity": 0,
              "line-opacity-transition": { duration: 400, delay: 0 },
            },
          });
        }

        paintMode(mm, mode, aoi);

        if (!fitted.current) {
          const b = aoiBounds(aoi);
          if (b) {
            mm.fitBounds(b, { padding: 56, duration: 0, maxZoom: 15 });
            fitted.current = true;
          }
        }

        if (!hovered.current) {
          mm.on("mousemove", "bg-fill", (e) => {
            if (!e.features?.length || !map.current || !pop.current) return;
            map.current.getCanvas().style.cursor = "pointer";
            pop.current
              .setLngLat(e.lngLat)
              .setHTML(popupHtml(e.features[0].properties as Record<string, number | string>))
              .addTo(map.current);
          });
          mm.on("mouseleave", "bg-fill", () => {
            if (!map.current) return;
            map.current.getCanvas().style.cursor = "";
            pop.current?.remove();
          });
          hovered.current = true;
        }

        installed = true;
        setLayerError(null);
        return true;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        // The one recoverable case: the style spec genuinely is not parsed yet. Anything else is
        // a real fault and must be shown rather than retried in silence.
        if (/not done loading/i.test(msg)) return false;
        setLayerError(msg);
        return true;
      }
    };

    if (install()) return;

    // `idle` is the reliable backstop: `styledata` stops firing once the style settles.
    const retry = () => {
      if (install()) {
        m.off("idle", retry);
        m.off("styledata", retry);
      }
    };
    m.on("idle", retry);
    m.on("styledata", retry);
    return () => {
      m.off("idle", retry);
      m.off("styledata", retry);
    };
  }, [aoi, allocations, ready, mode]);

  useEffect(() => {
    const m = map.current;
    if (m && ready && aoi) paintMode(m, mode, aoi);
  }, [mode, ready, aoi]);

  useEffect(() => {
    const m = map.current;
    if (m && ready && m.getLayer("osm"))
      m.setPaintProperty("osm", "raster-opacity", basemap ? 0.5 : 0);
  }, [basemap, ready]);

  const [lo, hi] = aoi?.meta.hei_range ?? [0, 1];

  return (
    <div style={{ position: "relative", flex: 1, minHeight: 0 }}>
      <div ref={box} style={{ position: "absolute", inset: 0 }} />

      {/* Empty / failed-fetch state. A blank basemap must read as "no data yet", never as a claim
          that Atlanta has no heat — and it says plainly that the data is fetched, not baked in. */}
      {!aoi && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 4,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 20,
            background: "rgba(231, 232, 226, 0.72)",
          }}
        >
          <div className="panel" style={{ padding: "16px 18px", maxWidth: 330 }}>
            <div className="eyebrow" style={{ marginBottom: 7 }}>
              No block groups loaded
            </div>
            <div style={{ fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.55 }}>
              Geometry and every figure stream from the backend at{" "}
              <b className="mono" style={{ color: "var(--ink)" }}>
                localhost:8000
              </b>{" "}
              — nothing is baked in. If the map stays empty, start the backend.
            </div>
          </div>
        </div>
      )}

      {layerError && (
        <div
          className="panel warn mono"
          style={{
            position: "absolute",
            top: 14,
            left: 260,
            right: 14,
            zIndex: 5,
            padding: "9px 11px",
            fontSize: 10.5,
          }}
        >
          Map layers failed to render — panel figures are unaffected. {layerError}
        </div>
      )}

      <div style={{ position: "absolute", top: 14, left: 14, width: 232 }}>
        <div className="toggle-row" style={{ background: "var(--panel)" }}>
          <button
            aria-pressed={mode === "need"}
            onClick={() => onModeChange("need")}
            title="Heat exposure index — what a heat map shows you"
          >
            Need
          </button>
          <button
            aria-pressed={mode === "plan"}
            onClick={() => onModeChange("plan")}
            title="Where this budget actually goes"
          >
            Plan
          </button>
        </div>
        <div
          className="panel mono"
          style={{ marginTop: 7, padding: "7px 9px", fontSize: 10, color: "var(--ink-2)" }}
        >
          {mode === "need"
            ? "Heat exposure — where the need is"
            : `${funded} of ${total} funded · ${total - funded} get nothing`}
        </div>
      </div>

      <div
        className="panel"
        style={{ position: "absolute", bottom: 26, left: 14, padding: "10px 12px", width: 232 }}
      >
        <div className="eyebrow" style={{ marginBottom: 7 }}>
          {mode === "need" ? "Heat exposure index" : "Allocation per block group"}
        </div>
        <div style={{ display: "flex", height: 9, marginBottom: 5 }}>
          {(mode === "need" ? HEAT_RAMP : PLAN_RAMP).map((c) => (
            <div key={c} style={{ flex: 1, background: c }} />
          ))}
        </div>
        <div
          className="mono"
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: 9.5,
            color: "var(--ink-3)",
          }}
        >
          <span>{mode === "need" ? lo.toFixed(2) : "low"}</span>
          <span>{mode === "need" ? hi.toFixed(2) : "high"}</span>
        </div>
        {mode === "plan" && (
          <div
            className="mono"
            style={{
              marginTop: 8,
              fontSize: 9.5,
              color: "var(--ink-3)",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span
              style={{
                width: 14,
                height: 9,
                background: GROUND,
                border: "1px solid #16233d",
                opacity: 0.5,
              }}
            />
            not funded
          </div>
        )}

        <button
          className="mono"
          onClick={() => setBasemap((b) => !b)}
          style={{
            marginTop: 9,
            fontSize: 9.5,
            background: "none",
            border: 0,
            padding: 0,
            color: "var(--ink-3)",
            cursor: "pointer",
            textDecoration: "underline",
          }}
        >
          {basemap ? "hide basemap" : "show basemap"}
        </button>
      </div>
    </div>
  );
}
