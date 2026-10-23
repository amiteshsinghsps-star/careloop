<script lang="ts">
  /** Honest live-sync indicator. It says whether the plan is being followed live, the current
   *  server version + timestamp (verbatim server fields), a polite "Plan updated · N items"
   *  notice when a sibling changes the plan, and a warning if the audit chain stops verifying.
   *  Every visible string is a closed-vocabulary label via `tt()` or a value from the server —
   *  never model prose, never a dose. */
  import { live } from './live.svelte';
  import { tt } from './session.svelte';

  const statusLabel = $derived.by(() => {
    switch (live.status) {
      case 'live': return tt('liveStatusLive');
      case 'connecting': return tt('liveStatusConnecting');
      case 'polling': return tt('liveStatusPolling');
      case 'stalled': return tt('liveStatusStalled');
      default: return tt('liveStatusOff');
    }
  });

  const pill = $derived(
    live.status === 'live' ? 'done'
      : live.status === 'polling' || live.status === 'stalled' ? 'warn'
        : 'info',
  );
</script>

<div class="livebadge" role="status" aria-live="polite">
  <span class="dot {pill}" aria-hidden="true"></span>
  <span class="cl-pill {pill}">{statusLabel}</span>

  {#if live.version !== null}
    <span class="cl-note">v{live.version}{#if live.at} · {live.at}{/if}</span>
  {/if}

  {#if live.updated}
    <span class="cl-note">{tt('planUpdated', { count: live.items })}</span>
  {/if}

  {#if live.verified === false}
    <span class="cl-note warn">{tt('ledgerUnverified')}</span>
  {/if}
</div>

<style>
  .livebadge { display: flex; flex-wrap: wrap; align-items: center; gap: var(--cl-sp-3); }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--cl-ink-3); flex: none; }
  .dot.done { background: var(--cl-done); }
  .dot.warn { background: var(--cl-warn); }
  .dot.info { background: var(--cl-info); }
</style>
