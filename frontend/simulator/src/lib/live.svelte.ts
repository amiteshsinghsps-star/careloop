/** Reactive holder around the SSE plan stream. It owns the visible state (connection status,
 *  current version, whether the audit chain verifies, the "plan updated" notice) and delegates
 *  all stream mechanics to the framework-free `openPlanStream`, whose reducer stays the single
 *  source of truth for "did the plan change".
 *
 *  Nothing here fabricates health text: every string a caregiver sees is a closed-vocabulary
 *  label (via `tt`) or a value the server sent (version, timestamp, masked item count). */
import { openPlanStream, type PlanStreamHandle, type StreamStatus } from './planstream';
import { planEventsUrl, refreshPlanSilently, startPolling } from './device.svelte';

const NOTICE_MS = 8000;

export const live = $state({
  status: 'off' as StreamStatus | 'off',
  /** Latest version seen from the server, or null before the first frame. */
  version: null as number | null,
  /** Server ISO timestamp of the last change (verbatim server field, never reformatted here). */
  at: '',
  /** The server's `verified` flag: false = the audit chain no longer verifies. */
  verified: null as boolean | null,
  /** Visible masked item count from the last refresh. */
  items: 0,
  /** True while the "Plan updated" notice is showing (auto-clears). */
  updated: false,
});

let handle: PlanStreamHandle | null = null;
let noticeTimer: number | null = null;

function flashUpdated(): void {
  live.updated = true;
  if (noticeTimer !== null) window.clearTimeout(noticeTimer);
  noticeTimer = window.setTimeout(() => { live.updated = false; noticeTimer = null; }, NOTICE_MS);
}

/** Connect to the household's SSE stream. Safe to call once after boot; call `stopLive` on
 *  unmount / sign-out / household switch so no stream leaks. */
export function startLive(): void {
  stopLive();
  handle = openPlanStream({
    url: planEventsUrl(),
    refresh: () => refreshPlanSilently(),
    onFallback: () => startPolling(),
    onStatus: (status) => { live.status = status; },
    onVersion: (info) => {
      live.version = info.version;
      if (info.at) live.at = info.at;
      live.verified = info.verified === undefined ? null : info.verified;
      if (info.changed && typeof info.items === 'number') {
        live.items = info.items;
        flashUpdated();
      }
    },
  });
}

/** Close the stream and reset to the honest idle state. Idempotent. */
export function stopLive(): void {
  if (handle) {
    handle.close();
    handle = null;
  }
  if (noticeTimer !== null) {
    window.clearTimeout(noticeTimer);
    noticeTimer = null;
  }
  live.status = 'off';
  live.updated = false;
}
