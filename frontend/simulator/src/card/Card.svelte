<script lang="ts">
  /** The MCP App card. It renders one thing: a masked care plan (or whatever tool answer
   *  the host sent). It never shows a dose, because the server masked it before this file
   *  ever saw the data, and it never fetches anything except same-origin /mcp. */
  import { toView } from '@careloop/client';
  import type { ResultView } from '@careloop/client';
  import PlanCard from '../lib/PlanCard.svelte';
  import type { CardActions } from '../lib/types';
  import { CardBridge } from './bridge';

  const bridge = new CardBridge();

  let view = $state<ResultView | null>(null);
  let spoken = $state('');
  let status = $state('Waiting for the care plan…');
  let busyRef = $state<string | null>(null);
  let logLines = $state<string[]>([]);
  let from = $state('none');
  let mode = $state<'bridge' | 'direct'>('direct');
  const debug = typeof window !== 'undefined'
    && new URLSearchParams(window.location.search).has('debug');

  $effect(() => {
    bridge.onLog = (line) => {
      status = line;
      mode = bridge.mode;
      logLines = [line, ...logLines].slice(0, 12);
    };
    bridge.onPayload = (payload) => {
      view = toView(payload.data);
      spoken = payload.spoken || '';
      from = payload.source;
      mode = bridge.mode;
      status = payload.data.error
        ? 'The server reported a problem; nothing was changed.'
        : 'Care plan on screen.';
    };
    bridge.attach();
    if (!bridge.readGlobalPayload()) {
      void bridge.loadFromQuery().then((loaded) => {
        if (!loaded && !view) status = 'No plan was sent to this card yet.';
      });
    }
    return () => bridge.detach();
  });

  async function run(name: string, args: Record<string, unknown>, label: string): Promise<void> {
    busyRef = String(args.ref ?? name);
    status = label;
    mode = bridge.mode;
    try {
      const result = await bridge.callTool(name, args);
      const next = toView(result.structuredContent, Boolean(result.isError));
      view = next;
      spoken = result.content?.find((c) => c.type === 'text')?.text ?? '';
      status = next.kind === 'error' ? (next.note ?? 'That did not work.') : `${name} answered.`;
    } catch (err) {
      status = err instanceof Error ? err.message : 'The call failed.';
    } finally {
      busyRef = null;
    }
  }

  const actions: CardActions = {
    done: (ref) => void run('mark_done', { ref, idempotency_key: `card-${ref}` }, 'Marking it done…'),
    explain: (ref) => void run('explain_instruction', { ref }, 'Fetching the clinician’s words…'),
  };
</script>

<div class="card-page">
  <header class="card-bar">
    <span class="cl-pill info">CareLoop card</span>
    <span class="u-tiny u-mute">{mode === 'bridge' ? 'hosted by an MCP App host' : 'talking to the server directly'}</span>
    {#if view?.person}<span class="cl-pill ok">{view.person}</span>{/if}
    {#if from !== 'none'}<span class="u-tiny u-mute">via {from}</span>{/if}
  </header>

  <main>
    {#if view}
      <PlanCard {view} {actions} busyRef={busyRef} screen={true} chrome={true} />
    {:else}
      <p class="cl-quote empty">No care plan has been sent to this card yet. The device asks
        <code>get_care_plan</code> and the answer arrives here.</p>
    {/if}

    {#if spoken}
      <p class="cl-note spoken"><span class="cl-label">Spoken on the device</span>“{spoken}”</p>
    {/if}
  </main>

  <p class="cl-note status" role="status" aria-live="polite">{status}</p>

  {#if debug}
    <section class="debug">
      <p class="cl-label">Card log</p>
      <ul class="cl-list u-tiny">
        {#each logLines as line}<li>{line}</li>{/each}
      </ul>
    </section>
  {/if}
</div>

<style>
  .card-page {
    display: grid; gap: var(--cl-sp-4); padding: var(--cl-sp-4);
    min-height: 100dvh; align-content: start;
    font-size: var(--cl-text-lg);
  }
  .card-bar { display: flex; flex-wrap: wrap; gap: var(--cl-sp-2); align-items: center; }
  main { display: grid; gap: var(--cl-sp-4); }
  .empty { margin: 0; }
  .spoken { border-top: 1px dashed var(--cl-line); padding-top: var(--cl-sp-3); }
  .status { min-height: var(--cl-target); }
  .debug { border-top: 1px solid var(--cl-line); }
</style>
