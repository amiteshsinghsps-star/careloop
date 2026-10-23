export interface Schedule {
  kind?: 'RECURRENCE' | 'WEEKLY' | 'INTERVAL' | 'ONCE' | 'UNKNOWN';
  day_parts?: string[];
  times_per_day?: number | null;
  every?: { n: number; unit: string } | null;
  weekdays?: number[];
  once_on?: string | null;
  raw?: string;
  tz_note?: string;
}

/** An item as returned by the companion API — includes the unmasked quote and dose text. */
export interface CareItem {
  ref: string;
  person: string;
  kind: string;
  status: string;
  text: string;
  quote: string;
  qstart?: number | null;
  qend?: number | null;
  schedule: Schedule;
  confidence?: number;
  needs_confirm: boolean;
  version: number;
  source: string;
  created_at: number;
  done_at?: number | null;
}

/** An item as returned on the Alexa surface — text and quote are dose-masked. */
export interface MaskedItem {
  ref: string;
  kind: string;
  text: string;
  schedule: string;
  status: string;
  needs_confirm: boolean;
}

export interface Visit {
  id: string;
  person: string;
  hash: string;
  at: number;
}

export interface LedgerResult {
  ok: boolean;
  events?: number;
  checked?: number;
  broken_at?: number | null;
  head?: string;
  /** Why the chain failed, in plain words: 'content hash mismatch (tampering?)' and friends. */
  reason?: string;
  detail?: string;
}

export interface PlanResponse {
  items: CareItem[];
  visits: Visit[];
  people: { name: string; role: string; aliases?: string[] }[];
  ledger: LedgerResult;
  full_doses_visible: boolean;
}

export interface CaptureResponse {
  visit_id?: string;
  engine?: string;
  model?: string;
  added?: number;
  notes?: string[];
  cost_usd?: number;
  items?: CareItem[];
  crisis?: boolean;
  message?: string;
  error?: string;
}

export interface Percentiles {
  p50: number | null;
  p95: number | null;
  p99: number | null;
  n: number;
}

export interface JudgeData {
  protocol: string[];
  manifest_hash: string;
  guest_safe: string[];
  tools: string[];
  metrics: {
    requests: number;
    errors: Record<string, number>;
    latency: Record<string, Percentiles>;
    cost_usd: number;
    escalations: number;
  };
  ledger: Record<string, LedgerResult>;
  chaos: string[];
  chaos_unknown: string[];
  policy_self_test: {
    dose_leak_after_mask: number;
    crisis_recall_demo: boolean;
    child_block_demo: boolean;
    injection_block_demo: boolean;
    masked_examples: string[];
  };
}

export interface MetricsResponse {
  counts: { requests: number; errors: Record<string, number> };
  latency_ms: Record<string, Percentiles>;
  slo_p95_under_500ms: boolean;
  uptime_s: number;
}

export interface ExportResponse {
  markdown: string;
  ics: string[];
  error?: string;
}

/* ------------------------------------------------------------------ live + ledger */

/** One replayed row of `GET /api/ledger` — the audit chain up to the moment asked. */
export interface LedgerHistoryRow {
  seq: number;
  /** Seconds since the epoch, exactly as the server's ledger stores them. */
  at: number;
  type: string;
  person?: string;
  kind?: string;
  ref?: string;
  status?: string;
}

/** An item of the plan as it stood at that moment. */
export interface LedgerItemSnapshot {
  ref: string;
  person: string | null;
  kind: string;
  status: string;
  text: string;
  quote: string;
  qstart?: number | null;
  qend?: number | null;
  version: number;
  needs_confirm: boolean;
  confidence?: number | null;
  created_at?: number | null;
}

/** The answer to "what was the plan then?", rebuilt by replaying the chain. */
export interface LedgerSnapshot {
  as_of: string;
  requested_until: number | null;
  verified: boolean;
  /** Chain length at the moment asked (not the chain as it stands now). */
  events: number;
  chain_events: number;
  history: LedgerHistoryRow[];
  state: { items: LedgerItemSnapshot[] };
  error?: string;
}

/* ------------------------------------------------------------------ MCP types */

export interface ToolDef {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  outputSchema?: Record<string, unknown>;
  annotations?: Record<string, unknown>;
}

export interface ToolResult {
  content?: { type: string; text: string }[];
  structuredContent?: Record<string, unknown>;
  isError?: boolean;
  _meta?: Record<string, unknown>;
}

export interface McpRpcMessage {
  jsonrpc: '2.0';
  id?: string | number;
  method?: string;
  params?: unknown;
  result?: unknown;
  error?: { code: number; message: string };
}

export interface TrafficEntry {
  at: number;
  direction: 'out' | 'in';
  method: string;
  ms?: number;
  status?: number;
  raw: string;
}
