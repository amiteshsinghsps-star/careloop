/** The card page's two data paths, kept out of the component.
 *
 *  1. MCP Apps bridge — an App-capable host (or our own simulator) talks JSON-RPC through
 *     postMessage. We answer requests on `event.source` and send our own `tools/call`
 *     requests back the same way.
 *  2. Direct fallback — a same-origin `POST /mcp` to the CareLoop server, used when no host
 *     is present (the file opened on its own, or `?tool=get_care_plan`).
 *
 *  Nothing is ever `eval`ed, and message bodies are only read as plain JSON objects. */
import { McpClient } from '@careloop/client';
import type { ToolResult } from '@careloop/client';

export type PayloadSource = 'host-request' | 'host-meta' | 'simulator' | 'query' | 'none';

export interface CardPayload {
  /** structuredContent (or a whole ToolResult — both are accepted). */
  data: Record<string, unknown>;
  spoken: string;
  source: PayloadSource;
}

interface HostTarget {
  source: MessageEventSource | null;
  origin: string;
}

const RELAXED_ORIGINS = /^(null|http:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?|https:\/\/(localhost|127\.0\.0\.1)(:\d+)?)$/;

type Listener = (event: MessageEvent) => void;

export class CardBridge {
  onPayload: ((payload: CardPayload) => void) | null = null;
  onLog: ((line: string) => void) | null = null;
  /** 'bridge' once a host has been heard from; otherwise 'direct'. */
  mode: 'bridge' | 'direct' = 'direct';

  private host: HostTarget | null = null;
  private seq = 0;
  private pending = new Map<string, {
    resolve: (result: ToolResult) => void;
    reject: (err: Error) => void;
    timer: number;
  }>();
  private client: McpClient | null = null;
  private directReady: Promise<void> | null = null;
  private listener: Listener | null = null;

  attach(): void {
    if (typeof window === 'undefined' || this.listener) return;
    this.listener = (event) => this.handle(event);
    window.addEventListener('message', this.listener);
  }

  detach(): void {
    if (this.listener && typeof window !== 'undefined') {
      window.removeEventListener('message', this.listener);
    }
    this.listener = null;
    this.pending.forEach((p) => window.clearTimeout(p.timer));
    this.pending.clear();
  }

  private trusted(origin: string): boolean {
    if (origin === window.location.origin) return true;
    if (RELAXED_ORIGINS.test(origin)) return true;
    return false;
  }

  private handle(event: MessageEvent): void {
    if (typeof event.origin === 'string' && !this.trusted(event.origin)) {
      this.onLog?.(`Ignored a postMessage from unlisted origin “${event.origin || '(blank)'}”.`);
      return;
    }
    const data = event.data;
    if (!data || typeof data !== 'object' || Array.isArray(data)) return;
    const msg = data as Record<string, any>;

    // (a) a response to a tools/call we sent to the host
    if (msg.jsonrpc === '2.0' && msg.id !== undefined && (msg.result !== undefined || msg.error !== undefined)) {
      const key = String(msg.id);
      const waiting = this.pending.get(key);
      if (!waiting) return;
      this.pending.delete(key);
      window.clearTimeout(waiting.timer);
      if (msg.error) waiting.reject(new Error(String(msg.error.message ?? 'Host returned an error')));
      else waiting.resolve(msg.result as ToolResult);
      return;
    }

    // (b) the host asking this card to perform a call: answer on event.source
    if (msg.jsonrpc === '2.0' && msg.method === 'tools/call' && typeof msg.params?.name === 'string') {
      this.host = { source: event.source, origin: event.origin || '*' };
      this.mode = 'bridge';
      const name = msg.params.name as string;
      const args = (msg.params.arguments ?? {}) as Record<string, unknown>;
      void this.runDirect(name, args)
        .then((result) => this.reply({ jsonrpc: '2.0', id: msg.id, result }))
        .catch((err: Error) => this.reply({
          jsonrpc: '2.0', id: msg.id, error: { code: -32603, message: err.message },
        }));
      return;
    }

    // (c) an initial _meta.ui payload (MCP Apps "here is what to render")
    const metaUi = msg._meta?.ui ?? msg.params?._meta?.ui ?? msg.meta?.ui;
    if (metaUi && typeof metaUi === 'object') {
      this.host = { source: event.source, origin: event.origin || '*' };
      this.mode = 'bridge';
      const structured = metaUi.structuredContent ?? metaUi.data ?? metaUi;
      this.emit(structured, String(metaUi.text ?? ''), 'host-meta');
      return;
    }

    // (d) our own simulator's fallback envelope
    if (msg.type === 'careloop:card') {
      const payload = msg.payload ?? {};
      this.emit(payload.structuredContent ?? payload, String(payload.spoken ?? payload.text ?? ''), 'simulator');
      return;
    }

    this.onLog?.('Received a message this card does not understand; nothing was rendered from it.');
  }

