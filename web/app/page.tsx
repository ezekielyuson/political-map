import { SearchBox } from "@/components/search-box";
import { getHealth } from "@/lib/api";

export const dynamic = "force-dynamic";

// Home page: hero + live search + a small banner with DB stats.
// We render the stats server-side for first-paint speed; everything below
// the fold is client-driven.
export default async function Home() {
  let stats: Awaited<ReturnType<typeof getHealth>> | null = null;
  let healthError: string | null = null;
  try {
    stats = await getHealth();
  } catch (e) {
    healthError = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="space-y-10">
      <section className="space-y-3">
        <h1 className="text-3xl font-semibold text-ink">
          Browse the political graph.
        </h1>
        <p className="text-muted max-w-2xl">
          Politicians, PACs, committees, lobbying firms — and the ties between
          them. Every connection carries provenance: where it came from, when
          it was true, how confident we are.
        </p>
      </section>

      <SearchBox />

      <section className="rounded-lg border border-slate-200 bg-white p-5 text-sm">
        <h2 className="font-semibold text-ink mb-2">In the graph right now</h2>
        {healthError ? (
          <p className="text-red-700">
            Couldn&apos;t reach the API ({healthError}).{" "}
            <a
              href={`${process.env.NEXT_PUBLIC_PGE_API_URL ?? "http://localhost:8000"}/health`}
              target="_blank"
              rel="noreferrer"
            >
              Check /health directly
            </a>
            .
          </p>
        ) : stats ? (
          <dl className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <Stat label="Nodes" value={stats.nodes_total} />
            <Stat label="Edges" value={stats.edges_total} />
            <Stat
              label="Politicians"
              value={stats.nodes_by_kind.Politician ?? 0}
            />
            <Stat
              label="Verified edges"
              value={stats.edges_by_evidence.VERIFIED ?? 0}
            />
          </dl>
        ) : (
          <p className="text-muted">loading…</p>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-muted text-xs uppercase tracking-wide">{label}</dt>
      <dd className="text-2xl font-semibold text-ink mt-1">
        {value.toLocaleString()}
      </dd>
    </div>
  );
}
