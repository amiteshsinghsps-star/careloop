import type { McpRpcMessage, ToolDef, ToolResult, TrafficEntry } from './types';

export interface McpOptions {
  base?: string;
  /** Bearer token. Omit when the server runs with CARELOOP_AUTH=off (dev identity). */
  token?: string | null;
  protocolVersion?: string;
  clientName?: string;
  onTraffic?: (entry: TrafficEntry) => void;
  fetchImpl?: typeof fetch;
}

/**
 * Minimal MCP Streamable-HTTP client that behaves like the documented Alexa+ client:
 * string request ids, `MCP-Session-Id` carried forward, `MCP-Protocol-Version` on every
 * initialised request, JSON (not SSE) responses, Bearer token in the header only.
 */
export class McpClient {
  private seq = 0;
  private base: string;
  readonly protocolVersion: string;
  readonly clientName: string;
  private token: string | null;
  sessionId: string | null = null;
  serverInfo: { name: string; version: string } | null = null;
  negotiated: string | null = null;
  private onTraffic?: (e: TrafficEntry) => void;
  private fetchImpl: typeof fetch;

  constructor(opts: McpOptions = {}) {
    this.base = (opts.base ?? '').replace(/\/$/, '');
    this.token = opts.token ?? null;
    this.protocolVersion = opts.protocolVersion ?? '2025-11-25';
    this.clientName = opts.clientName ?? 'Alexa+ MCP Client';
    this.onTraffic = opts.onTraffic;
    // `fetch` is a Window operation, not a free function: holding the reference and calling it
    // later throws "Illegal invocation" in a browser, while Node's fetch happily accepts any
    // receiver — so every unit test passed and the simulator could never reach /mcp. Bind it.
    this.fetchImpl = opts.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  setToken(token: string | null): void { this.token = token; }

  private headers(): Record<string, string> {
    const h: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
    };
    if (this.token) h.Authorization = `Bearer ${this.token}`;
    if (this.sessionId) h['MCP-Session-Id'] = this.sessionId;
    if (this.negotiated) h['MCP-Protocol-Version'] = this.negotiated;
    return h;
  }

  private emit(direction: 'out' | 'in', method: string, raw: string, ms?: number, status?: number): void {
    this.onTraffic?.({ at: Date.now(), direction, method, raw, ms, status });
  }

  private async rpc(method: string, params?: unknown): Promise<any> {
    const id = `cl-${Date.now().toString(36)}-${this.seq++}`;
    const body: McpRpcMessage = { jsonrpc: '2.0', id, method, params };
    const t0 = performance.now();
    this.emit('out', method, JSON.stringify(body, null, 2));
    const res = await this.fetchImpl(`${this.base}/mcp`, {
      method: 'POST', headers: this.headers(), body: JSON.stringify(body),
    });
    const ms = Math.round(performance.now() - t0);
    const text = await res.text();
    this.emit('in', method, pretty(text), ms, res.status);
    if (res.status === 401) {
      throw new McpUnauthorized(res.headers.get('WWW-Authenticate'));
    }
    let payload: any;
    try { payload = JSON.parse(firstJsonObject(text)); }
    catch { throw new McpError(`Server returned a response we could not read (HTTP ${res.status}).`); }
    if (payload.error) throw new McpError(payload.error.message ?? 'Unknown protocol error');
    const sid = res.headers.get('MCP-Session-Id');
    if (sid) this.sessionId = sid;
    return payload.result;
  }

  async initialize(): Promise<{ protocolVersion: string; serverInfo: any; capabilities: any }> {
    const result = await this.rpc('initialize', {
      protocolVersion: this.protocolVersion,
      clientInfo: { name: this.clientName, version: '1.0' },
      capabilities: { roots: { listChanged: true } },
    });
    this.negotiated = result?.protocolVersion ?? this.protocolVersion;
    this.serverInfo = result?.serverInfo ?? null;
    await this.notify('notifications/initialized');
    return result;
  }

  async notify(method: string, params?: unknown): Promise<void> {
    const body: McpRpcMessage = { jsonrpc: '2.0', method, params };
    this.emit('out', method, JSON.stringify(body, null, 2));
    const res = await this.fetchImpl(`${this.base}/mcp`, {
      method: 'POST', headers: this.headers(), body: JSON.stringify(body),
    });
    this.emit('in', method, `HTTP ${res.status} (no body)`, undefined, res.status);
  }

  async listTools(): Promise<ToolDef[]> {
    const r = await this.rpc('tools/list', {});
    return (r?.tools ?? []) as ToolDef[];
  }

  /** `meta` is the request's own `params._meta` — the transport the speaker assertion rides,
   *  because who is asking is a fact about the request, not an argument a tool declares. Omit it
   *  and the body is exactly as it was. */
  async callTool(
    name: string,
    args: Record<string, unknown> = {},
    meta?: Record<string, string> | null,
  ): Promise<ToolResult & { ms: number }> {
    const t0 = performance.now();
    const params: Record<string, unknown> = { name, arguments: args };
    if (meta && Object.keys(meta).length) params._meta = meta;
    const r = await this.rpc('tools/call', params);
    return { ...(r as ToolResult), ms: Math.round(performance.now() - t0) };
  }

  async listResources(): Promise<any[]> {
    const r = await this.rpc('resources/list', {});
    return r?.resources ?? [];
  }

  async readResource(uri: string): Promise<{ uri: string; mimeType: string; text: string } | null> {
    const r = await this.rpc('resources/read', { uri });
    return r?.contents?.[0] ?? null;
  }

  async ping(): Promise<boolean> {
    await this.rpc('ping', {});
    return true;
  }
}

export class McpError extends Error {}

export class McpUnauthorized extends McpError {
  constructor(readonly challenge: string | null) {
    super('The household account needs to be linked before this can be answered.');
  }
}

function firstJsonObject(text: string): string {
  const start = text.indexOf('{');
  if (start < 0) throw new Error('no json');
  // tolerate an SSE-framed body ("data: {...}")
  if (text.startsWith('event:') || text.startsWith('data:')) {
    const line = text.split('\n').find((l) => l.startsWith('data:'));
    if (line) return line.slice(5).trim();
  }
  return text;
}

function pretty(text: string): string {
  try { return JSON.stringify(JSON.parse(firstJsonObject(text)), null, 2); }
  catch { return text || '(empty)'; }
}
