<script lang="ts">
  /** Live status line: is the server up, which MCP protocol version was negotiated,
   *  the session id, how many tools it published and the worst p95 we are serving. */
  import { device } from '../lib/device.svelte';
  import { session, tt } from '../lib/session.svelte';
  import { errorTotal, fmtMs, worstP95 } from '../lib/format';

  interface Props {
    onOpenTraffic: () => void;
  }
  let { onOpenTraffic }: Props = $props();

  const p95 = $derived(worstP95(device.metrics));
  const slo = $derived(device.metrics ? device.metrics.slo_p95_under_500ms : null);
  const errors = $derived(errorTotal(device.metrics?.counts.errors));
  const requests = $derived(device.metrics?.counts.requests ?? 0);
  const uptime = $derived(device.metrics ? Math.round(device.metrics.uptime_s) : null);
  const trafficCount = $derived(session.traffic.length);

  const onlineLabel = $derived(device.online === null ? 'checking' : device.online ? 'up' : 'down');
</script>

<div class="statusbar" role="status" aria-live="polite">
  <span class="cell">
    {tt('serverLabel')} <b>{onlineLabel}</b>
    {#if device.online === false}
      <span class="cl-pill bad">start it with `make run`</span>
    {:else if device.online}
      <span class="cl-pill done">/healthz</span>
    {/if}
  </span>
  <span class="cell">MCP <b>{device.protocol ?? '—'}</b></span>
  <span class="cell">Session <code class="mono-xs">{device.sessionId ?? '—'}</code></span>
  <span class="cell">{tt('toolsLabel')} <b>{device.tools.length}</b></span>
  <span class="cell">p95 <b>{fmtMs(p95)}</b>{#if slo !== null} {#if slo}<span class="cl-pill done">SLO&nbsp;met</span>{:else}<span class="cl-pill bad">over 500&nbsp;ms</span>{/if}{/if}</span>
  <span class="cell">Calls <b>{requests}</b> · errors <b>{errors}</b></span>
  {#if uptime !== null}<span class="cell">up <b>{uptime}s</b></span>{/if}

  <span class="u-flex" style="margin-left:auto">
    <button class="cl-btn sm quiet" type="button" aria-pressed={session.pollPaused}
      onclick={() => (session.pollPaused = !session.pollPaused)}>
      {session.pollPaused ? tt('resumePolling') : tt('pausePolling')}
    </button>
    <button class="cl-btn sm ghost" type="button" onclick={onOpenTraffic}
      aria-haspopup="dialog" aria-expanded={session.trafficOpen}>
      {tt('trafficButton', { count: trafficCount })}
    </button>
  </span>
</div>
