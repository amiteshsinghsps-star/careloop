<script lang="ts">
  /** The thing you talk to: a text box, a mic (Web Speech) and spoken playback
   *  (speechSynthesis). Both browser APIs are optional — when one is missing the control
   *  disappears and a note says why, instead of silently doing nothing. */
  import { speech, startListening, stopListening, cancelSpeech } from '../lib/speech.svelte';
  import { session, setTts, tt } from '../lib/session.svelte';

  interface Props {
    busy: boolean;
    disabled: boolean;
    onSay: (text: string) => void;
    /** Set by the parent to drop a phrase into the box (tool chips that need text). */
    prefill?: string;
  }
  let { busy, disabled, onSay, prefill = '' }: Props = $props();

  let draft = $state('');
  let box = $state<HTMLTextAreaElement | null>(null);

  $effect(() => {
    if (!prefill) return;
    draft = prefill;
    box?.focus();
    box?.setSelectionRange(prefill.length, prefill.length);
  });

  const hints = [
    'what is the plan for Papa?',
    "what's next in the morning",
    'mark the first one as done',
    'why is the second item there?',
    'who is in my household?',
    'delete the third item',
    'what should I ask at the next visit?',
    'how am I doing today?',
  ];

  function submit(): void {
    const text = draft.trim();
    if (!text || busy || disabled) return;
    draft = '';
    onSay(text);
  }

  function listen(): void {
    if (speech.listening) {
      stopListening();
      return;
    }
    startListening((heard) => onSay(heard));
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }
</script>

<div class="composer">
  <label class="cl-label" for="utterance">{tt('sayItLabel')}</label>
  <div class="row">
    <textarea id="utterance" class="cl-textarea" rows="2" placeholder="e.g. what did the doctor say for Mama tonight?"
      bind:this={box} bind:value={draft} onkeydown={onKeydown} disabled={disabled || busy}
      aria-describedby="heard-help"></textarea>
    {#if speech.micSupported}
      <button class="mic-btn" type="button" data-live={speech.listening}
        aria-pressed={speech.listening} onclick={listen}
        aria-label={speech.listening ? tt('micStop') : tt('micListen')}>
        {#if speech.listening}
          <svg class="ico" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
          </svg>
        {:else}
          <svg class="ico" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3Z" fill="currentColor" />
            <path d="M5 11a7 7 0 0 0 14 0M12 18v3" stroke="currentColor" stroke-width="2" fill="none" />
          </svg>
        {/if}
      </button>
    {/if}
  </div>

  {#if speech.listening || speech.heard}
    <p class="cl-note">
      {#if speech.listening}Listening… {speech.heard || '(say something)'}{:else}Heard: “{speech.heard}”{/if}
    </p>
  {/if}
  {#if speech.lastError}
    <p class="cl-callout bad">{speech.lastError}</p>
  {/if}

  <div class="u-flex">
    <button class="cl-btn" type="button" onclick={submit} disabled={disabled || busy || !draft.trim()}>
      {busy ? tt('workingButton') : tt('sendButton')}
    </button>
    {#if speech.speaking}
      <button class="cl-btn ghost" type="button" onclick={cancelSpeech}>Stop speaking</button>
    {/if}
  </div>

  <div class="toggles">
    {#if speech.ttsSupported}
      <label class="cl-check">
        <input type="checkbox" checked={session.tts} onchange={(e) => setTts(e.currentTarget.checked)} />
        <span>Speak the answer aloud (uses the tool’s own sentence, nothing else)</span>
      </label>
    {:else}
      <p class="cl-note">{speech.ttsReason}</p>
    {/if}
    {#if !speech.micSupported}
      <p class="cl-note">{speech.micReason}</p>
    {/if}
  </div>

  <div>
    <p class="cl-label" id="heard-help">{tt('tryThese')}</p>
    <div class="chips">
      {#each hints as hint}
        <button class="chip" type="button" disabled={busy || disabled} onclick={() => onSay(hint)}>{hint}</button>
      {/each}
    </div>
  </div>
</div>

<style>
  .ico { width: 22px; height: 22px; display: block; }
</style>
