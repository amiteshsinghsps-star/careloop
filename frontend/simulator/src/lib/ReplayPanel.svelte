<script lang="ts">
  /** Feeding the plan: the sample visits are synthetic transcripts (no real patients),
   *  and the adversarial set exists to show the guards working, not to impress. */
  import { SAMPLE_VISITS } from '@careloop/client';
  import type { SampleVisit } from '@careloop/client';
  import { guardLog } from './device.svelte';
  import { EXPECTED_LABEL, ADVERSARIAL_LABEL } from './replayMeta';

  interface Props {
    busy: boolean;
    captureLine: string | null;
    /** Second argument: name the person in the follow-up phrase, or leave it open so the
     *  server has to ask back. */
    onSample: (sample: SampleVisit, askNamedPerson: boolean) => void;
    onAdversarial: () => void;
  }
  let { busy, captureLine, onSample, onAdversarial }: Props = $props();

  const running = $derived(guardLog.some((g) => g.outcome === 'waiting'));
</script>

<div class="u-stack">
  <div>
    <h3>Replay a sample visit</h3>
    <p class="cl-note">
      Synthetic adult visit transcripts. Each one goes to <code>POST /api/capture</code> (the
      companion side), then we ask the voice surface “what’s next”. Extraction engine, item
      count and model cost are reported exactly as the server returns them.
    </p>
    {#if captureLine}<p class="cl-callout" role="status">{captureLine}</p>{/if}
  </div>

  <div class="replay-list">
    {#each SAMPLE_VISITS as sample}
      <div class="replay-row">
        <h4>{sample.title} · {sample.person}</h4>
        <details>
          <summary class="cl-note">Show the transcript the extractor sees</summary>
          <pre class="result-pane transcript">{sample.transcript}</pre>
        </details>
        <div class="tiny-gap">
          <button class="cl-btn sm" type="button" disabled={busy} onclick={() => onSample(sample, true)}>
            Capture, then ask what’s next for {sample.person}
          </button>
          <button class="cl-btn sm ghost" type="button" disabled={busy} onclick={() => onSample(sample, false)}>
            Capture, then ask without naming anyone
          </button>
        </div>
      </div>
    {/each}
  </div>

  <div>
    <h3>Adversarial set — do the guards hold?</h3>
    <p class="cl-note">
      Four transcripts that must be refused, redirected or reclassified
      (<code>ADVERSARIAL_VISITS</code> in <code>frontend/client/src/samples.ts</code>). They run
      against the live server; the verdicts below are its answers, not ours.
    </p>
    <button class="cl-btn" type="button" disabled={busy} onclick={onAdversarial}>
      {running ? 'Running…' : 'Run all four'}
    </button>

    {#if guardLog.length}
      <table class="cl-table" style="margin-top:var(--cl-sp-4)">
        <thead>
          <tr><th scope="col">Scenario</th><th scope="col">Expected</th><th scope="col">What happened</th><th scope="col">Result</th></tr>
        </thead>
        <tbody>
          {#each guardLog as verdict}
            <tr>
              <td>{ADVERSARIAL_LABEL[verdict.id] ?? verdict.id}</td>
              <td>{EXPECTED_LABEL[verdict.expect] ?? verdict.expect}</td>
              <td>{verdict.outcome === 'waiting' ? 'running…' : verdict.detail}</td>
              <td>
                {#if verdict.outcome === 'waiting'}
                  <span class="cl-pill info">pending</span>
                {:else if verdict.ok}
                  <span class="cl-pill done">guard held</span>
                {:else}
                  <span class="cl-pill bad">did not hold</span>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
      <p class="cl-note" style="margin-top:var(--cl-sp-3)">
        A “did not hold” row is a real finding, not a cosmetic one — it means the server stored
        or answered something it should have refused.
      </p>
    {/if}
  </div>
</div>

<style>
  .transcript { max-height: 14rem; white-space: pre-wrap; }
</style>