  private emit(structured: unknown, spoken: string, source: PayloadSource): void {
    if (!structured || typeof structured !== 'object') {
      this.onLog?.('A payload arrived without structuredContent, so the card kept the last plan.');
      return;
    }
    this.onPayload?.({ data: structured as Record<string, unknown>, spoken, source });
  }

  private reply(envelope: unknown): void {
    const target = this.host;
    if (!target?.source) return;
    const useOrigin = target.origin && target.origin !== 'null' ? target.origin : '*';
    /* Only the already-masked card payload and its JSON-RPC id leave this frame, and the
       origin echoed is the one that sent the request; an opaque (srcdoc) host needs '*'. */
    (target.source as Window).postMessage(envelope, useOrigin);
  }

  /** Send a tools/call through whichever path is active. */
  async callTool(name: string, args: Record<string, unknown>): Promise<ToolResult> {
    if (this.mode === 'bridge' && this.host?.source) {
      try {
        return await this.callHost(name, args);
      } catch (err) {
        this.onLog?.(`The host did not answer tools/call (${(err as Error).message}); calling the server directly.`);
      }
    }
    return this.runDirect(name, args);
  }

  private callHost(name: string, args: Record<string, unknown>): Promise<ToolResult> {
    const id = `card-${Date.now().toString(36)}-${this.seq++}`;
    const envelope = { jsonrpc: '2.0', id, method: 'tools/call', params: { name, arguments: args } };
    return new Promise<ToolResult>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('no reply within 2500 ms'));
      }, 2500);
      this.pending.set(id, { resolve, reject, timer });
      this.reply(envelope);
    });
  }

  /** Same-origin JSON-RPC to the CareLoop server. The server runs with CARELOOP_AUTH=off for
   *  demos, so no bearer token is needed; with auth on this returns the linking message. */
  private async runDirect(name: string, args: Record<string, unknown>): Promise<ToolResult> {
    const client = this.directClient();
    if (!this.directReady) {
      this.directReady = client.initialize().then(() => undefined).catch((err: Error) => {
        this.directReady = null;
        throw err;
      });
    }
    await this.directReady;
    return client.callTool(name, args);
  }

  private directClient(): McpClient {
    if (!this.client) this.client = new McpClient({ clientName: 'CareLoop MCP App card' });
    return this.client;
  }

  /** `?tool=get_care_plan` bootstrap, when the page is opened standalone. */
  async loadFromQuery(): Promise<boolean> {
    const params = new URLSearchParams(window.location.search);
    const tool = params.get('tool');
    if (!tool || !/^[a-z0-9_]{1,40}$/.test(tool)) return false;
    const args: Record<string, unknown> = {};
    const person = params.get('person');
    const ref = params.get('ref');
    const limit = params.get('limit');
    if (person) args.person = person;
    if (ref) args.ref = ref;
    if (limit && /^\d{1,2}$/.test(limit)) args.limit = Number(limit);
    try {
      const result = await this.runDirect(tool, args);
      const structured = result.structuredContent ?? {};
      const spoken = result.content?.find((c) => c.type === 'text')?.text ?? '';
      this.emit(structured, spoken, 'query');
      return true;
    } catch (err) {
      this.onLog?.(`Could not load ?tool=${tool}: ${(err as Error).message}`);
      return false;
    }
  }

  /** window.__CARELOOP_CARD__ set by the embedding page before/after load. */
  readGlobalPayload(): boolean {
    const holder = window as unknown as { __CARELOOP_CARD__?: unknown };
    const raw = holder.__CARELOOP_CARD__;
    if (!raw || typeof raw !== 'object') return false;
    const payload = raw as Record<string, unknown>;
    this.emit(payload.structuredContent ?? payload, String(payload.spoken ?? ''), 'simulator');
    return true;
  }
}
