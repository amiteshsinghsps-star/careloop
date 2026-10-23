import type {
  CaptureResponse, CareItem, ExportResponse, JudgeData, LedgerSnapshot, MetricsResponse,
  PlanResponse,
} from './types';

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

export interface ApiOptions {
  /** Base URL of the CareLoop server. Empty string = same origin (production, dev proxy). */
  base?: string;
  signal?: AbortSignal;
}

async function post<T>(base: string, path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(base + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body ?? {}),
    signal,
  });
  let data: any = {};
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok || data?.error) {
    throw new ApiError(String(data?.error ?? `Request failed (${res.status})`), res.status);
  }
  return data as T;
}

async function get<T>(base: string, path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(base + path, { headers: { Accept: 'application/json' }, credentials: 'include', signal });
  if (!res.ok) throw new ApiError(`Request failed (${res.status})`, res.status);
  return (await res.json()) as T;
}

/** REST surface of the companion ("CareLoop Home") API. Reads are POSTs server-side. */
export class CareLoopApi {
  private base: string;

  constructor(opts: ApiOptions = {}) {
    this.base = (opts.base ?? '').replace(/\/$/, '');
  }

  health(): Promise<{ status: string }> { return get(this.base, '/healthz'); }

  ready(): Promise<{ status: string; auth: string }> { return get(this.base, '/readyz'); }

  metrics(): Promise<MetricsResponse> { return get(this.base, '/metrics'); }

  judgeData(): Promise<JudgeData> { return get(this.base, '/api/judge-data'); }

  login(code: string, pin: string): Promise<{ ok: boolean }> {
    return post(this.base, '/api/login', { code: code.toUpperCase(), pin });
  }

  createHousehold(code: string, pin: string, name: string): Promise<{ household_code: string; message: string }> {
    return post(this.base, '/api/create-household', { code: code.toUpperCase(), pin, name });
  }

  logout(): Promise<{ ok: boolean }> { return post(this.base, '/api/logout', {}); }

  plan(person?: string | null): Promise<PlanResponse> {
    return post(this.base, '/api/plan', { person: person ?? null });
  }

  capture(transcript: string, person?: string): Promise<CaptureResponse> {
    return post(this.base, '/api/capture', { transcript, person: person ?? '' });
  }

  mark(ref: string, who?: string, version?: number, idem?: string): Promise<Partial<CareItem> & { replayed?: boolean; error?: string }> {
    return post(this.base, '/api/mark', { ref, who, version, idem });
  }

  confirm(ref: string): Promise<{ ref: string; needs_confirm: boolean }> {
    return post(this.base, '/api/confirm', { ref });
  }

  remove(ref: string, confirm: boolean): Promise<{ deleted?: boolean; needs_confirmation?: boolean; summary?: unknown }> {
    return post(this.base, '/api/delete', { ref, confirm });
  }

  addPerson(name: string, role = 'elder', aliases: string[] = []): Promise<{ name: string }> {
    return post(this.base, '/api/add-person', { name, role, aliases });
  }

  export(person?: string | null): Promise<ExportResponse> {
    return post(this.base, '/api/export', { person: person ?? null });
  }

  /** `GET /api/ledger?until=` — companion-authenticated history snapshot. */
  ledger(until: string, signal?: AbortSignal): Promise<LedgerSnapshot> {
    return get<LedgerSnapshot>(this.base, `/api/ledger?until=${encodeURIComponent(until)}`, signal);
  }

  /** Absolute-enough URL for the `EventSource` behind `/api/events`. The SSE stream lives
   *  on the same origin (or the dev base), and the companion cookie is sent automatically. */
  eventsUrl(): string {
    return `${this.base}/api/events`;
  }

  erase(confirm: boolean): Promise<{ receipt?: string; erased?: boolean; [k: string]: unknown }> {
    return post(this.base, '/api/erase', { confirm });
  }
}
