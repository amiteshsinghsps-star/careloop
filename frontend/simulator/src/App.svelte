<script lang="ts">
  /** The simulated Alexa+ page. Everything on screen comes from one tool response: the
   *  sentence the device would speak and the card you see are the same structuredContent,
   *  rendered by toView() in frontend/client/src/view.ts. */
  import type { SampleVisit } from '@careloop/client';
  import StatusBar from './lib/StatusBar.svelte';
  import Composer from './lib/Composer.svelte';
  import DeviceControls from './lib/DeviceControls.svelte';
  import PlanCard from './lib/PlanCard.svelte';
  import type { CardActions } from './lib/types';
  import ReplayPanel from './lib/ReplayPanel.svelte';
  import TrafficDrawer from './lib/TrafficDrawer.svelte';
  import TurnList from './lib/TurnList.svelte';
  import CommandPalette from './lib/CommandPalette.svelte';
  import LiveBadge from './lib/LiveBadge.svelte';
  import DeviceLab from './lib/DeviceLab.svelte';
  import { startLive, stopLive } from './lib/live.svelte';
  import {
    boot, cancelAskBack, confirmDelete, currentCard, explainItem, markDone, pickPerson,
    runAdversarialSet, runSampleVisit, say, stopPolling, configureClients, DEMO_HOUSEHOLD, device,
  } from './lib/device.svelte';
  import { session, setTts, setTextSize, toggleTrafficOpen, tt } from './lib/session.svelte';
  import { disposeSpeech } from './lib/speech.svelte';

  let prefill = $state('');
  let paletteOpen = $state(false);

  $effect(() => {
    configureClients();
    void boot().then(() => startLive());
    return () => {
      stopLive();
      stopPolling();
      disposeSpeech();
    };
  });

  $effect(() => {
    const root = document.documentElement;
    root.dataset.theme = session.theme;
    root.dataset.text = session.text;
    root.lang = session.lang;
  });

  // Ctrl/Cmd+K toggles the command palette anywhere on the page (but not while typing a
  // composed phrase into the composer, where it would steal the keystroke).
  $effect(() => {
    function onGlobalKey(event: KeyboardEvent): void {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        paletteOpen = !paletteOpen;
      }
    }
    window.addEventListener('keydown', onGlobalKey);
    return () => window.removeEventListener('keydown', onGlobalKey);
  });

  const card = $derived(currentCard());
  const busy = $derived(device.busy);
  const needsLinking = $derived(device.needsLinking);

  /** Palette commands (M8): every one is an action you can already do by voice or tap on this
   *  screen. `utterance` is the phrase the router in utterance.ts understands; screen-only
   *  actions (traffic, font size, judge console) leave it empty and the row says so honestly. */
  const commands = $derived.by(() => {
    const who = session.people[0];
    const list: Array<{
      id: string; label: string; utterance: string; keywords?: string; run: () => void;
    }> = [
      { id: 'next', label: who ? `What’s next for ${who}` : 'What is the plan today',
        utterance: who ? `what is next for ${who}` : 'what is the plan today',
        keywords: 'agenda plan today', run: () => void say(who ? `what is next for ${who}` : 'what is the plan today') },
      { id: 'done-morning', label: 'Mark the morning one done',
        utterance: 'mark the morning one as done', keywords: 'complete taken',
        run: () => void say('mark the morning one as done') },
      { id: 'last-visit', label: 'Show the last visit',
        utterance: 'why is the first item there', keywords: 'source proof recorded clinician',
        run: () => void explainItem('first') },
      { id: 'people', label: 'Who is in my household', utterance: 'who is in my household',
        keywords: 'family list people', run: () => void say('who is in my household') },
      { id: 'traffic', label: 'Show MCP traffic', utterance: '', keywords: 'protocol frames json',
        run: () => toggleTrafficOpen(true) },
      { id: 'large-text', label: 'Toggle large text', utterance: '', keywords: 'elder font size accessibility',
        run: () => setTextSize(session.text === 'large' ? 'normal' : 'large') },
      { id: 'judge', label: 'Open judge panel', utterance: '', keywords: 'conformance console metrics',
        run: () => { window.location.href = 'judge.html'; } },
    ];
    return list;
  });

  /** Chip click: call a read-only tool directly; for tools that need an argument we have
   *  no business inventing (a note), put the phrase in the composer instead. */
  async function onChip(name: string): Promise<void> {
    if (name === 'add_note') {
      prefill = 'add a note that the clinic changed the physio time to Thursday';
      return;
    }
    const tool = device.tools.find((t) => t.name === name);
    const required = (tool?.inputSchema as { required?: string[] } | undefined)?.required ?? [];
    const args: Record<string, unknown> = {};
    if (required.includes('ref')) args.ref = 'first';
    if (required.includes('person')) args.person = session.people[0];
    await say('', { tool: name, args });
  }

  const actions: CardActions = {
    done: (ref) => void markDone(ref),
    explain: (ref) => void explainItem(ref),
    confirmDelete: (ref) => void confirmDelete(ref),
    cancel: () => cancelAskBack(),
    pickPerson: (name) => void pickPerson(name),
    say: (text) => void say(text),
  };

  function onReplay(sample: SampleVisit, askNamed: boolean): void {
    void runSampleVisit(sample, askNamed);
  }
