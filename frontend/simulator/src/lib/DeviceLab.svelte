<script lang="ts">
  /** Device Lab — the simulated Alexa+ runtime, turn by turn.
   *
   *  Every row is one exchange: what was said, how the shipped interaction model resolved it, what
   *  the real server answered over JSON-RPC, and the response envelope the device would have been
   *  handed (typed against Amazon's own `ask-sdk-model`). It is a simulator, not Alexa hardware,
   *  and the panel says so. */
  import { onMount } from 'svelte';
  import {
    clearLab, downloadTrace, lab, labSay, machine, modelArgsFor, runScenario, SCENARIOS, stopClock,
  } from './alexa/deviceRuntime.svelte';
  import { SIM_MAX_REPROMPTS } from './alexa/machine';
  import { DELIVERY, INTERACTION_MODEL, VOICE_BUDGET, VOICE_PROFILE } from './alexa/interactionModel';
  import { session } from './session.svelte';
  import { fmtMs } from './format';

  let phrase = $state('what is next');
  let openTurn = $state<string | null>(null);
  const stateLabel: Record<string, string> = {
    idle: 'Standby', wake: 'Wake word', listening: 'Listening', processing: 'Working',
    speaking: 'Speaking', awaiting: 'Open window', reprompting: 'Asking again',
    interrupted: 'Interrupted', closed: 'Session ended',
  };

  onMount(() => () => stopClock());

  const turns = $derived(lab.turns);
  const events = $derived(machine.events);
  const latest = $derived(turns.length ? turns[turns.length - 1] : null);
  const problems = $derived(turns.filter((t) => t.ask?.problems.length));
  // Turns the model held back from a write. A read-back does call the server, and writes nothing:
  // it is the answer that asks, not the request that changes the plan.
  const held = $derived(turns.filter((t) => t.dialog));
  const heldLine = $derived(held.length
    ? `Dialog delegation held ${held.length} turn(s) back: `
      + `${held.filter((t) => t.dialog?.stage === 'elicit').length} asked for a slot, `
      + `${held.filter((t) => t.dialog?.stage === 'confirm').length} asked for a yes, `
      + `${held.filter((t) => t.dialog?.stage === 'refused').length} refused — nothing was written on any of them.`
    : '');
  const deviceState = $derived(stateLabel[machine.state] ?? machine.state);
  const speed = $derived(lab.speed);
  // Refusals the service wrote, not decisions the device made: counted from the turn rows so the
  // panel can say which requests were bounded and where the sentence came from.
  const refusedTurns = $derived(turns.filter((t) => t.refused !== null));

  function send(): void {
    const text = phrase.trim();
    if (!text) return;
    void labSay(text);
  }

  function pick(scenarioId: string): void {
    const scenario = SCENARIOS.find((s) => s.id === scenarioId);
    if (scenario) void runScenario(scenario);
  }

  function slotPill(slotState: string): string {
    return slotState === 'no-match' ? 'bad' : slotState === 'success' ? 'ok' : 'warn';
  }
</script>

