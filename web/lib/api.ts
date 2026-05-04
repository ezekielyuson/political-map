// Thin fetch wrapper around the PGE API. All functions throw on non-2xx so
// callers don't have to thread error checks through their UI logic.
//
// API base comes from NEXT_PUBLIC_PGE_API_URL (set in Vercel). Falls back
// to localhost for `next dev`.

import type {
  EdgeView,
  HealthResponse,
  MapConnectionsResponse,
  MapPoliticiansResponse,
  NodeListResponse,
  NodeView,
  PathsView,
  Subgraph,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_PGE_API_URL ?? "http://localhost:8000";

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function jsonFetch<T>(path: string): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, `${res.status} ${url}: ${body.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

export async function getHealth(): Promise<HealthResponse> {
  return jsonFetch<HealthResponse>("/health");
}

export async function searchNodes(opts: {
  q?: string;
  kind?: string;
  limit?: number;
  offset?: number;
}): Promise<NodeListResponse> {
  const params = new URLSearchParams();
  if (opts.q) params.set("q", opts.q);
  if (opts.kind) params.set("kind", opts.kind);
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.offset !== undefined) params.set("offset", String(opts.offset));
  const qs = params.toString();
  return jsonFetch<NodeListResponse>(`/nodes${qs ? "?" + qs : ""}`);
}

export async function getNode(id: string): Promise<NodeView | null> {
  try {
    return await jsonFetch<NodeView>(`/nodes/${encodeURIComponent(id)}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export async function getNeighbors(
  id: string,
  opts: { depth?: number; edgeKind?: string; nodeKind?: string } = {},
): Promise<Subgraph | null> {
  const params = new URLSearchParams();
  if (opts.depth !== undefined) params.set("depth", String(opts.depth));
  if (opts.edgeKind) params.set("edge_kind", opts.edgeKind);
  if (opts.nodeKind) params.set("node_kind", opts.nodeKind);
  const qs = params.toString();
  try {
    return await jsonFetch<Subgraph>(
      `/nodes/${encodeURIComponent(id)}/neighbors${qs ? "?" + qs : ""}`,
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export async function findPaths(opts: {
  from: string;
  to: string;
  maxDepth?: number;
  maxPaths?: number;
}): Promise<PathsView> {
  const params = new URLSearchParams({
    from: opts.from,
    to: opts.to,
  });
  if (opts.maxDepth !== undefined) params.set("max_depth", String(opts.maxDepth));
  if (opts.maxPaths !== undefined) params.set("max_paths", String(opts.maxPaths));
  return jsonFetch<PathsView>(`/paths?${params.toString()}`);
}

// Convenience: build an index of edges and nodes so renderers don't have to
// scan arrays repeatedly.
export function indexSubgraph(sg: { nodes: NodeView[]; edges: EdgeView[] }) {
  const nodesById = new Map<string, NodeView>(sg.nodes.map((n) => [n.id, n]));
  const edgesById = new Map<string, EdgeView>(sg.edges.map((e) => [e.id, e]));
  return { nodesById, edgesById };
}

export async function getMapPoliticians(opts: {
  chamber?: string;
  party?: string;
} = {}): Promise<MapPoliticiansResponse> {
  const params = new URLSearchParams();
  if (opts.chamber) params.set("chamber", opts.chamber);
  if (opts.party) params.set("party", opts.party);
  const qs = params.toString();
  return jsonFetch<MapPoliticiansResponse>(`/map/politicians${qs ? "?" + qs : ""}`);
}

export async function getMapConnections(
  id: string,
): Promise<MapConnectionsResponse | null> {
  try {
    return await jsonFetch<MapConnectionsResponse>(
      `/map/connections/${encodeURIComponent(id)}`,
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export { ApiError };
