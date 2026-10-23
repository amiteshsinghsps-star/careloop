import { Injectable, computed, signal } from '@angular/core';
import { ApiError, CareLoopApi } from '@careloop/client';
import type { CaptureResponse, CareItem, ExportResponse, LedgerResult, LedgerSnapshot, PlanResponse, Visit } from '@careloop/client';

export type Person = PlanResponse['people'][number];
export type DeleteSummary = { ref: string; person: string; kind: string; quote: string };

/** The companion always talks to its own origin. `ng serve` proxies /api, /mcp, /oauth,
 *  /metrics and the health endpoints (proxy.conf.json), and `npm run dist` mounts the built
 *  app on the Python server that serves it. Inferring a port instead sent every request to a
 *  hard-coded :8080 the moment the server ran anywhere else — and the page's own CSP
 *  (`connect-src 'self'`) was right to refuse the cross-port call. */
const API_BASE = '';

@Injectable({ providedIn: 'root' })
export class SessionService {
  readonly api = new CareLoopApi({ base: API_BASE });

  readonly signedIn = signal(false);
  readonly checked = signal(false);
  readonly people = signal<Person[]>([]);
  readonly items = signal<CareItem[]>([]);
  readonly visits = signal<Visit[]>([]);
  readonly ledger = signal<LedgerResult | null>(null);
  readonly fullDosesVisible = signal(false);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly toast = signal<string | null>(null);

  readonly itemTotal = computed(() => this.items().length);
  readonly pendingCount = computed(() => this.items().filter((i) => i.status === 'pending').length);

  private toastTimer: ReturnType<typeof setTimeout> | null = null;

  /** Restore the session on load: there is no /api/me, so probe with plan() and read the 401. */
  async ensureSession(): Promise<boolean> {
    if (this.checked()) return this.signedIn();
    await this.refresh();
    this.checked.set(true);
    return this.signedIn();
  }

  async signIn(code: string, pin: string): Promise<boolean> {
    const res = await this.run(() => this.api.login(code, pin));
    if (!res) return false;
    this.signedIn.set(true);
    this.checked.set(true);
    await this.refresh();
    return true;
  }

  async signUp(code: string, pin: string, name: string): Promise<boolean> {
    const res = await this.run(() => this.api.createHousehold(code, pin, name));
    if (!res) return false;
    this.signedIn.set(true);
    this.checked.set(true);
    this.showToast(res.message || 'Household ready.');
    await this.refresh();
    return true;
  }

  async signOut(): Promise<void> {
    await this.run(() => this.api.logout());
    this.signedIn.set(false);
    this.checked.set(true);
    this.items.set([]);
    this.visits.set([]);
    this.people.set([]);
    this.ledger.set(null);
    this.fullDosesVisible.set(false);
  }

  async refresh(person?: string | null): Promise<void> {
    const res = await this.run(() => this.api.plan(person ?? null));
    if (!res) return;
    this.items.set(res.items);
    this.visits.set(res.visits);
    this.people.set(res.people);
    this.ledger.set(res.ledger);
    this.fullDosesVisible.set(res.full_doses_visible);
    this.signedIn.set(true);
    this.checked.set(true);
  }

  async capture(transcript: string, person?: string): Promise<CaptureResponse | null> {
    return this.run(() => this.api.capture(transcript, person));
  }

  /** Optimistic-concurrency mark: send the item's version + a stable idempotency key. */
  async mark(item: CareItem, who?: string): Promise<void> {
    const idem = `${item.ref}:${item.version}`;
    this.busy.set(true);
    this.error.set(null);
    try {
      await this.api.mark(item.ref, who, item.version, idem);
      await this.refresh();
    } catch (e) {
      this.handleError(e);
    } finally {
      this.busy.set(false);
    }
  }

  async confirmItem(ref: string): Promise<boolean> {
    const res = await this.run(() => this.api.confirm(ref));
    if (res) await this.refresh();
    return !!res;
  }

  async previewDelete(ref: string): Promise<DeleteSummary | null> {
    const res = await this.run(() => this.api.remove(ref, false));
    if (!res || !res.needs_confirmation || !res.summary) return null;
    const s = res.summary as Partial<DeleteSummary>;
    return { ref: s.ref ?? ref, person: s.person ?? '', kind: s.kind ?? '', quote: s.quote ?? '' };
  }

  async deleteItem(ref: string): Promise<boolean> {
    const res = await this.run(() => this.api.remove(ref, true));
    if (res) await this.refresh();
    return !!res;
  }

  async addPerson(name: string, role: string, aliases: string[]): Promise<boolean> {
    const res = await this.run(() => this.api.addPerson(name, role, aliases));
    if (res) await this.refresh();
    return !!res;
  }

  async exportSheet(person?: string | null): Promise<ExportResponse | null> {
    return this.run(() => this.api.export(person ?? null));
  }

  /** Time-travel read of `GET /api/ledger`. Deliberately NOT routed through `api.ledger()`: the
   *  shared GET helper throws away the response body, so a rejected date would surface as a
   *  generic "Request failed (400)" instead of the server's real, plain-language message.
   *  `until` is an epoch-seconds string or an ISO date, or null for "now". Never touches or
   *  un-masks any dose text — it returns whatever the server replayed, verbatim. */
  async ledgerAt(until: string | null):
    Promise<{ snapshot?: LedgerSnapshot; error?: string; status?: number }> {
    const qs = until ? `?until=${encodeURIComponent(until)}` : '';
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/api/ledger${qs}`, {
        headers: { Accept: 'application/json' },
        credentials: 'include',
      });
    } catch {
      return { error: 'Could not reach CareLoop. Check the connection and try again.' };
    }
    let data: any = {};
    try { data = await res.json(); } catch { /* empty body */ }
    if (!res.ok) {
      // The server's error is already plain language — show it verbatim, never invent one.
      return { error: String(data?.error ?? `Request failed (${res.status})`), status: res.status };
    }
    return { snapshot: data as LedgerSnapshot };
  }

  async eraseAll(): Promise<Record<string, unknown> | null> {
    const res = await this.run(() => this.api.erase(true));
    if (res) {
      await this.refresh();
      this.showToast('Everything was erased for this household.');
    }
    return res;
  }

  setError(message: string | null): void {
    this.error.set(message);
  }

  clearError(): void {
    this.error.set(null);
  }

  showToast(message: string): void {
    this.toast.set(message);
    if (this.toastTimer) clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => this.toast.set(null), 6000);
  }

  dismissToast(): void {
    this.toast.set(null);
    if (this.toastTimer) clearTimeout(this.toastTimer);
  }

  private async run<T>(fn: () => Promise<T>): Promise<T | null> {
    this.busy.set(true);
    this.error.set(null);
    try {
      return await fn();
    } catch (e) {
      this.handleError(e);
      return null;
    } finally {
      this.busy.set(false);
    }
  }

  private handleError(e: unknown): void {
    if (e instanceof ApiError) {
      if (e.status === 401) {
        this.signedIn.set(false);
        this.checked.set(true);
      }
      if (e.status === 409 && e.message === 'version-conflict') {
        this.showToast('Someone else just changed this — reloading.');
        void this.refresh();
        return;
      }
      // Messages from the server are already plain-language — show verbatim, never ids/stacks.
      this.error.set(e.message);
      return;
    }
    this.error.set('Could not reach CareLoop. Check the connection and try again.');
  }
}
