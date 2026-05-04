"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { Map as MaplibreMap, Marker, Popup } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { getMapConnections } from "@/lib/api";
import type { MapConnectionsResponse, MapPolitician } from "@/lib/types";

// MapLibre + a free demo tile style. Demotiles is hosted by MapLibre, fine
// for low-traffic dev/demo. For production we'd self-host or sign up at
// MapTiler / Protomaps.
const STYLE_URL = "https://demotiles.maplibre.org/style.json";

const PARTY_COLOR: Record<string, string> = {
  DEM: "#2563eb", // blue-600
  REP: "#dc2626", // red-600
  IND: "#7c3aed", // violet-600
};

const COMPANY_COLOR = "#f97316"; // orange-500
const ARC_COLOR = "#f97316";

interface Props {
  politicians: MapPolitician[];
}

export function MapView({ politicians }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MaplibreMap | null>(null);
  const politicianMarkersRef = useRef<Marker[]>([]);
  const companyMarkersRef = useRef<Marker[]>([]);

  const [selected, setSelected] = useState<MapPolitician | null>(null);
  const [connections, setConnections] = useState<MapConnectionsResponse | null>(
    null,
  );
  const [connectionsLoading, setConnectionsLoading] = useState(false);

  // 1) Initialize the map once on mount.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: STYLE_URL,
      center: [-98.5795, 39.8283], // geographic center of contiguous US
      zoom: 3.4,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.on("load", () => {
      // Add an empty source/layer for arc lines; we update its data when a
      // politician is selected.
      map.addSource("arcs", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: "arcs",
        type: "line",
        source: "arcs",
        paint: {
          "line-color": ARC_COLOR,
          "line-width": [
            "interpolate",
            ["linear"],
            ["get", "weight"],
            0, 1,
            1, 5,
          ],
          "line-opacity": 0.7,
        },
      });
    });
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // 2) Render politician dots (party-colored). Re-render when the list changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    politicianMarkersRef.current.forEach((m) => m.remove());
    politicianMarkersRef.current = [];

    for (const p of politicians) {
      const color = PARTY_COLOR[p.party ?? ""] ?? "#64748b";
      const el = document.createElement("button");
      el.className =
        "rounded-full border border-white shadow-sm hover:scale-125 transition-transform cursor-pointer";
      el.style.backgroundColor = color;
      el.style.width = "10px";
      el.style.height = "10px";
      el.title = `${p.name} (${p.party ?? "?"} — ${p.state ?? "?"})`;
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        setSelected(p);
      });
      const marker = new maplibregl.Marker({ element: el })
        .setLngLat([p.longitude, p.latitude])
        .addTo(map);
      politicianMarkersRef.current.push(marker);
    }
  }, [politicians]);

  // 3) When a politician is selected, fetch their connections.
  useEffect(() => {
    if (!selected) {
      setConnections(null);
      return;
    }
    let cancelled = false;
    setConnectionsLoading(true);
    getMapConnections(selected.id)
      .then((res) => {
        if (!cancelled) setConnections(res);
      })
      .catch(() => {
        if (!cancelled) setConnections(null);
      })
      .finally(() => {
        if (!cancelled) setConnectionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  // 4) When connections arrive, draw company markers + arcs.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    // Wipe previous company markers.
    companyMarkersRef.current.forEach((m) => m.remove());
    companyMarkersRef.current = [];

    // Reset arc source.
    const arcSource = map.getSource("arcs") as
      | maplibregl.GeoJSONSource
      | undefined;
    if (!arcSource) {
      // Map hasn't finished loading yet (load handler runs on first 'load').
      // Schedule for next frame.
      setTimeout(() => {
        const s = map.getSource("arcs") as maplibregl.GeoJSONSource | undefined;
        s?.setData({ type: "FeatureCollection", features: [] });
      }, 50);
    } else {
      arcSource.setData({ type: "FeatureCollection", features: [] });
    }

    if (!selected || !connections || connections.connections.length === 0) {
      return;
    }

    const polLat = connections.politician.latitude;
    const polLng = connections.politician.longitude;
    if (polLat == null || polLng == null) return;

    // Find the largest donation so we can normalize line widths.
    const maxCents = Math.max(
      ...connections.connections.map((c) => c.total_cents),
    );

    type ArcFeature = GeoJSON.Feature<GeoJSON.LineString, { weight: number }>;
    const arcFeatures: ArcFeature[] = [];

    for (const conn of connections.connections) {
      // Place a logo marker at the company HQ.
      const wrap = document.createElement("button");
      wrap.className =
        "flex flex-col items-center gap-0.5 cursor-pointer hover:scale-105 transition-transform";
      const img = document.createElement("img");
      img.src = conn.logo_url ?? "";
      img.alt = conn.name;
      img.className = "rounded-md bg-white shadow border border-slate-200";
      img.style.width = "36px";
      img.style.height = "36px";
      img.style.objectFit = "contain";
      img.style.padding = "3px";
      img.onerror = () => {
        // Logo failed -> render a colored circle with the company's initial.
        img.style.display = "none";
        const fallback = document.createElement("div");
        fallback.textContent = conn.name[0] ?? "?";
        fallback.className =
          "rounded-md flex items-center justify-center font-semibold text-white shadow";
        fallback.style.width = "36px";
        fallback.style.height = "36px";
        fallback.style.backgroundColor = COMPANY_COLOR;
        wrap.insertBefore(fallback, wrap.firstChild);
      };
      wrap.appendChild(img);

      const popup = new Popup({ offset: 10, closeButton: false }).setHTML(
        `<div style="font-family:ui-sans-serif">
           <div style="font-weight:600">${conn.name}</div>
           <div style="font-size:11px;color:#64748b">${conn.hq_city ?? ""}${
             conn.hq_state ? ", " + conn.hq_state : ""
           }</div>
           <div style="font-size:12px;margin-top:4px">
             $${(conn.total_cents / 100).toLocaleString("en-US", {
               maximumFractionDigits: 0,
             })} via ${conn.pacs.length} PAC${conn.pacs.length === 1 ? "" : "s"}
           </div>
         </div>`,
      );

      const marker = new maplibregl.Marker({ element: wrap, anchor: "center" })
        .setLngLat([conn.longitude, conn.latitude])
        .setPopup(popup)
        .addTo(map);
      companyMarkersRef.current.push(marker);

      // Arc: politician -> company. We approximate a great circle with a
      // simple parabolic curve in lng/lat space; good enough at US scale.
      const N = 32;
      const arcCoords: [number, number][] = [];
      for (let i = 0; i <= N; i++) {
        const t = i / N;
        const lng = polLng + (conn.longitude - polLng) * t;
        const lat = polLat + (conn.latitude - polLat) * t;
        // Slight curvature: bulge perpendicular by 1.5 degrees at midpoint.
        const dx = conn.longitude - polLng;
        const dy = conn.latitude - polLat;
        const dist = Math.sqrt(dx * dx + dy * dy);
        const bulge = 0.15 * dist * Math.sin(Math.PI * t);
        // Normal direction (perpendicular to dx,dy)
        const nx = -dy / (dist || 1);
        const ny = dx / (dist || 1);
        arcCoords.push([lng + nx * bulge, lat + ny * bulge]);
      }

      arcFeatures.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: arcCoords },
        properties: { weight: conn.total_cents / maxCents },
      });
    }

    const s = map.getSource("arcs") as maplibregl.GeoJSONSource | undefined;
    if (s) {
      s.setData({ type: "FeatureCollection", features: arcFeatures });
    }
  }, [selected, connections]);

  // Bottom panel summary.
  const summary = useMemo(() => {
    if (!selected) return null;
    if (connectionsLoading) return "loading connections…";
    if (!connections) return "no connections found.";
    const c = connections.connections;
    if (c.length === 0)
      return `${selected.name} has no donations from tracked corporate PACs.`;
    const total = c.reduce((s, x) => s + x.total_cents, 0);
    return `${selected.name}: $${(total / 100).toLocaleString("en-US", {
      maximumFractionDigits: 0,
    })} from ${c.length} ${c.length === 1 ? "company" : "companies"}.`;
  }, [selected, connections, connectionsLoading]);

  return (
    <div className="space-y-3">
      <div
        ref={containerRef}
        style={{ width: "100%", height: "640px", borderRadius: "8px" }}
        className="border border-slate-200 overflow-hidden bg-slate-100"
        onClick={() => setSelected(null)}
      />

      <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-3 rounded-full"
            style={{ backgroundColor: PARTY_COLOR.DEM }}
          />
          Democrat
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-3 rounded-full"
            style={{ backgroundColor: PARTY_COLOR.REP }}
          />
          Republican
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-3 rounded-full"
            style={{ backgroundColor: PARTY_COLOR.IND }}
          />
          Independent
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-4 h-4 rounded"
            style={{ backgroundColor: COMPANY_COLOR }}
          />
          Company HQ (when politician selected)
        </span>
      </div>

      {selected && (
        <div className="rounded-md border border-slate-200 bg-white p-4 text-sm">
          <div className="flex items-baseline justify-between gap-3">
            <div>
              <div className="font-medium text-ink">{selected.name}</div>
              <div className="text-xs text-muted">
                {selected.party ?? "?"} · {selected.chamber ?? "?"} ·{" "}
                {selected.state ?? "?"}
                {selected.district ? `-${selected.district}` : ""}
              </div>
            </div>
            <a
              href={`/nodes/${encodeURIComponent(selected.id)}`}
              className="text-xs underline"
            >
              full profile →
            </a>
          </div>
          <p className="mt-2 text-muted">{summary}</p>
          {connections && connections.connections.length > 0 && (
            <ul className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
              {connections.connections.slice(0, 10).map((c) => (
                <li key={c.company_id} className="flex items-center gap-2">
                  {c.logo_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={c.logo_url}
                      alt={c.name}
                      style={{ width: 18, height: 18, objectFit: "contain" }}
                      className="rounded bg-white border border-slate-200"
                    />
                  )}
                  <span className="font-medium">{c.name}</span>
                  <span className="text-muted ml-auto">
                    $
                    {(c.total_cents / 100).toLocaleString("en-US", {
                      maximumFractionDigits: 0,
                    })}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
