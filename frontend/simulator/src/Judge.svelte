<script lang="ts">
  /** Judge console: one page that answers “is this really an MCP server, is it fast
   *  enough, and do the policy guards actually fire?” — plus a live tool console so a
   *  reviewer can prove any tool is invocable from a browser. */
  import { CareLoopApi, McpClient, McpUnauthorized } from '@careloop/client';
  import type { JudgeData, Percentiles, ToolDef } from '@careloop/client';
  import { session, setAutoRefresh, setTheme } from './lib/session.svelte';
  import { copyText, errorTotal, fmtCost, fmtMs } from './lib/format';

  const SLO_MS = 500;
  const api = new CareLoopApi();

  let data = $state<JudgeData | null>(null);
  let loadError = $state<string | null>(null);
  let updatedAt = $state<number | null>(null);
  let loading = $state(false);

  /* ------------------------------------------------------------------ data */

  async function refresh(): Promise<void> {
    loading = true;
    try {
      data = await api.judgeData();
      loadError = null;
      updatedAt = Date.now();
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    document.documentElement.dataset.theme = session.theme;
  });

  $effect(() => {
    void refresh();
    void bootConsole();
    const id = window.setInterval(() => {
      if (session.autoRefresh) void refresh();
    }, 5000);
    return () => window.clearInterval(id);
  });

  const latencyRows = $derived.by(() => {
    const errors = data?.metrics.errors ?? {};
    return Object.entries(data?.metrics.latency ?? {})
      .map(([tool, p]) => ({ tool, p, errors: errors[tool] ?? 0 }))
      .sort((a, b) => (b.p.p95 ?? 0) - (a.p.p95 ?? 0));
  });
  const slowestP95 = $derived(
    latencyRows.reduce((max, row) => Math.max(max, row.p.p95 ?? 0), 0),
  );
  const sloHeld = $derived(data ? slowestP95 < SLO_MS : null);

  /* ------------------------------------------------------------ tool console */

  const mcp = new McpClient({
    protocolVersion: '2025-11-25',
    clientName: 'CareLoop judge console',
  });
  const consoleTools = $state<ToolDef[]>([]);
  const consoleState = $state({
    ready: false,
    error: null as string | null,
    sessionId: null as string | null,
    protocol: null as string | null,
    chosen: '',
    args: '{}',
    result: '',
    ms: null as number | null,
    ran: false,
    running: false,
  });

  async function bootConsole(): Promise<void> {
    try {
      const init = await mcp.initialize();
      consoleState.protocol = mcp.negotiated ?? init?.protocolVersion ?? null;
      consoleState.sessionId = mcp.sessionId;
      consoleTools.length = 0;
      consoleTools.push(...await mcp.listTools());
      consoleState.chosen = consoleTools.some((t) => t.name === 'get_care_plan')
        ? 'get_care_plan'
        : consoleTools[0]?.name ?? '';
      consoleState.args = '{}';
      consoleState.ready = true;
      consoleState.error = null;
    } catch (err) {
      consoleState.error = err instanceof McpUnauthorized
        ? `${err.message} (the server is running with CARELOOP_AUTH=on)`
        : err instanceof Error ? err.message : String(err);
    }
  }

  /** A harmless starting body from the tool's own input schema — nothing invented. */
  function seedArgs(def: ToolDef): string {
    const props = (def.inputSchema as { properties?: Record<string, any> }).properties ?? {};
    const required = (def.inputSchema as { required?: string[] }).required ?? [];
    const body: Record<string, unknown> = {};
    for (const key of required) {
      const spec = props[key] ?? {};
      if (Array.isArray(spec.enum) && spec.enum.length) body[key] = spec.enum[0];
      else if (spec.type === 'integer') body[key] = spec.minimum ?? 1;
      else if (spec.type === 'boolean') body[key] = false;
      else if (key === 'ref') body[key] = 'first';
      else if (key === 'text') body[key] = 'Replace me with the note to remember.';
      else if (key === 'person') body[key] = '';
      else body[key] = '';
    }
    return JSON.stringify(body, null, 2);
  }

  function chooseTool(name: string): void {
    consoleState.chosen = name;
    const def = consoleTools.find((t) => t.name === name);
    consoleState.args = def ? seedArgs(def) : '{}';
  }

  async function runConsole(): Promise<void> {
    consoleState.running = true;
    consoleState.ran = true;
    try {
      const parsed = JSON.parse(consoleState.args || '{}') as Record<string, unknown>;
      const result = await mcp.callTool(consoleState.chosen, parsed);
      consoleState.ms = result.ms;
      consoleState.result = JSON.stringify(
        {
          isError: result.isError ?? false,
          ms: result.ms,
          content: result.content,
          structuredContent: result.structuredContent,
          _meta: result._meta,
        },
        null, 2);
      consoleState.error = null;
    } catch (err) {
      consoleState.result = '';
      consoleState.error = err instanceof Error ? err.message : String(err);
    } finally {
      consoleState.running = false;
      void refresh();
    }
  }

  /* --------------------------------------------------------------- commands */

  const commands: { cmd: string; note: string }[] = [
    { cmd: 'make serve', note: 'CARELOOP_AUTH=off HOST=127.0.0.1 PORT=8080 python -m careloop.mcp_server' },
    { cmd: 'make preflight', note: 'python -m tools.preflight http://127.0.0.1:8080/mcp' },
    { cmd: 'npm run preflight', note: 'python -m preflight http://127.0.0.1:8080/mcp' },
    { cmd: 'make smoke', note: 'python scripts/smoke.py — handshake, tools/list, one tool call' },
    { cmd: 'make test', note: 'python -m pytest -q tests' },
    { cmd: 'npx vitest run', note: 'run from frontend/simulator — the router and render-model suites' },
  ];

  let copied = $state('');
  async function copyCommand(cmd: string): Promise<void> {
    copied = (await copyText(cmd)) ? cmd : '';
    setTimeout(() => (copied = ''), 2000);
  }

  const policyFlags = $derived.by<{ label: string; ok: boolean; missed: string }[]>(() => {
    const test = data?.policy_self_test;
    if (!test) return [];
    return [
      { label: 'Crisis recall', ok: test.crisis_recall_demo, missed: 'MISSED' },
      { label: 'Child data blocked', ok: test.child_block_demo, missed: 'STORED' },
      { label: 'Prompt injection blocked', ok: test.injection_block_demo, missed: 'FOLLOWED' },
    ];
  });

  function pill(p: Percentiles): { cls: string; text: string } {
    if (p.p95 === null) return { cls: 'info', text: 'no calls yet' };
    return p.p95 < SLO_MS ? { cls: 'done', text: 'p95 under 500 ms' } : { cls: 'bad', text: 'p95 over 500 ms' };
  }
