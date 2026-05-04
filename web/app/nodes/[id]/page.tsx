import { notFound } from "next/navigation";
import { NodeCard } from "@/components/node-card";
import { EdgeList } from "@/components/edge-list";
import { getNeighbors, getNode, indexSubgraph } from "@/lib/api";

export const dynamic = "force-dynamic";

interface PageProps {
  params: Promise<{ id: string }>;
}

// Server component: fetches the node + 1-hop neighborhood server-side, so
// the page comes back fully rendered. No client-side spinners on the
// initial paint.
export default async function NodePage({ params }: PageProps) {
  const { id: rawId } = await params;
  const id = decodeURIComponent(rawId);

  const node = await getNode(id);
  if (!node) notFound();

  const subgraph = await getNeighbors(id, { depth: 1 });
  const edges = subgraph?.edges ?? [];
  const { nodesById } = indexSubgraph(subgraph ?? { nodes: [], edges: [] });

  return (
    <div className="space-y-8">
      <NodeCard node={node} />

      <section>
        <h2 className="text-xl font-semibold text-ink mb-3">Connections</h2>
        <EdgeList focalId={node.id} edges={edges} nodesById={nodesById} />
      </section>
    </div>
  );
}

export async function generateMetadata({ params }: PageProps) {
  const { id: rawId } = await params;
  const id = decodeURIComponent(rawId);
  const node = await getNode(id).catch(() => null);
  return {
    title: node ? `${node.name} — PGE` : "Not found — PGE",
  };
}