</script>

<a class="skip-link" href="#composer">{tt('skipToComposer')}</a>

<div class="cl-shell">
  <header class="cl-nav cl-wrap">
    <a class="brand" href="."><span class="mark" aria-hidden="true"></span>CareLoop</a>
    <span class="badge-sim">Simulated Alexa+ experience</span>
    <nav aria-label="CareLoop pages">
      <button class="cl-btn sm ghost" type="button" aria-haspopup="dialog"
        aria-expanded={paletteOpen} onclick={() => (paletteOpen = true)}>{tt('openPalette')} (Ctrl K)</button>
      <a href="judge.html">Judge console</a>
      <a href="/judge">Judge (server page)</a>
      <a href="/companion/">Companion (full doses)</a>
    </nav>
  </header>

  <div class="cl-wrap">
    <p class="cl-note honesty">
      This page is a <strong>simulated Alexa+ device</strong>, built for the hackathon demo. The
      voice model on a real Alexa+ is Amazon’s own: it hears the customer, then calls these MCP
      tools. Here a labelled deterministic router
      (<code>route()</code>, <code>frontend/client/src/utterance.ts</code>) plays that part so the
      demo is reproducible offline. Everything below the router — arguments, validation, masking,
      storage — is the real server over real JSON-RPC.
    </p>

    {#if needsLinking}
      <div class="cl-callout warn">
        <h3>Account linking is on</h3>
        <p>{device.bootError ?? 'The server returned HTTP 401 with a resource-metadata challenge.'}</p>
        <p class="cl-note">
          The server is running with <code>CARELOOP_AUTH=on</code>. For this demo it normally runs
          with <code>CARELOOP_AUTH=off</code>, where <code>POST /mcp</code> needs no bearer token and
          the dev identity binds household <code>{DEMO_HOUSEHOLD.code}</code> (PIN
          <code>{DEMO_HOUSEHOLD.pin}</code>). Sign in at <a href="/companion/">/companion/</a> to get a
          token, or restart the server with auth off.
        </p>
      </div>
    {:else if device.bootError}
      <div class="cl-callout bad">
        <p>{device.bootError}</p>
      </div>
    {/if}

    <main class="cl-main" id="main">
      <StatusBar onOpenTraffic={() => toggleTrafficOpen(true)} />
      <div class="livebar"><LiveBadge /></div>
      <DeviceControls />

      <section class="cl-panel tools-panel" aria-labelledby="tools-h">
        <div class="u-between">
          <h2 id="tools-h" style="margin:0">Tools this server publishes</h2>
          <span class="cl-note">{device.tools.length} · from <code>tools/list</code></span>
        </div>
        <p class="cl-note">
          Tap a tool to call it directly, with only the arguments its own schema asks for.
          {#if device.serverLabel}Serving as <code>{device.serverLabel}</code>.{/if}
        </p>
        <div class="chips">
          {#each device.tools as tool}
            <button class="chip" type="button" onclick={() => onChip(tool.name)} title={tool.description}>
              {tool.name}
            </button>
          {/each}
          {#if !device.tools.length}
            <span class="cl-note">No tool list yet — the server has not answered <code>tools/list</code>.</span>
          {/if}
        </div>
      </section>

      <div class="sim-grid">
        <div class="u-stack">
          <section class="cl-panel" id="composer" aria-labelledby="ask-h">
            <h2 id="ask-h" style="margin-top:0">Talk to it</h2>
            <Composer {busy} disabled={!device.booted} {prefill} onSay={(text) => void say(text)} />
          </section>

          <section class="cl-panel" aria-labelledby="said-h">
            <div class="u-between">
              <h2 id="said-h" style="margin:0">This session</h2>
              <button class="cl-btn sm quiet" type="button" aria-pressed={session.tts}
                onclick={() => setTts(!session.tts)}>
                {session.tts ? tt('voiceOn') : tt('voiceOff')}
              </button>
            </div>
            <TurnList turns={session.turns} {busy} busyRef={device.busyRef} people={session.people} {actions} />
          </section>

          <DeviceLab />

          <section class="cl-panel" aria-labelledby="replay-h">
            <h2 id="replay-h" style="margin-top:0">Feed it something</h2>
            <ReplayPanel {busy} captureLine={device.captureLine} onSample={onReplay}
              onAdversarial={() => void runAdversarialSet()} />
          </section>
        </div>

        <aside class="card-rail" data-device={session.device} aria-labelledby="card-h">
          <section class="cl-panel">
            <div class="rail-title">
              <h2 id="card-h" style="margin:0">On the screen</h2>
              <span class="cl-pill info">{session.device === 'small' ? '≤10" width' : '≥11" width'}</span>
            </div>
            <p class="cl-note">
              The card is the same <code>structuredContent</code> the voice is reading, rendered by
              <code>toView()</code>. It is dose-masked by the server, and it never shows a dose.
              The identical markup also ships standalone: <code>card.html</code> (this dev server) or
              <a href="/card">/card</a> on the CareLoop server — built by
              <code>vite build --config vite.card.config.mts</code> into one self-contained file that
              the server inlines into <code>resources/read</code> for
              <code>ui://careloop/card</code>.
            </p>
            {#if card}
              <PlanCard view={card.view} highlight={card.highlight} {actions} busyRef={device.busyRef}
                people={session.people} screen={true} />
            {:else}
              <p class="cl-note">Nothing on screen yet — ask a question and the card fills in.</p>
            {/if}
          </section>
        </aside>
      </div>

      <footer class="cl-panel footer">
        <p class="cl-disclaimer">
          {card?.view.dosePolicy
            ?? 'Doses stay masked on every voice and card surface: CareLoop reads out what to do and when, never a quantity.'}
        </p>
        <ul class="cl-flow">
          <li class="step">Visit transcript</li>
          <li class="arrow">→</li>
          <li class="step">capture + guards</li>
          <li class="arrow">→</li>
          <li class="step">MCP tools</li>
          <li class="arrow">→</li>
          <li class="step">masked voice + card</li>
        </ul>
        <p class="cl-note">
          <a href="/companion/">CareLoop companion</a> shows the full, exact doses to the signed-in
          family only. <a href="judge.html">Judge console</a> (or <a href="/judge">/judge</a>) shows
          the protocol conformance, latency and policy self-test for this same server.
        </p>
      </footer>
    </main>
  </div>
</div>

<TrafficDrawer open={session.trafficOpen} onClose={() => toggleTrafficOpen(false)} />
<CommandPalette open={paletteOpen} {commands} onClose={() => (paletteOpen = false)} />

<style>
  .cl-wrap { padding-block: var(--cl-sp-3); }
  .livebar { margin-block: var(--cl-sp-3); }
  .honesty { border-left: 4px solid var(--cl-info); padding-left: var(--cl-sp-4); margin-top: var(--cl-sp-4); }
  .tools-panel { margin-block: var(--cl-sp-5); }
  .footer { margin-top: var(--cl-sp-6); display: grid; gap: var(--cl-sp-4); }
  .footer p { margin: 0; }
</style>
