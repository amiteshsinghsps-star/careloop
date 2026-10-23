<script lang="ts">
  /** The unedited protocol log: every JSON-RPC frame this page sent and received, in
   *  order, with the tool name attached so a reviewer can follow one exchange. */
  import type { TrafficEntry } from '@careloop/client';
  import { session, clearTraffic } from '../lib/session.svelte';
  import { copyText, fmtClock } from '../lib/format';

  interface Props {
    open: boolean;
    onClose: () => void;
  }
  let { open, onClose }: Props = $props();

  let filter = $state('all');
  let copied = $state('');

  interface Row {
    entry: TrafficEntry;
    label: string;
    tool: string | null;
  }

  function parseTool(raw: string): string | null {
    try {
      const obj = JSON.parse(raw) as { params?: { name?: string } };
      return typeof obj.params?.name === 'string' ? obj.params.name : null;
    } catch {
      return null;
    }
  }

  /** A response frame carries no method, so it inherits the tool from its request. */
  const rows = $derived.by<Row[]>(() => {
    const out: Row[] = [];
    let lastTool: string | null = null;
    let lastMethod = '';
    for (const entry of session.traffic) {
      if (entry.direction === 'out') {
        lastTool = parseTool(entry.raw);
        lastMethod = entry.method;
        out.push({ entry, tool: lastTool, label: lastTool ? `${entry.method} → ${lastTool}` : entry.method });
      } else {
        out.push({ entry, tool: lastTool, label: `${lastMethod} ← response` });
      }
    }
    return out;
  });

  const tools = $derived([...new Set(rows.map((r) => r.tool).filter((t): t is string => Boolean(t)))].sort());
  const shown = $derived(filter === 'all' ? rows : rows.filter((r) => (r.tool ?? r.entry.method) === filter));

  async function copy(text: string, key: string): Promise<void> {
    copied = (await copyText(text)) ? key : `copy-blocked:${key}`;
    setTimeout(() => (copied = ''), 2500);
  }

  function copyAll(): void {
    void copy(shown.map((r) => `--- ${r.label} ---\n${r.entry.raw}`).join('\n\n'), 'all');
  }
</script>

<svelte:window onkeydown={(e) => open && e.key === 'Escape' && onClose()} />

{#if open}
  <button class="scrim" type="button" aria-label="Close MCP traffic" onclick={onClose}></button>
  <div class="drawer" role="dialog" aria-modal="true" aria-label="MCP traffic log">
    <header class="u-between">
      <div>
        <h3 style="margin:0">MCP traffic</h3>
        <p class="cl-note" style="margin:.25rem 0 0">
          Real JSON-RPC 2.0 over Streamable HTTP — the same <code>POST /mcp</code> frames an
          Alexa+ skill sends. Nothing here is mocked; copy any frame and paste it into a
          terminal to check it.
        </p>
      </div>
      <div class="tiny-gap">
        <label class="sr-only" for="traffic-filter">Filter by tool</label>
        <select id="traffic-filter" class="cl-select" bind:value={filter}>
          <option value="all">All frames ({rows.length})</option>
          {#each tools as name}
            <option value={name}>{name}</option>
          {/each}
        </select>
        <button class="cl-btn sm ghost" type="button" onclick={copyAll}>Copy list</button>
        <button class="cl-btn sm quiet" type="button" onclick={clearTraffic}>Clear</button>
        <button class="cl-btn sm" type="button" onclick={onClose}>Close</button>
      </div>
    </header>

    <div class="body">
      {#if copied.startsWith('copy-blocked')}
        <p class="cl-note">The browser blocked clipboard access. Select the frame and copy it manually.</p>
      {:else if copied}
        <p class="cl-note">Copied.</p>
      {/if}
      {#if !shown.length}
        <p class="cl-note">No frames yet. Send a phrase and the request/response pair appears here.</p>
      {/if}
      {#each shown as row}
        <details class="traffic-item">
          <summary>
            <span class="dir {row.entry.direction}">{row.entry.direction === 'out' ? '→' : '←'} {row.entry.direction}</span>
            <span>{row.label}</span>
            <span class="u-mute">{fmtClock(row.entry.at)}</span>
            {#if row.entry.ms !== undefined}<span class="u-mute">{row.entry.ms} ms</span>{/if}
            {#if row.entry.status !== undefined}
              <span class="cl-pill {row.entry.status >= 400 ? 'bad' : 'done'}">HTTP {row.entry.status}</span>
            {/if}
            <button class="cl-btn sm ghost" type="button" aria-label="Copy this frame"
              onclick={(e) => { e.preventDefault(); void copy(row.entry.raw, String(row.entry.at)); }}>Copy</button>
          </summary>
          <pre>{row.entry.raw}</pre>
        </details>
      {/each}
    </div>
  </div>
{/if}
