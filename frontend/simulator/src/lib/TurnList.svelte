<script lang="ts">
  /** The conversation column. Each turn prints exactly what the tool returned — the same
   *  string the speaker reads — plus the render model behind it. */
  import type { CardActions } from './types';
  import PlanCard from './PlanCard.svelte';
  import type { Turn } from './session.svelte';

  interface Props {
    turns: Turn[];
    busy: boolean;
    busyRef: string | null;
    people: string[];
    actions: CardActions;
  }
  let { turns, busy, busyRef, people, actions }: Props = $props();

  const lastId = $derived(turns.length ? turns[turns.length - 1].id : '');

  /** Kept in the script so the template only prints what the call actually carried. */
  function argSummary(turn: Turn): string {
    const keys = Object.keys(turn.args);
    return keys.length ? JSON.stringify(turn.args) : '';
  }
</script>

<ol class="turns" aria-live="polite" aria-relevant="additions">
  {#each turns as turn}
    <li class="turn" class:err={turn.isError} class:info={turn.infoOnly}>
      {#if turn.said && !turn.infoOnly && !turn.said.startsWith('(')}
        <div class="turn-said">
          <p class="bubble">{turn.said}</p>
        </div>
      {/if}

      <p class="route-read" class:pending={turn.pending}>
        {#if turn.pending}
          Calling <strong>{turn.tool}</strong>…
        {:else}
          Router (our deterministic stand-in for Alexa’s own model, not Alexa’s model): {turn.read}
          · <code>{turn.tool}</code>
          {#if argSummary(turn)}{argSummary(turn)}{/if}
          {#if turn.ms}· {turn.ms} ms{/if}
          {#if turn.isError}· <span class="cl-pill bad">error</span>{/if}
        {/if}
      </p>

      {#if turn.infoOnly}
        <p class="cl-callout">{turn.spoken || '…'}</p>
      {:else if !turn.pending}
        <p class="bubble spoken">{turn.spoken}</p>
        <PlanCard view={turn.view} dense={true} {busyRef} {people}
          chrome={turn.id === lastId}
          actions={turn.view.kind === 'confirm-delete' || turn.view.kind === 'clarify' ? actions : {}} />
      {/if}
    </li>
  {/each}
  {#if !turns.length}
    <li class="cl-note">Nothing said yet. Ask a question, tap a tool chip, or replay a sample visit.</li>
  {/if}
</ol>

{#if busy}
  <p class="cl-note" role="status">Waiting for the server…</p>
{/if}

<style>
  .turns { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--cl-sp-5); }
  .turn { display: grid; gap: var(--cl-sp-2); }
  .turn-said { display: flex; justify-content: flex-end; }
  .turn .bubble {
    border-radius: var(--cl-r-lg); border: 1px solid var(--cl-line); background: var(--cl-surface);
    padding: var(--cl-sp-3) var(--cl-sp-4); margin: 0; max-width: 100%;
  }
  .turn-said .bubble { background: var(--cl-accent-soft); border-color: transparent; }
  .turn.err .bubble.spoken { background: var(--cl-danger-soft); border-color: var(--cl-danger); }
  .route-read { margin: 0; font-size: var(--cl-text-xs); color: var(--cl-ink-3); }
  .route-read.pending { color: var(--cl-info); }
  .turn.info { opacity: .92; }
  .spoken { white-space: pre-wrap; }
</style>
