/** The simulated device controller: owns the MCP client, the companion REST client, the
 *  status poll and the utterance -> route() -> tools/call -> toView() flow. Kept out of the
 *  component so the wiring reads in one place (and the judge console can reuse invoke()). */
import {
  ADVERSARIAL_VISITS, ApiError, CareLoopApi, highlightIndex, McpClient, McpUnauthorized,
  route, toView,
} from '@careloop/client';
import type { Intent, SampleVisit } from '@careloop/client';
import type { MetricsResponse, ResultView, ToolDef, ToolResult } from '@careloop/client';
import {
  addTurn, lastCompletedTurn, newTurnId, patchTurn, pushTraffic, session, type Turn,
} from './session.svelte';
import { cancelSpeech, setTtsGate, speak } from './speech.svelte';
import { fmtCost, spokenText } from './format';

/** The seeded demo household, shown in the UI so a reviewer can reproduce everything. */
export const DEMO_HOUSEHOLD = { code: 'CASA01', pin: '481542' };

export type AdversarialVisit = (typeof ADVERSARIAL_VISITS)[number];

interface DeviceState {
  booted: boolean;
  bootError: string | null;
  /** /healthz result: true = up, false = down, null = not asked yet. */
  online: boolean | null;
  protocol: string | null;
  sessionId: string | null;
  serverLabel: string;
  tools: ToolDef[];
  metrics: MetricsResponse | null;
  /** Set when the server asked for OAuth (CARELOOP_AUTH=on). */
  needsLinking: boolean;
  busy: boolean;
  /** ref of the item whose card button is in flight. */
  busyRef: string | null;
  /** Honest one-line summary of the last capture, shown above the conversation. */
  captureLine: string | null;
  /** The intent we asked about before the server asked “who is this about?”. */
  pendingIntent: Intent | null;
}

export const device = $state<DeviceState>({
  booted: false,
  bootError: null,
  online: null,
  protocol: null,
  sessionId: null,
  serverLabel: '',
  tools: [],
  metrics: null,
  needsLinking: false,
  busy: false,
  busyRef: null,
  captureLine: null,
  pendingIntent: null,
});

export interface GuardVerdict {
  id: string;
  expect: string;
  outcome: string;
  ok: boolean;
  detail: string;
}

/** Append-only verdict list from the adversarial run. */
export const guardLog = $state<GuardVerdict[]>([]);

let mcp: McpClient | null = null;
let api: CareLoopApi | null = null;
let pollTimer: number | null = null;

export function configureClients(base = ''): void {
  api = new CareLoopApi({ base });
  mcp = new McpClient({
    base,
    protocolVersion: '2025-11-25',
    clientName: 'CareLoop simulator (simulated Alexa+ client)',
    onTraffic: (entry) => pushTraffic(entry),
  });
  setTtsGate(() => session.tts);
}

function needClients(): { mcp: McpClient; api: CareLoopApi } {
  if (!mcp || !api) configureClients();
  if (!mcp || !api) throw new Error('CareLoop clients are not initialised.');
  return { mcp, api };
}

/* ---------------------------------------------------------------------- boot */

export async function boot(): Promise<void> {
  const { mcp: client } = needClients();
  device.bootError = null;
  await refreshStatus();
  try {
    const init = await client.initialize();
    device.protocol = client.negotiated ?? init?.protocolVersion ?? null;
    device.serverLabel = init?.serverInfo
      ? `${init.serverInfo.name} ${init.serverInfo.version}`
      : 'CareLoop MCP server';
    device.tools = await client.listTools();
    device.sessionId = client.sessionId;
    device.booted = true;
  } catch (err) {
    device.bootError = describeError(err);
    if (err instanceof McpUnauthorized) device.needsLinking = true;
  }
  await refreshPeople();
  startPolling();
}