<section class="cl-panel lab" aria-labelledby="lab-h">
  <div class="u-between head">
    <div>
      <h2 id="lab-h" style="margin:0">Device Lab</h2>
      <p class="cl-note">
        The device runtime, not a chat box: intent resolution from <code>alexa/interaction-model.json</code>,
        an ASK response envelope per turn, and a state machine with the model's own silence timers.
        Simulated — this is not Alexa hardware.
      </p>
    </div>
    <span class="cl-pill info" data-state={machine.state}>{deviceState}</span>
  </div>

  <ul class="budget">
    <li><span>{VOICE_BUDGET.maxSpokenWords}</span> words spoken, model limit</li>
    <li><span>{VOICE_BUDGET.silenceTimeoutSeconds}s</span> silence, then</li>
    <li><span>{SIM_MAX_REPROMPTS}</span> reprompt(s) (ours), then the session ends</li>
    <li><span>{INTERACTION_MODEL.languageModel.intents.length}</span> intents ·
      <span>{INTERACTION_MODEL.languageModel.types.length}</span> slot types</li>
    <li>speaking as <span>{lab.speaker ?? '—'}</span></li>
    <li>delivery <span>{lab.delivery ?? DELIVERY.defaultStyle}</span></li>
  </ul>

  <div class="send">
    <label class="cl-label" for="lab-phrase">Say one phrase to the device runtime</label>
    <div class="row">
      <input class="cl-input" id="lab-phrase" bind:value={phrase} autocomplete="off"
        placeholder="what is next for {session.people[0] ?? 'mother'}" />
      <button class="cl-btn" type="button" onclick={send} disabled={lab.running}>Send</button>
    </div>
  </div>

  <div class="scenarios" role="group" aria-label="Scripted conversations">
    {#each SCENARIOS as scenario (scenario.id)}
      <button class="cl-btn sm" type="button" disabled={lab.running}
        aria-pressed={lab.scenario === scenario.id} onclick={() => pick(scenario.id)}
        title={scenario.note}>
        {scenario.label}
      </button>
    {/each}
  </div>

  <div class="u-between controls">
    <div class="row" role="group" aria-label="Playback speed">
      {#each [1, 2, 4] as factor (factor)}
        <button class="cl-btn sm quiet" type="button" aria-pressed={speed === factor}
          onclick={() => (lab.speed = factor)}>{factor}×</button>
      {/each}
    </div>
    <div class="row">
      <button class="cl-btn sm ghost" type="button" onclick={downloadTrace} disabled={!turns.length}>
        Export trace JSON
      </button>
      <button class="cl-btn sm ghost" type="button" onclick={clearLab} disabled={!turns.length}>
        Reset device
      </button>
    </div>
  </div>

  <p class="spoken" aria-live="polite">
    {#if latest?.spoken}
      <strong>Spoken:</strong> {latest.spoken}
    {:else}
      Nothing spoken yet in this lab session.
    {/if}
  </p>

  {#if heldLine}
    <p class="cl-note">{heldLine}</p>
  {/if}

  {#if refusedTurns.length}
    <p class="cl-note">
      {refusedTurns.length} request(s) were bounded by the household’s stored voice scope. The device
      only claimed a voice — every sentence below came back from the service, and each refusal is
      written to this household’s own ledger.
    </p>
  {/if}

  {#if problems.length}
    <div class="cl-callout bad">
      <h3>{problems.length} turn(s) broke a voice rule</h3>
      <ul>
        {#each problems as bad (bad.id)}
          <li>{bad.utterance} — {bad.ask?.problems.join('; ')}</li>
        {/each}
      </ul>
    </div>
  {/if}

  {#if !turns.length}
    <p class="cl-note">Run a scenario, or type a phrase above. Each turn appears here with both
      envelopes.</p>
  {:else}
    <ol class="turns">
      {#each turns as turn (turn.id)}
        {@const ask = turn.ask}
        <li class="turn">
          <div class="u-between">
            <span class="said">“{turn.utterance}”</span>
            <span class="cl-note">{fmtMs(turn.ms)}</span>
          </div>

          <div class="lines">
            {#if turn.proactive}
              <span class="cl-pill info">device-initiated: nothing was heard, so no intent was resolved</span>
            {:else}
              <span class="cl-pill {turn.slu.intent ? 'ok' : 'warn'}">
                model: {turn.slu.intent ?? turn.slu.builtIn ?? 'no match'}
              </span>
            {/if}
            {#if turn.slu.matched === 'fuzzy'}
              <span class="cl-pill warn">no sample said this — the intent was scored, not matched</span>
            {/if}
            <span class="cl-pill info">router: {turn.routed.tool}</span>
            {#if turn.local}
              <span class="cl-pill warn">no intent matched — the device answered locally, the skill was never called</span>
            {/if}
            {#if !turn.agree}
              <span class="cl-pill bad">the two understanders disagree</span>
            {/if}
            {#if turn.dialog}
              <span class="cl-pill {turn.dialog.stage === 'refused' ? 'ok' : 'warn'}">
                {#if turn.dialog.stage === 'elicit'}
                  the model needs {turn.dialog.slot} — asked with {turn.dialog.promptId}, nothing sent
                {:else if turn.dialog.stage === 'confirm'}
                  the model requires a yes — read back with {turn.dialog.promptId}, nothing written yet
                {:else}
                  refused, on the model’s own line — nothing was written
                {/if}
              </span>
            {:else if ask?.slotToElicit}
              <span class="cl-pill warn">asks for {ask.slotToElicit}</span>
            {/if}
            {#each ask?.directives ?? [] as directive (directive.type)}
              <span class="cl-pill">{directive.type}</span>
            {/each}
            {#if turn.connection}
              <span class="cl-pill warn">reminder armed: {turn.connection.uri} comes back after
                {Math.round(turn.connection.afterMs / 1000)} s — the session stays open to be resumed</span>
            {/if}
            {#if turn.proactive}
              <span class="cl-pill info">the turn the customer never asked for: the same read-only call,
                the artifact’s own line in front of the answer, nothing armed again</span>
            {/if}
            {#if ask && ask.masked.count}
              <span class="cl-pill warn">{ask.masked.count} masked field(s)</span>
            {/if}
            {#if turn.delivery}
              <span class="cl-pill info">
                spoken {turn.delivery.style} · rate {turn.delivery.rate} · level {turn.delivery.volume ?? 'unchanged'}
                {#if turn.delivery.truncated}· cut to {turn.delivery.maxSpokenWords} words{:else}
                  · {turn.delivery.maxSpokenWords}-word ceiling, not needed{/if} —
                {turn.delivery.appliedTo === 'held' ? 'the same answer, re-spoken'
                  : turn.delivery.appliedTo === 'only' ? 'nothing was held to re-speak'
                  : 'in force from here'}
              </span>
            {/if}
            {#if turn.meta}
              <span class="cl-pill info">claimed as {turn.meta[VOICE_PROFILE.speakerMetaKey]}
                (simulated) on this request — the service weighs it, the household decides it</span>
            {/if}
            {#if turn.refused}
              <span class="cl-pill bad">the service refused it: {turn.refused}</span>
            {/if}
            {#if ask}
              <span class="cl-note">{ask.words} w · ~{Math.round(ask.estimateMs / 100) / 10} s ·
                session {ask.envelope.response.shouldEndSession ? 'closes' : 'stays open'}</span>
            {/if}
          </div>

          {#if turn.slu.slots.length}
            <table class="cl-table slots">
              <caption class="sr-only">Slot resolution for “{turn.utterance}”</caption>
              <thead>
                <tr><th scope="col">Slot</th><th scope="col">Heard</th><th scope="col">Resolved</th>
                  <th scope="col">State</th><th scope="col">Tool argument</th></tr>
              </thead>
              <tbody>
                {#each turn.slu.slots as slot (slot.name)}
                  <tr>
                    <td>{slot.name}</td>
                    <td>{slot.spoken}</td>
                    <td>{slot.resolved ?? '—'}</td>
                    <td><span class="cl-pill {slotPill(slot.state)}">{slot.state}</span></td>
                    <td><code>{JSON.stringify(modelArgsFor(turn))}</code></td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}

          <details>
            <summary>Envelopes and markup</summary>
            {#if ask}
              <h3 class="mini">Speech ({ask.ssml.length} chars)</h3>
              <pre class="code">{ask.ssml}</pre>
              <h3 class="mini">Request envelope {turn.requestEnvelope ? '' : '(none — the model did not match, so a device answers locally)'}</h3>
              <pre class="code">{JSON.stringify(turn.requestEnvelope ?? { localFallback: INTERACTION_MODEL.fallbackPrompt }, null, 2)}</pre>
              <h3 class="mini">Response envelope</h3>
              <pre class="code">{JSON.stringify(ask.envelope, null, 2)}</pre>
              <h3 class="mini">Card the surface renders</h3>
              <pre class="code">{JSON.stringify(ask.card, null, 2)}</pre>
            {:else if turn.local}
              <h3 class="mini">No envelope — the model matched nothing, so this prompt is the device’s own</h3>
              <pre class="code">{JSON.stringify({
                fallbackPrompt: INTERACTION_MODEL.fallbackPrompt,
                spoken: turn.spoken,
                routerWouldHaveCalled: { tool: turn.routed.tool, args: turn.routed.args },
              }, null, 2)}</pre>
            {:else}
              <pre class="code">{JSON.stringify(turn.requestEnvelope, null, 2)}</pre>
            {/if}
          </details>
        </li>
      {/each}
    </ol>

    <details class="timeline">
      <summary>Device timeline ({events.length} events)</summary>
      <ol>
        {#each events as event, index (`ev-${index}`)}
          <li>
            <span class="cl-note">{(event.atMs / 1000).toFixed(1)}s</span>
            <strong>{event.kind}</strong>
            {event.from} → {event.to}
            <span class="cl-note">— {event.note}</span>
          </li>
        {/each}
      </ol>
    </details>
  {/if}
</section>

<style>
  .lab { display: grid; gap: var(--cl-sp-4); }
  .head { align-items: start; }
  .budget { display: flex; flex-wrap: wrap; gap: var(--cl-sp-4); margin: 0; padding: 0; list-style: none; }
  .budget li { font-size: var(--cl-text-xs); color: var(--cl-ink-2); }
  .budget span { font-weight: 600; color: var(--cl-ink); }
  .send .row, .controls .row { display: flex; gap: var(--cl-sp-2); align-items: center; }
  .send .cl-input { flex: 1; min-width: 0; }
  .scenarios { display: flex; flex-wrap: wrap; gap: var(--cl-sp-2); }
  .controls { flex-wrap: wrap; gap: var(--cl-sp-2); }
  .spoken { margin: 0; font-size: var(--cl-text-sm); }
  .turns { display: grid; gap: var(--cl-sp-3); margin: 0; padding: 0; list-style: none; }
  .turn { border: 1px solid var(--cl-line); border-radius: var(--cl-r); padding: var(--cl-sp-3); display: grid; gap: var(--cl-sp-2); }
  .said { font-weight: 600; }
  .lines { display: flex; flex-wrap: wrap; gap: var(--cl-sp-2); align-items: center; }
  .slots { font-size: var(--cl-text-xs); }
  .mini { margin: var(--cl-sp-3) 0 var(--cl-sp-1); font-size: var(--cl-text-xs); text-transform: uppercase; letter-spacing: .04em; color: var(--cl-ink-3); }
  .code { white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--cl-mono); font-size: var(--cl-text-xs); background: var(--cl-surface-2); padding: var(--cl-sp-2); border-radius: var(--cl-r-sm); margin: 0; }
  .timeline ol { margin: 0; padding-left: var(--cl-sp-5); display: grid; gap: var(--cl-sp-1); font-size: var(--cl-text-xs); }
  .timeline summary, details summary { cursor: pointer; min-height: var(--cl-target); display: flex; align-items: center; }
</style>
