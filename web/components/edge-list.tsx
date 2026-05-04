import Link from "next/link";
import type { EdgeView, NodeView } from "@/lib/types";

// One row per edge. We resolve the "other side" (the neighbor relative to
// the focal node) and render its name + kind, plus the edge's own metadata.
//
// Provenance is foregrounded: every row shows evidence_type and source_name
// because that's the dossier shape we promised in the build plan.

interface Props {
  focalId: string;
  edges: EdgeView[];
  nodesById: Map<string, NodeView>;
}

const EVIDENCE_COLORS: Record<string, string> = {
  VERIFIED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  REPORTED: "bg-amber-50 text-amber-700 border-amber-200",
  INFERRED: "bg-sky-50 text-sky-700 border-sky-200",
};

function formatAmount(cents: unknown): string | null {
  if (typeof cents !== "number") return null;
  const dollars = cents / 100;
  return dollars.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

function edgeSummary(edge: EdgeView): string | null {
  const a = edge.attrs as Record<string, unknown>;
  const amount = formatAmount(a.amount_cents);
  const parts: string[] = [];
  if (amount) parts.push(amount);
  if (typeof a.role === "string") parts.push(a.role);
  if (typeof a.position === "string") parts.push(a.position);
  if (typeof a.quarter === "string") parts.push(a.quarter);
  if (Array.isArray(a.issue_codes) && a.issue_codes.length > 0) {
    parts.push((a.issue_codes as string[]).slice(0, 4).join(", "));
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function EdgeList({ focalId, edges, nodesById }: Props) {
  if (edges.length === 0) {
    return (
      <p className="text-sm text-muted">
        No connections. (Bootstrap data has nodes only — run an ingest with API
        keys to land donations, committee assignments, and lobbying contracts.)
      </p>
    );
  }

  // Group by edge kind for readability.
  const grouped = new Map<string, EdgeView[]>();
  for (const e of edges) {
    const list = grouped.get(e.kind) ?? [];
    list.push(e);
    grouped.set(e.kind, list);
  }

  return (
    <div className="space-y-6">
      {[...grouped.entries()].map(([kind, kindEdges]) => (
        <section key={kind}>
          <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted">
            {kind} ({kindEdges.length})
          </h3>
          <ul className="divide-y divide-slate-200 rounded-md border border-slate-200 bg-white">
            {kindEdges.map((edge) => {
              const otherId =
                edge.src_id === focalId ? edge.dst_id : edge.src_id;
              const other = nodesById.get(otherId);
              const summary = edgeSummary(edge);
              const evidenceClass =
                EVIDENCE_COLORS[edge.evidence_type] ??
                "bg-slate-50 text-slate-700 border-slate-200";
              return (
                <li key={edge.id}>
                  <Link
                    href={`/nodes/${encodeURIComponent(otherId)}`}
                    className="block px-4 py-3 no-underline hover:bg-slate-50"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-medium text-ink truncate">
                          {other?.name ?? otherId}
                        </div>
                        <div className="mt-1 flex items-center gap-2 text-xs">
                          <span
                            className={`rounded border px-1.5 py-0.5 ${evidenceClass}`}
                          >
                            {edge.evidence_type}
                          </span>
                          <span className="text-muted">
                            via {edge.source_name}
                          </span>
                          {edge.as_of_date && (
                            <span className="text-muted">
                              · {edge.as_of_date}
                            </span>
                          )}
                          {edge.confidence && (
                            <span className="text-muted">
                              · {edge.confidence} confidence
                            </span>
                          )}
                        </div>
                        {summary && (
                          <div className="mt-1 text-sm text-ink">{summary}</div>
                        )}
                      </div>
                      <span className="text-muted text-sm">→</span>
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
