<script lang="ts">
  /** Renders one ResultView — the single model produced by toView(). The same component
   *  draws the simulator's chat turns, its right-rail card and the MCP App card, so the
   *  screen can never show something the voice did not say. Doses are never invented or
   *  expanded here: we print exactly the masked text the server sent. */
  import { kindLabel, ordinal, ROLE_LABEL, STATUS_LABEL } from '@careloop/ui';
  import type { MaskedItem, ResultView } from '@careloop/client';
  import type { CardActions, ItemRow } from './types';

  interface Props {
    view: ResultView;
    /** Item the spoken phrase pointed at (highlightIndex), or -1. */
    highlight?: number;
    /** Chat-turn variant: tighter, smaller type. */
    dense?: boolean;
    actions?: CardActions;
    /** ref currently being acted on, so its buttons disable. */
    busyRef?: string | null;
    /** Show attribution / disclaimer / dose policy. Default true. */
    chrome?: boolean;
    /** Large-text styling for an Alexa screen. */
    screen?: boolean;
    /** Household members offered as chips when the server asks "who is this about?". */
    people?: string[];
  }

  let {
    view,
    highlight = -1,
    dense = false,
    actions = {},
    busyRef = null,
    chrome = true,
    screen = false,
    people = [],
  }: Props = $props();

  const items = $derived(view.items ?? []);
  const raw = $derived(view.raw ?? {});
  const confirmRef = $derived(
    (raw.summary as { ref?: string } | undefined)?.ref ?? (raw.ref as string | undefined) ?? '',
  );
  /* The note is already printed inside the error / crisis / done / deleted / plan blocks;
     only prep and digest need it as a separate line. */
  const noteAlone = $derived(!!view.note && (view.kind === 'prep' || view.kind === 'digest'));

  /* toView() only keeps `note` when the payload carries message/count_hint, so the counts
     the server sent are read straight from the raw structuredContent here. Nothing is
     invented: every number below came from this tool response. */
  const errorText = $derived(String(raw.error ?? view.note ?? 'Something went wrong.'));
  const doneText = $derived(
    view.note
    ?? (typeof raw.remaining_count === 'number' ? `${raw.remaining_count} still to do.` : 'marked as complete'),
  );
  const diffText = $derived.by(() => {
    if (view.kind !== 'diff') return '';
    const count = (key: string) => (Array.isArray(raw[key]) ? (raw[key] as unknown[]).length : 0);
    const compared = typeof raw.compared_to === 'string' ? raw.compared_to : 'the earlier visit';
    return `${count('new')} new · ${count('stopped')} stopped · ${count('changed')} changed · compared with ${compared}.`;
  });
  const prepText = $derived.by(() => {
    if (view.kind !== 'prep') return '';
    const count = (key: string) => (Array.isArray(raw[key]) ? (raw[key] as unknown[]).length : 0);
    return `${count('skipped')} replaced items and ${count('notes')} family notes are in this household’s log.`;
  });

  /** Agenda rows keep the flat item index, so the highlight matches the spoken ordinal. */
  const agenda = $derived.by<{ part_of_day: string; entries: ItemRow[]; empty: boolean }[]>(() => {
    let offset = 0;
    return (view.slots ?? []).map((slot) => {
      const entries = slot.items.map((item) => ({ item, index: offset++ }));
      return { part_of_day: slot.part_of_day, entries, empty: slot.items.length === 0 };
    });
  });

  function statusLabel(status: string): string {
    return STATUS_LABEL[status] ?? status;
  }

  function pillFor(item: MaskedItem): { cls: string; label: string } {
    if (item.needs_confirm && item.status === 'pending') return { cls: 'warn', label: 'Check with the clinician' };
    if (item.status === 'done') return { cls: 'done', label: statusLabel(item.status) };
    if (item.status !== 'pending') return { cls: 'info', label: statusLabel(item.status) };
    return { cls: 'ok', label: statusLabel(item.status) };
  }
</script>

