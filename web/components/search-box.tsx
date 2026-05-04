"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { searchNodes } from "@/lib/api";
import type { NodeView } from "@/lib/types";
import { NODE_KINDS } from "@/lib/types";

// Debounced live-search box. Hits /nodes?q=&kind= as the user types.
export function SearchBox() {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<string>("");
  const [results, setResults] = useState<NodeView[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Empty query + no kind filter -> clear, don't hit the API on every keystroke.
    if (!q.trim() && !kind) {
      setResults([]);
      setError(null);
      return;
    }
    const handle = setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await searchNodes({
          q: q.trim() || undefined,
          kind: kind || undefined,
          limit: 25,
        });
        setResults(res.nodes);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    }, 200);
    return () => clearTimeout(handle);
  }, [q, kind]);

  return (
    <section className="space-y-4">
      <div className="flex flex-col sm:flex-row gap-3">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search by name (e.g. 'Bernie' or 'Acme')"
          className="flex-1 rounded-md border border-slate-300 bg-white px-4 py-2.5 text-base shadow-sm focus:border-accent focus:outline-none"
          autoFocus
        />
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          className="rounded-md border border-slate-300 bg-white px-3 py-2.5 text-base shadow-sm"
        >
          <option value="">Any kind</option>
          {NODE_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      )}

      {loading && <p className="text-sm text-muted">searching…</p>}

      {!loading && (q.trim() || kind) && results.length === 0 && !error && (
        <p className="text-sm text-muted">No matches.</p>
      )}

      {results.length > 0 && (
        <ul className="divide-y divide-slate-200 rounded-md border border-slate-200 bg-white">
          {results.map((n) => (
            <li key={n.id}>
              <Link
                href={`/nodes/${encodeURIComponent(n.id)}`}
                className="block px-4 py-3 no-underline hover:bg-slate-50"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <div>
                    <div className="font-medium text-ink">{n.name}</div>
                    <div className="text-xs text-muted font-mono">{n.id}</div>
                  </div>
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-muted">
                    {n.kind}
                  </span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