</script>

<a class="skip-link" href="#console">Skip to the live tool console</a>

<div class="cl-shell">
  <header class="cl-nav cl-wrap">
    <a class="brand" href="index.html"><span class="mark" aria-hidden="true"></span>CareLoop</a>
    <span class="cl-pill info">Judge console</span>
    <nav aria-label="CareLoop pages">
      <a href="index.html">Simulated Alexa+</a>
      <a href="/companion/">Companion</a>
      <a href="/simulator/">Simulator (server page)</a>
    </nav>
  </header>

  <div class="cl-wrap">
    <main class="cl-main">
      <div class="u-between" style="margin-bottom:var(--cl-sp-5)">
        <div>
          <h1 style="margin:0">Conformance, latency and policy — live</h1>
          <p class="cl-note" style="margin:var(--cl-sp-2) 0 0">
            Every number here is read from the running server at <code>GET /api/judge-data</code>.
            {#if updatedAt}Last refresh {new Date(updatedAt).toLocaleTimeString()}.{/if}
            {#if loading}Refreshing…{/if}
          </p>
        </div>
        <div class="tiny-gap">
          <button class="cl-btn" type="button" onclick={refresh} disabled={loading}>Refresh</button>
          <label class="cl-check">
            <input type="checkbox" checked={session.autoRefresh}
              onchange={(e) => setAutoRefresh(e.currentTarget.checked)} />
            <span>Auto-refresh every 5 s</span>
          </label>
          <button class="cl-btn sm quiet" type="button" aria-pressed={session.theme === 'dark'}
            onclick={() => setTheme(session.theme === 'dark' ? 'light' : 'dark')}>
            {session.theme === 'dark' ? 'Light theme' : 'Dark theme'}
          </button>
        </div>
      </div>

      {#if loadError}
        <p class="cl-callout bad" role="alert">{loadError} — start the server with <code>make serve</code>,
          then refresh.</p>
      {/if}

      {#if data}
        <section class="cl-panel" aria-labelledby="proto-h">
          <h2 id="proto-h">Protocol and manifest</h2>
          <div class="cl-grid cols-3">
            <div class="cl-metric"><span class="v">{data.protocol.join(', ')}</span><span class="k">MCP protocol versions served</span></div>
            <div class="cl-metric"><span class="v">{data.tools.length}</span><span class="k">tools published</span></div>
            <div class="cl-metric"><span class="v">{data.guest_safe.length}</span><span class="k">guest-safe tools</span></div>
          </div>
          <p class="mono-xs">manifest hash · {data.manifest_hash}</p>
          <p class="cl-label">Guest-safe (callable before account linking)</p>
          <div class="chips">
            {#each data.guest_safe as name}<span class="chip">{name}</span>{/each}
          </div>
          <p class="cl-label">All tools in the manifest</p>
          <div class="chips">
            {#each data.tools as name}<span class="chip">{name}</span>{/each}
          </div>
        </section>

        <section class="cl-panel" aria-labelledby="lat-h">
          <div class="u-between">
            <h2 id="lat-h" style="margin:0">Per-tool latency and errors</h2>
            <span class="cl-pill {sloHeld ? 'done' : 'bad'}">
              SLO p95 &lt; 500 ms · {sloHeld ? 'PASS' : 'FAIL'}
            </span>
          </div>
          <p class="cl-note">
            Requests so far: {data.metrics.requests} · errors {errorTotal(data.metrics.errors)} ·
            worst p95 {fmtMs(slowestP95)}.
          </p>
          <table class="cl-table">
            <thead>
              <tr>
                <th scope="col">Tool</th><th scope="col">p50</th><th scope="col">p95</th>
                <th scope="col">p99</th><th scope="col">calls</th><th scope="col">errors</th>
                <th scope="col">p95 &lt; 500 ms</th>
              </tr>
            </thead>
            <tbody>
              {#each latencyRows as row}
                <tr>
                  <td><code>{row.tool}</code></td>
                  <td>{fmtMs(row.p.p50)}</td><td>{fmtMs(row.p.p95)}</td><td>{fmtMs(row.p.p99)}</td>
                  <td>{row.p.n}</td><td>{row.errors}</td>
                  <td><span class="cl-pill {pill(row.p).cls}">{pill(row.p).text}</span></td>
                </tr>
              {/each}
              {#if !latencyRows.length}
                <tr><td colspan="7" class="u-mute">No tool has been called since the server started.</td></tr>
              {/if}
            </tbody>
          </table>

          <div class="cl-grid cols-3" style="margin-top:var(--cl-sp-5)">
            <div class="cl-metric"><span class="v">{fmtCost(data.metrics.cost_usd)}</span><span class="k">Cumulative model cost</span></div>
            <div class="cl-metric"><span class="v">{data.metrics.escalations}</span><span class="k">Escalations (crisis redirects)</span></div>
            <div class="cl-metric"><span class="v">{data.chaos.length ? data.chaos.join(', ') : 'none'}</span><span class="k">Chaos switches in effect</span></div>
          </div>
          {#if data.chaos.length}
            <p class="cl-note">Failure injection is on: {data.chaos.join(', ')} — throttling, a skipped
              verifier, an 800&nbsp;ms tool stall or a read-only store, each with a real call site
              (see the chaos flags in README.md).</p>
          {:else}
            <p class="cl-note">No faults injected right now. Set
              <code>CARELOOP_CHAOS=latency_800ms,db_locked</code> to watch the degradation path.</p>
          {/if}
          {#if data.chaos_unknown.length}
            <p class="cl-note warn">These names are not recognised and do nothing:
              {data.chaos_unknown.join(', ')}.</p>
          {/if}
        </section>

        <section class="cl-panel" aria-labelledby="ledger-h">
          <h2 id="ledger-h">Per-cell ledger verification</h2>
          <p class="cl-note">Each household is its own encrypted cell with a hash-chained event log.</p>
          <table class="cl-table">
            <thead>
              <tr><th scope="col">Cell</th><th scope="col">Chain</th><th scope="col">Events</th>
                <th scope="col">Checked</th><th scope="col">Head</th></tr>
            </thead>
            <tbody>
              {#each Object.entries(data.ledger) as [cell, res]}
                <tr>
                  <td><code>{cell}</code></td>
                  <td>
                    {#if res.ok}<span class="cl-pill done">verified</span>
                    {:else}<span class="cl-pill bad">broken{res.broken_at !== null && res.broken_at !== undefined ? ` at ${res.broken_at}` : ''}</span>{/if}
                  </td>
                  <td>{res.events ?? '—'}</td><td>{res.checked ?? '—'}</td>
                  <td class="mono-xs">{res.head ? res.head.slice(0, 16) : '—'}</td>
                </tr>
              {/each}
              {#if !Object.keys(data.ledger).length}
                <tr><td colspan="5" class="u-mute">No households created yet.</td></tr>
              {/if}
            </tbody>
          </table>
          {#if Object.values(data.ledger).some((l) => l.detail)}
            <p class="cl-note">{Object.values(data.ledger).map((l) => l.detail).filter(Boolean).join(' · ')}</p>
          {/if}
        </section>

        <section class="cl-panel" aria-labelledby="policy-h">
          <h2 id="policy-h">Policy self-test</h2>
          <p class="cl-label">Dose leak after masking (must be 0)</p>
          <p><span class="cl-pill {data.policy_self_test.dose_leak_after_mask === 0 ? 'done' : 'bad'}">
            {data.policy_self_test.dose_leak_after_mask} leaked
          </span></p>
          <ul class="cl-list">
            {#each data.policy_self_test.masked_examples as example}
              <li class="cl-quote">{example}</li>
            {/each}
          </ul>
          <div class="tiny-gap">
            {#each policyFlags as flag}
              <span class="cl-pill {flag.ok ? 'done' : 'bad'}">{flag.label}: {flag.ok ? 'detected' : flag.missed}</span>
            {/each}
          </div>
        </section>
      {:else if !loadError}
        <p class="cl-note" role="status">Loading the judge payload…</p>
      {/if}

      <section class="cl-panel" id="console" aria-labelledby="console-h">
        <div class="u-between">
          <h2 id="console-h" style="margin:0">Call any tool, right here</h2>
          <span class="cl-pill {consoleState.ready ? 'done' : 'warn'}">
            {consoleState.ready ? `negotiated ${consoleState.protocol ?? '—'}` : 'not initialised'}
          </span>
        </div>
        <p class="cl-note">
          This is the same <code>McpClient</code> the simulator uses: a real
          <code>initialize</code> → <code>tools/list</code> → <code>tools/call</code> handshake from
          the browser to <code>POST /mcp</code>. Session id <code>{consoleState.sessionId ?? '—'}</code>.
        </p>
        {#if consoleState.error && !consoleState.ready}
          <p class="cl-callout bad">{consoleState.error}</p>
          <button class="cl-btn ghost" type="button" onclick={bootConsole}>Try the handshake again</button>
        {:else}
          <div class="console-grid">
            <div>
              <label class="cl-field">
                <span class="cl-label">Tool</span>
                <select class="cl-select" value={consoleState.chosen}
                  onchange={(e) => chooseTool(e.currentTarget.value)}>
                  {#each consoleTools as tool}
                    <option value={tool.name}>{tool.name}</option>
                  {/each}
                </select>
              </label>
              <label class="cl-field">
                <span class="cl-label">arguments (JSON)</span>
                <textarea class="cl-textarea" spellcheck="false"
                  bind:value={consoleState.args} aria-describedby="args-help"></textarea>
              </label>
              <p class="cl-note" id="args-help">
                The body starts from the tool’s own <code>required</code> list; the server validates
                it against the published schema before anything runs.
              </p>
              <div class="tiny-gap">
                <button class="cl-btn" type="button" onclick={runConsole}
                  disabled={consoleState.running || !consoleState.chosen}>
                  {consoleState.running ? 'Calling…' : 'Call tools/call'}
                </button>
                <button class="cl-btn ghost sm" type="button" onclick={() => chooseTool(consoleState.chosen)}>
                  Reset arguments
                </button>
                {#if consoleState.ms !== null}
                  <span class="cl-note">{fmtMs(consoleState.ms)} round trip</span>
                {/if}
              </div>
            </div>
            <div>
              <p class="cl-label">Result</p>
              {#if consoleState.error && consoleState.ran}
                <p class="cl-callout bad">{consoleState.error}</p>
              {:else}
                <pre class="result-pane">{consoleState.result || 'No call made yet.'}</pre>
              {/if}
            </div>
          </div>
        {/if}
      </section>

      <section class="cl-panel" aria-labelledby="cmd-h">
        <h2 id="cmd-h">Run the preflight yourself</h2>
        <p class="cl-note">From the repository root, with the server on port 8080:</p>
        {#each commands as item}
          <div class="cmd-row">
            <pre class="cmd">{item.cmd}</pre>
            <span class="cl-note">{item.note}</span>
            <button class="cl-btn sm ghost" type="button" onclick={() => copyCommand(item.cmd)}>
              {copied === item.cmd ? 'Copied' : 'Copy'}
            </button>
          </div>
        {/each}
        <p class="cl-disclaimer">
          The published manifest, protocol versions and metrics on this page come from the server
          under test — nothing here is a screenshot or a recorded transcript.
        </p>
      </section>
    </main>
  </div>
</div>

<style>
  .cmd-row { display: grid; grid-template-columns: minmax(0, 22rem) minmax(0, 1fr) auto; gap: var(--cl-sp-3); align-items: center; }
  .cmd-row .cmd { margin: 0; }
  @media (max-width: 48rem) { .cmd-row { grid-template-columns: minmax(0, 1fr); } }
  .chip { pointer-events: none; }
</style>
