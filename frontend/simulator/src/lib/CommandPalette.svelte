<script lang="ts">
  /** Ctrl/Cmd+K command palette (M8). A WAI-ARIA combobox: focus stays on the input and the
   *  active row is announced with aria-activedescendant, so screen-reader behaviour matches a
   *  native listbox. It only ever runs actions you could already do by voice or tap, and each
   *  row shows the equivalent utterance. The keyboard contract + fuzzy matching live in
   *  frontend/client/src/palette.ts (unit-tested here); this component owns focus and ARIA. */
  import { clampActive, filterPalette, moveActive, paletteKeyAction, type PaletteCommand } from '@careloop/client';
  import { tt } from '../lib/session.svelte';

  /** A palette entry plus what it actually does on this screen. */
  export interface Command extends PaletteCommand {
    run: () => void;
  }

  interface Props {
    open: boolean;
    commands: Command[];
    onClose: () => void;
  }
  let { open, commands, onClose }: Props = $props();

  let query = $state('');
  let active = $state(0);
  let input = $state<HTMLInputElement | null>(null);
  // Plain (non-reactive): only the open transition reads/writes it, so keeping it out of the
  // rune graph avoids the effect re-running (and re-focusing) on every keystroke.
  let restoreFocus: Element | null = null;

  const results = $derived(filterPalette(commands, query));
  const listId = 'cl-palette-list';
  const optionId = (i: number): string => `${listId}-opt-${i}`;
  const activeId = $derived(active >= 0 && active < results.length ? optionId(active) : '');

  // On open: remember where focus was, focus the input, reset the query. On close: hand focus
  // back — no focus trap exists while the palette is closed.
  $effect(() => {
    if (open) {
      restoreFocus = document.activeElement;
      query = '';
      active = 0; // the clamp effect resolves -1 for an empty list; avoids reading `results`
      requestAnimationFrame(() => input?.focus());
    } else if (restoreFocus instanceof HTMLElement) {
      const el = restoreFocus;
      restoreFocus = null;
      el.focus();
    }
  });

  // Keep the highlight valid as the filtered list shrinks/grows with typing.
  $effect(() => {
    void query;
    active = clampActive(active, results.length);
  });

  function choose(index: number): void {
    const cmd = results[index];
    if (!cmd) return;
    onClose();
    cmd.run();
  }

  function onKeydown(event: KeyboardEvent): void {
    const action = paletteKeyAction(event.key);
    if (action === 'ignore') {
      // Trap Tab inside the dialog while it is open (the input is the only focusable control
      // besides the close button); everything else is left to the browser.
      if (event.key === 'Tab') event.preventDefault();
      return;
    }
    event.preventDefault();
    if (action === 'close') {
      onClose();
      return;
    }
    if (action === 'select') {
      choose(active);
      return;
    }
    const delta = action === 'down' ? 1 : -1;
    active = moveActive(active, delta, results.length);
  }
</script>

{#if open}
  <div class="cl-palette-scrim">
    <!-- Dismissal is a real button behind the dialog, not a click handler on a div: mouse users
         still click the backdrop, keyboard users get Tab + Enter, and Escape closes the dialog. -->
    <button class="cl-palette-backdrop" type="button" aria-label={tt('closeButton')} onclick={onClose}></button>
    <div class="cl-palette" role="dialog" aria-modal="true" aria-label={tt('paletteLabel')} tabindex="-1">
      <input class="cl-palette-input" type="text" role="combobox" bind:this={input}
        aria-expanded="true" aria-controls={listId} aria-activedescendant={activeId}
        aria-autocomplete="list" autocomplete="off" spellcheck="false"
        placeholder={tt('palettePlaceholder')} aria-label={tt('paletteLabel')}
        bind:value={query} onkeydown={onKeydown} />

    <ul class="cl-palette-list" id={listId} role="listbox"
        aria-label={tt('paletteLabel')} tabindex="-1">
      {#each results as cmd, i}
        <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
        <li class="cl-palette-opt" id={optionId(i)} role="option" aria-selected={i === active}
          data-active={i === active} onclick={() => choose(i)}>
          <span class="opt-label">{cmd.label}</span>
          {#if cmd.utterance}
            <span class="opt-utt">“{cmd.utterance}”</span>
          {:else}
            <span class="opt-utt screenonly">No spoken equivalent — screen action</span>
          {/if}
        </li>
      {:else}
        <li class="cl-palette-opt empty" role="option" aria-selected="false">Nothing matches.</li>
      {/each}
    </ul>

    <div class="cl-palette-foot">
      <p class="cl-note">{tt('paletteHint')}</p>
      <button class="cl-btn sm ghost" type="button" onclick={onClose}>{tt('closeButton')}</button>
    </div>
  </div>
  </div>
{/if}

<style>
  .cl-palette-scrim {
    position: fixed; inset: 0; z-index: 60;
    background: color-mix(in srgb, var(--cl-ink) 45%, transparent);
    display: grid; place-items: start center; padding-top: 8vh;
  }
  .cl-palette-backdrop {
    position: absolute; inset: 0; width: 100%; height: 100%;
    border: 0; padding: 0; background: transparent; cursor: pointer;
  }
  .cl-palette {
    position: relative;
    width: min(560px, 92vw); background: var(--cl-bg); border: 1px solid var(--cl-line);
    border-radius: var(--cl-r-lg); box-shadow: var(--cl-shadow);
    display: grid; gap: var(--cl-sp-2); padding: var(--cl-sp-3);
  }
  .cl-palette-input {
    width: 100%; min-height: var(--cl-target); font: inherit; color: var(--cl-ink);
    background: var(--cl-surface); border: 1px solid var(--cl-line);
    border-radius: var(--cl-r); padding: 0 var(--cl-sp-3);
  }
  .cl-palette-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; max-height: 46vh; overflow: auto; }
  .cl-palette-opt {
    min-height: var(--cl-target); display: grid; align-content: center; gap: 2px;
    padding: 0 var(--cl-sp-3); border-radius: var(--cl-r); cursor: pointer;
    border: 1px solid transparent;
  }
  .cl-palette-opt[data-active='true'] { background: var(--cl-accent-soft); border-color: var(--cl-accent); }
  .opt-label { font-weight: 600; color: var(--cl-ink); }
  .opt-utt { color: var(--cl-ink-2); font-size: .9em; }
  .opt-utt.screenonly { font-style: italic; }
  .cl-palette-opt.empty { color: var(--cl-ink-2); cursor: default; }
  .cl-palette-foot { display: flex; align-items: center; justify-content: space-between; gap: var(--cl-sp-3); }
</style>