<article class="card" class:dense class:screen data-kind={view.kind}>
  <header class="card-head">
    <h3>{view.title}</h3>
    {#if view.person}
      <span class="cl-pill info">{view.person}</span>
    {/if}
  </header>

  {#if view.kind === 'error'}
    <p class="cl-callout bad">{errorText}</p>
    {#if actions.say}
      <div class="card-actions">
        <button class="cl-btn sm ghost" type="button" onclick={() => actions.say?.('what is the plan?')}>Try again</button>
      </div>
    {/if}

  {:else if view.kind === 'crisis'}
    <div class="cl-callout crisis" role="alert">
      <h4>Thank you for telling me</h4>
      <p class="redirect">{view.note ?? 'If anyone is in danger right now, please call your local emergency number.'}</p>
      <p class="cl-note">Nothing from this conversation was saved to the care plan, and no one else sees it.</p>
    </div>

  {:else if view.kind === 'clarify'}
    <p class="question">{view.question ?? 'Who is this about?'}</p>
    {#if actions.pickPerson && people.length}
      <div class="chips">
        {#each people as name}
          <button class="chip" type="button" onclick={() => actions.pickPerson?.(name)}>{name}</button>
        {/each}
      </div>
    {/if}

  {:else if view.kind === 'confirm-delete'}
    <p class="question">{view.question}</p>
    {#if actions.confirmDelete}
      <div class="card-actions">
        <button class="cl-btn danger" type="button" disabled={busyRef === confirmRef}
          onclick={() => actions.confirmDelete?.(confirmRef)}>Yes, delete</button>
        <button class="cl-btn ghost" type="button" onclick={() => actions.cancel?.()}>No, keep it</button>
      </div>
    {/if}

  {:else if view.kind === 'deleted'}
    <p class="cl-callout">That item is off the plan. Earlier versions stay in your household log, and the clinician’s note is not erased.</p>

  {:else if view.kind === 'done'}
    <p class="cl-callout soft">Done — {doneText}</p>

  {:else if view.kind === 'about'}
    <ul class="cl-list">
      {#each view.capabilities ?? [] as cap}
        <li>{cap}</li>
      {/each}
    </ul>
    {#if (view.examples ?? []).length}
      <p class="cl-label">Things you can say</p>
      <ul class="cl-list examples">
        {#each view.examples ?? [] as ex}
          <li>{#if actions.say}
            <button class="chip chip-wide" type="button" onclick={() => actions.say?.(ex)}>{ex}</button>
          {:else}
            “{ex}”
          {/if}</li>
        {/each}
      </ul>
    {/if}

  {:else if view.kind === 'people'}
    <table class="cl-table">
      <thead><tr><th scope="col">Name</th><th scope="col">Role</th><th scope="col">To do</th></tr></thead>
      <tbody>
        {#each view.people ?? [] as p}
          <tr><td>{p.name}</td><td>{ROLE_LABEL[p.role] ?? p.role}</td><td>{p.pending_count}</td></tr>
        {/each}
        {#if !(view.people ?? []).length}
          <tr><td class="u-mute" colspan="3">No one is recorded in this household yet.</td></tr>
        {/if}
      </tbody>
    </table>

  {:else if view.kind === 'digest'}
    <div class="digest">
      <div class="cl-metric"><span class="v">{view.numbers?.done ?? 0}</span><span class="k">Done today</span></div>
      <div class="cl-metric"><span class="v">{view.numbers?.pending ?? 0}</span><span class="k">Still to do</span></div>
      <div class="cl-metric">
        <span class="v">{view.numbers?.last_done_at ?? '—'}</span>
        <span class="k">Last marked done</span>
      </div>
    </div>

  {:else if view.kind === 'prep'}
    {#if items.length}
      <ol class="cl-list numbered">
        {#each items as flag, i}
          <li><span class="ordinal">question {i + 1}</span><br />{flag.text}</li>
        {/each}
      </ol>
    {:else}
      <p class="cl-note">Nothing was flagged to ask about — the plan is up to date.</p>
    {/if}
    {#if prepText}<p class="u-mute">{prepText}</p>{/if}

  {:else if view.kind === 'quote'}
    <blockquote class="cl-quote">
      “{view.quote}”
      <span class="src">Recorded {view.recordedOn ?? 'on the visit'} · the clinician’s own words, doses masked on this screen.</span>
    </blockquote>

  {:else if view.kind === 'agenda'}
    {#each agenda as slot}
      <section class="slot">
        <p class="cl-label">{slot.part_of_day}</p>
        {#if slot.empty}
          <p class="cl-note">Nothing at this time.</p>
        {:else}
          <div class="u-stack">
            {#each slot.entries as entry}
              {@render itemRow(entry.item, entry.index)}
            {/each}
          </div>
        {/if}
      </section>
    {/each}

  {:else}
    <!-- plan / added / diff -->
    {#if diffText}<p class="u-mute">{diffText}</p>{/if}
    {#if items.length}
      <div class="u-stack">
        {#each items as item, i}
          {@render itemRow(item, i)}
        {/each}
      </div>
    {:else}
      <p class="cl-note">{view.note ?? 'Nothing pending in the plan we have for this household.'}</p>
    {/if}
  {/if}

  {#if noteAlone}
    <p class="u-mute">{view.note}</p>
  {/if}

  {#if chrome}
    <footer class="card-foot">
      {#if view.attribution}<p class="attribution">{view.attribution}</p>{/if}
      {#if view.dosePolicy}<p class="cl-note">{view.dosePolicy}</p>{/if}
      {#if view.disclaimer}<p class="cl-disclaimer">{view.disclaimer}</p>{/if}
    </footer>
  {/if}
</article>

{#snippet itemRow(item: MaskedItem, index: number)}
  <div class="cl-item" data-kind={item.kind} data-hl={highlight === index ? 'true' : undefined}
    aria-current={highlight === index ? 'true' : undefined}>
    <div class="meta">
      <span class="ordinal">{ordinal(index)}</span>
      <span class="cl-pill" data-kind={item.kind}>{kindLabel(item.kind)}</span>
      <span class="cl-pill {pillFor(item).cls}">{pillFor(item).label}</span>
    </div>
    <p class="who">{item.text}</p>
    {#if item.schedule}<p class="u-mute sched">{item.schedule}</p>{/if}
    {#if actions.done || actions.explain || actions.say}
      <div class="card-actions">
        {#if actions.done && item.status === 'pending'}
          <button class="cl-btn sm" type="button" disabled={busyRef === item.ref}
            onclick={() => actions.done?.(item.ref)}>Mark done</button>
        {/if}
        {#if actions.explain}
          <button class="cl-btn sm ghost" type="button" disabled={busyRef === item.ref}
            onclick={() => actions.explain?.(item.ref)}>Why is this here?</button>
        {/if}
        {#if actions.say && item.ref}
          <button class="cl-btn sm quiet" type="button" disabled={busyRef === item.ref}
            onclick={() => actions.say?.(`delete the ${ordinal(index)} item`)}>Delete</button>
        {/if}
      </div>
    {/if}
  </div>
{/snippet}

<style>
  .card { display: grid; gap: var(--cl-sp-4); }
  .card.dense { gap: var(--cl-sp-3); }
  .card.screen { font-size: var(--cl-text-lg); }
  .card-head { display: flex; align-items: center; justify-content: space-between; gap: var(--cl-sp-3); flex-wrap: wrap; }
  .card-head h3 { margin: 0; }
  .ordinal { font-size: var(--cl-text-xs); text-transform: uppercase; letter-spacing: .06em; color: var(--cl-ink-3); }
  .who { margin: 0; }
  .sched { margin: 0; font-size: var(--cl-text-sm); }
  .card-actions { display: flex; flex-wrap: wrap; gap: var(--cl-sp-2); }
  .card-foot { display: grid; gap: var(--cl-sp-2); border-top: 1px solid var(--cl-line); padding-top: var(--cl-sp-3); }
  .attribution { margin: 0; font-weight: 600; font-size: var(--cl-text-sm); color: var(--cl-ink-2); }
  .card-foot p { margin: 0; }
  .question { margin: 0; font-size: var(--cl-text-lg); font-weight: 600; }
  .crisis { display: grid; gap: var(--cl-sp-2); border: 2px solid var(--cl-danger); }
  .crisis h4 { margin: 0; font-size: var(--cl-text-xl); }
  .crisis p { margin: 0; }
  .redirect { font-size: var(--cl-text-lg); font-weight: 600; }
  .digest { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--cl-sp-4); }
  .examples li { list-style: none; }
  .numbered li { list-style: none; }
  .slot + .slot { margin-top: var(--cl-sp-4); }
  :global(.cl-item[data-hl="true"]) { border-color: var(--cl-accent); box-shadow: 0 0 0 3px var(--cl-accent-soft); }
  :global(.cl-callout.soft) { background: var(--cl-done-soft); border-color: transparent; }
  :global(.chip-wide) { width: 100%; text-align: left; font-family: var(--cl-font); }
</style>