export async function refreshPeople(): Promise<void> {
  let names: string[] = [];
  try {
    if (api) names = ((await api.plan()).people ?? []).map((p) => p.name);
  } catch {
    /* the REST roster wants a signed-in household */
  }
  if (!names.length) {
    // A device still knows whose plan it is reading, so fall back to the transport the voice
    // surface itself uses: the same list_people tool, over the same MCP session.
    try {
      const { mcp: client } = needClients();
      const listed = await client.callTool('list_people', {});
      const people = (listed.structuredContent as { people?: Array<{ name: string }> } | undefined)?.people;
      names = (people ?? []).map((p) => p.name);
    } catch {
      /* people stay unresolved; the router simply will not match names */
    }
  }
  session.people = names;
}

export async function refreshStatus(): Promise<void> {
  if (!api) return;
  try {
    const health = await api.health();
    device.online = Boolean(health.status);
  } catch {
    device.online = false;
  }
  try {
    device.metrics = await api.metrics();
  } catch {
    device.metrics = null;
  }
}

export function startPolling(intervalMs = 5000): void {
  if (pollTimer !== null) return;
  pollTimer = window.setInterval(() => {
    if (session.pollPaused) return;
    void refreshStatus();
  }, intervalMs);
}

export function stopPolling(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

/** Absolute-enough URL for the SSE stream behind `GET /api/events`, from the shared client so
 *  the base/dev-proxy handling is identical to every other request. */
export function planEventsUrl(): string {
  const { api: rest } = needClients();
  return rest.eventsUrl();
}

/** Live-sync refresh: re-read the masked agenda through the exact same voice path
 *  (`route()` -> `tools/call` -> `toView()`) but silently, so the card updates by itself when a
 *  sibling changes the ledger — with no second copy of the render logic and never a dose.
 *  Resolves to the new visible item count (the number shown in the "Plan updated" notice). */
export async function refreshPlanSilently(): Promise<number> {
  if (device.busy) return 0;          // a call is already in flight; the next frame will catch up
  const who = session.people[0];
  const turn = await say(who ? `what is next for ${who}` : 'what is the plan today', {
    replay: true,
    silent: true,
  });
  return turn?.view.items.length ?? 0;
}

/* ------------------------------------------------------------------- errors */

export function describeError(err: unknown): string {
  if (err instanceof McpUnauthorized) return err.message;
  if (err instanceof ApiError) return err.message;
  if (err instanceof TypeError) {
    return 'The CareLoop server is not reachable. Start it with `make serve` and reload this page.';
  }
  return err instanceof Error ? err.message : String(err);
}

function errorView(message: string): ResultView {
  return toView({ error: message }, true);
}

/* ------------------------------------------------------------------ calling */

/** Call a tool and return both the raw result and its render model. */
export async function invoke(
  name: string,
  args: Record<string, unknown>,
): Promise<{ result: ToolResult & { ms: number }; view: ResultView }> {
  const { mcp: client } = needClients();
  const result = await client.callTool(name, args);
  return { result, view: toView(result.structuredContent, Boolean(result.isError)) };
}

export interface SayOptions {
  /** Skip route() and call this tool directly (tool chips, card buttons, replays). */
  tool?: string;
  args?: Record<string, unknown>;
  /** True when the phrase came from a sample-visit replay. */
  replay?: boolean;
  /** Background refresh (live sync): drive the card through the same masked path but never
   *  speak it, so a sibling's change updates the screen silently without stealing the voice. */
  silent?: boolean;
  /** The request's own `params._meta` — how the Device Lab says who is speaking. Omitted, nothing
   *  is claimed and the household is answered exactly as it was before. */
  meta?: Record<string, string> | null;
}

/** The whole flow: utterance -> route() -> tools/call -> toView() -> chat turn + card. */
export async function say(utterance: string, opts: SayOptions = {}): Promise<Turn | null> {
  const said = utterance.trim();
  if (device.busy) return null;
  const { mcp: client } = needClients();
  device.busy = true;

  const intent: Intent = opts.tool
    ? { tool: opts.tool, args: opts.args ?? {}, read: `Called ${opts.tool} directly.` }
    : consumePendingIntent(said);
  device.pendingIntent = null;

  const placeholder: Turn = {
    id: newTurnId(),
    said: said || `(${intent.tool})`,
    read: intent.read,
    tool: intent.tool,
    args: intent.args,
    spoken: '',
    view: toView({}),
    ms: 0,
    at: Date.now(),
    isError: false,
    highlight: -1,
    pending: true,
    replay: Boolean(opts.replay),
  };
  addTurn(placeholder);

  try {
    const result = await client.callTool(intent.tool, intent.args, opts.meta);
    const view = toView(result.structuredContent, Boolean(result.isError));
    const spoken = spokenText(result);
    const highlight = view.items.length ? highlightIndex(said, view.items) : -1;
    // An ask-back keeps the intent, so the next phrase re-runs the same tool with the
    // person the caregiver picks.
    device.pendingIntent = view.kind === 'clarify' ? intent : null;
    device.sessionId = client.sessionId;
    patchTurn(placeholder.id, {
      spoken, view, ms: result.ms, isError: Boolean(result.isError), highlight,
      pending: false, read: intent.read, args: intent.args, tool: intent.tool, raw: result,
    });
    if (!result.isError && session.tts && !opts.silent) speak(spoken);
    void refreshStatus();
    return { ...placeholder, spoken, view, ms: result.ms, highlight, pending: false, raw: result };
  } catch (err) {
    const message = describeError(err);
    patchTurn(placeholder.id, { view: errorView(message), spoken: message, isError: true, pending: false });
    if (err instanceof McpUnauthorized) device.needsLinking = true;
    return null;
  } finally {
    device.busy = false;
  }
}

/** Ask-back handling: re-run the held intent, with a person if this phrase names one. */
function consumePendingIntent(said: string): Intent {
  const fresh = route(said, session.people);
  const held = device.pendingIntent;
  if (!held) return fresh;
  const picked = typeof fresh.args.person === 'string' ? fresh.args.person : null;
  const args: Record<string, unknown> = { ...held.args };
  if (picked) args.person = picked;
  return {
    tool: held.tool,
    args,
    read: `${held.read} — now answered for ${picked ?? 'the whole household'}.`,
  };
}

/** A name chip on the ask-back card: re-run the held intent for that person. */
export async function pickPerson(name: string): Promise<void> {
  const held = device.pendingIntent;
  if (!held) return;
  await say('', { tool: held.tool, args: { ...held.args, person: name } });
}

export async function confirmDelete(ref: string): Promise<void> {
  if (!ref) return;
  device.busyRef = ref;
  await say('', { tool: 'delete_instruction', args: { ref, confirm: true } });
  device.busyRef = null;
}

export async function markDone(ref: string): Promise<void> {
  device.busyRef = ref;
  await say('', { tool: 'mark_done', args: { ref, idempotency_key: `sim-${ref}` } });
  device.busyRef = null;
}

export async function explainItem(ref: string): Promise<void> {
  device.busyRef = ref;
  await say('', { tool: 'explain_instruction', args: { ref } });
  device.busyRef = null;
}

export function cancelAskBack(): void {
  device.pendingIntent = null;
  cancelSpeech();
}

/* ------------------------------------------------------------------- replay */

/** Feed a synthetic visit into the household, then ask what is next.
 *  `askNamedPerson` false leaves the person out, so the server has to ask back. */
export async function runSampleVisit(sample: SampleVisit, askNamedPerson = true): Promise<void> {
  const { api: rest } = needClients();
  if (device.busy) return;
  device.busy = true;
  device.captureLine = `Sending “${sample.title}” to POST /api/capture (the companion side, not the voice side)…`;
  const marker: Turn = {
    id: newTurnId(),
    said: `[replay] ${sample.title}`,
    read: 'POST /api/capture — companion endpoint, deliberately outside the voice surface',
    tool: 'api/capture',
    args: { person: sample.person },
    spoken: '',
    view: toView({}),
    ms: 0,
    at: Date.now(),
    isError: false,
    highlight: -1,
    pending: true,
    replay: true,
    infoOnly: true,
  };
  addTurn(marker);
  try {
    const res = await rest.capture(sample.transcript, sample.person);
    if (res.crisis) {
      const view = toView({ crisis: true, message: res.message });
      patchTurn(marker.id, { spoken: res.message ?? 'Crisis language detected.', pending: false });
      addTurn({
        id: newTurnId(), said: '', read: 'The capture guard took over before anything was stored.',
        tool: 'api/capture', args: {}, spoken: res.message ?? '', view, ms: 0, at: Date.now(),
        isError: false, highlight: -1, pending: false, replay: true,
      });
      device.captureLine = 'Crisis redirect returned; nothing was written to the plan.';
      if (session.tts && res.message) speak(res.message);
      return;
    }
    const summary = `Captured ${res.added ?? 0} item(s) with the ${res.engine ?? 'rule'} engine`
      + `${res.model ? ` (${res.model})` : ''} · cost ${fmtCost(res.cost_usd)} · visit ${res.visit_id ?? '—'}`;
    patchTurn(marker.id, { spoken: summary, pending: false });
    device.captureLine = summary;
    await refreshPeople();
    device.busy = false;              // hand the lock to the voice turn we are about to make
    await say(askNamedPerson ? `what is next for ${sample.person}` : 'what is next?', { replay: true });
  } catch (err) {
    const message = describeError(err);
    patchTurn(marker.id, { spoken: message, isError: true, pending: false });
    device.captureLine = `Capture refused: ${message}`;
  } finally {
    device.busy = false;
  }
}

/** Prove the server-side guards by feeding each adversarial transcript in turn. */
export async function runAdversarialSet(): Promise<void> {
  const { api: rest } = needClients();
  guardLog.length = 0;
  device.busy = true;
  try {
    for (const visit of ADVERSARIAL_VISITS) {
      const verdict: GuardVerdict = {
        id: visit.id, expect: visit.expect, outcome: 'waiting', ok: false, detail: '',
      };
      guardLog.push(verdict);
      try {
        const res = await rest.capture(visit.transcript, 'Papa');
        if (visit.id === 'crisis') {
          verdict.outcome = res.crisis ? 'crisis-redirect' : 'stored-anyway';
          verdict.ok = Boolean(res.crisis) && Boolean(res.message);
          verdict.detail = res.message ?? 'No redirect text came back.';
        } else if (visit.id === 'negation-trap') {
          const kinds = (res.items ?? []).map((i) => i.kind);
          verdict.outcome = kinds.length ? kinds.join(', ') : 'nothing stored';
          verdict.ok = kinds.includes('avoid') && !kinds.includes('medication');
          verdict.detail = `Server classified the negated instruction as: ${kinds.join(', ') || 'nothing'}.`;
        } else {
          verdict.outcome = 'stored-anyway';
          verdict.detail = `${res.added ?? 0} item(s) were added — the guard did not fire.`;
        }
      } catch (err) {
        verdict.outcome = 'rejected at the door';
        verdict.detail = describeError(err);
        verdict.ok = visit.id === 'injection' || visit.id === 'child';
      }
    }
  } finally {
    device.busy = false;
    void refreshStatus();
  }
}

/** The right rail shows the last completed spoken turn (voice/screen parity). */
export function currentCard(): { view: ResultView; highlight: number } | null {
  for (let i = session.turns.length - 1; i >= 0; i--) {
    const turn = session.turns[i];
    if (turn.pending || turn.infoOnly) continue;
    return { view: turn.view, highlight: turn.highlight };
  }
  return null;
}
