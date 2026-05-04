"use client";

import { useState } from "react";
import Link from "next/link";
import { findPaths, indexSubgraph } from "@/lib/api";
import type { PathsView } from "@/lib/types";

// Path finder: two ids in, list of paths out. Each hop renders as a
// node-edge-node strip with the connecting edge's evidence type and source
// in the chip.
export default function PathsPage() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [maxDepth, setMaxDepth] = useState(3);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PathsView | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!from.trim() || !to.trim()) {
      setError("Both from and to are required.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await findPaths({
        from: from.trim(),
        to: to.trim(),
        maxDepth,
      });
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const idx = result ? indexSubgraph(result) : null;

  return (
    <div className="space-y-8">
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold text-ink">Paths between two nodes</h1>
        <p className="text-muted text-sm max-w-2xl">
          Bidirectional BFS up to ``max_depth`` hops. Use ids you copied from a
          search result or a node page (the small monospaced text under each name).
        </p>
      </section>

      <form
        onSubmit={onSubmit}
        className="rounded-lg border border-slate-200 bg-white p-5 space-y-3"
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-muted">From id</span>
            <input
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              placeholder="pol:S001229"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm shadow-sm focus:border-accent focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-muted">To id</span>
            <input
              value={to}
              onChange={(e) => setTo(e.target.value)}
              placeholder="pac:C00123456"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm shadow-sm focus:border-accent focus:outline-none"
            />
          </label>
        </div>
        <div className="flex items-end gap-3">
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-muted">Max depth</span>
            <input
              type="number"
              min={1}
              max={5}
              value={maxDepth}
              onChange={(e) => setMaxDepth(Number(e.target.value))}
              className="mt-1 w-24 rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm"
            />
          </label>
          <button
            type="submit"
            disabled={loading}
            className="ml-auto rounded-md bg-accent px-4 py-2 text-white text-sm font-medium hover:bg-blue-600 disabled:opacity-50"
          >
            {loading ? "Searching…" : "Find paths"}
          </button>
        </div>
        {error && (
          <p className="text-sm text-red-700">{error}</p>
        )}
      </form>

      {result && idx && (
        <section className="space-y-4">
          {result.paths.length === 0 ? (
            <p className="text-muted">
              No path found within {maxDepth} hop{maxDepth === 1 ? "" : "s"}.
            </p>
          ) : (
            <>
              <p className="text-sm text-muted">
                {result.paths.length} path
                {result.paths.length === 1 ? "" : "s"} found.
              </p>
              <ul className="space-y-3">
                {result.paths.map((path, i) => (
                  <li
                    key={i}
                    className="rounded-lg border border-slate-200 bg-white p-4 overflow-x-auto"
                  >
                    <div className="flex items-center gap-2 whitespace-nowrap">
                      <NodeChip
                        nodeId={path[0].from_node}
                        name={
                          idx.nodesById.get(path[0].from_node)?.name ??
                          path[0].from_node
                        }
                      />
                      {path.map((hop) => {
                        const edge = idx.edgesById.get(hop.edge_id);
                        const target =
                          idx.nodesById.get(hop.to_node)?.name ?? hop.to_node;
                        return (
                          <span
                            key={hop.edge_id}
                            className="flex items-center gap-2"
                          >
                            <span className="text-muted text-xs">
                              {edge?.kind ?? "?"}
                            </span>
                            <span className="text-muted">→</span>
                            <NodeChip nodeId={hop.to_node} name={target} />
                          </span>
                        );
                      })}
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
    </div>
  );
}

function NodeChip({ nodeId, name }: { nodeId: string; name: string }) {
  return (
    <Link
      href={`/nodes/${encodeURIComponent(nodeId)}`}
      className="inline-block rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1 text-sm no-underline hover:bg-slate-100"
    >
      <span className="font-medium text-ink">{name}</span>
      <span className="ml-2 text-xs text-muted font-mono">{nodeId}</span>
    </Link>
  );
}
