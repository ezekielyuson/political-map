// Mirror of pge.graph.queries Pydantic models. Keep in sync when the API
// schema changes. We intentionally don't generate these from OpenAPI -- the
// surface is small enough that hand-typed beats a build step.

export interface NodeView {
  id: string;
  kind: string;
  name: string;
  attrs: Record<string, unknown>;
}

export interface EdgeView {
  id: string;
  kind: string;
  src_id: string;
  dst_id: string;
  evidence_type: "VERIFIED" | "REPORTED" | "INFERRED";
  source_name: string;
  source_id: string;
  as_of_date: string | null;
  strength: string | null;
  confidence: string | null;
  attrs: Record<string, unknown>;
}

export interface Subgraph {
  nodes: NodeView[];
  edges: EdgeView[];
}

export interface PathHop {
  edge_id: string;
  from_node: string;
  to_node: string;
}

export interface PathsView {
  paths: PathHop[][];
  nodes: NodeView[];
  edges: EdgeView[];
}

export interface NodeListResponse {
  nodes: NodeView[];
  limit: number;
  offset: number;
}

export interface HealthResponse {
  ok: boolean;
  nodes_total: number;
  edges_total: number;
  nodes_by_kind: Record<string, number>;
  edges_by_kind: Record<string, number>;
  edges_by_evidence: Record<string, number>;
}

// What a node "is" determines how we render it. Keep this in sync with the
// NodeKind literal in src/pge/schema/nodes.py.
export const NODE_KINDS = [
  "Politician",
  "PoliticalParty",
  "GovernmentBody",
  "Company",
  "PAC",
  "LobbyingFirm",
  "Individual",
  "Bill",
] as const;
export type NodeKind = (typeof NODE_KINDS)[number];
