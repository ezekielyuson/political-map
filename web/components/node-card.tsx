import type { NodeView } from "@/lib/types";

const KIND_LABELS: Record<string, string> = {
  Politician: "Politician",
  PAC: "PAC",
  Company: "Company",
  GovernmentBody: "Government body",
  LobbyingFirm: "Lobbying firm",
  Individual: "Individual",
  Bill: "Bill",
  PoliticalParty: "Political party",
};

// A shallow render of arbitrary attrs. Skips nulls and keeps strings short.
function AttrList({ attrs }: { attrs: Record<string, unknown> }) {
  const entries = Object.entries(attrs).filter(
    ([k, v]) =>
      v !== null &&
      v !== undefined &&
      v !== "" &&
      k !== "external_ids" &&
      k !== "aliases" &&
      k !== "notes",
  );
  if (entries.length === 0) return null;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      {entries.map(([k, v]) => (
        <ContextRow key={k} label={k} value={v} />
      ))}
    </dl>
  );
}

function ContextRow({ label, value }: { label: string; value: unknown }) {
  let display: string;
  if (Array.isArray(value)) {
    display = value.length > 0 ? value.join(", ") : "—";
  } else if (typeof value === "object") {
    display = JSON.stringify(value);
  } else {
    display = String(value);
  }
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="font-mono text-ink truncate">{display}</dd>
    </>
  );
}

export function NodeCard({ node }: { node: NodeView }) {
  const externalIds = (node.attrs["external_ids"] ?? {}) as Record<string, string>;
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink">{node.name}</h1>
          <p className="text-xs text-muted font-mono mt-1">{node.id}</p>
        </div>
        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-muted">
          {KIND_LABELS[node.kind] ?? node.kind}
        </span>
      </div>

      {Object.keys(externalIds).length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {Object.entries(externalIds).map(([source, id]) => (
            <span
              key={`${source}:${id}`}
              className="rounded bg-slate-50 px-2 py-0.5 text-xs font-mono text-muted border border-slate-200"
              title={`${source} id`}
            >
              {source}:{id}
            </span>
          ))}
        </div>
      )}

      <div className="mt-4">
        <AttrList attrs={node.attrs} />
      </div>
    </section>
  );
}
