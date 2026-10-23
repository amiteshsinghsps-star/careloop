import { ChangeDetectionStrategy, Component, OnDestroy, inject, input, output, signal } from '@angular/core';
import type { CaptureResponse } from '@careloop/client';
import { ADVERSARIAL_VISITS, SAMPLE_VISITS } from '@careloop/client';
import { SessionService } from '../session.service';
import { SpeechService } from '../speech';

interface SampleOption { id: string; title: string; transcript: string; }

@Component({
  selector: 'cl-capture-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="cl-panel">
      <h3 id="capture-title">Capture a visit</h3>
      <p class="cl-note">Paste, upload or record the clinician conversation. The transcript stays in memory only —
        nothing is stored until you extract care items, and raw audio is never kept.</p>

      <div class="cl-field">
        <label for="cap-text">Visit transcript</label>
        <textarea id="cap-text" class="cl-textarea" rows="10" #ta
                  placeholder="Doctor: …" (input)="text.set(ta.value)">{{ text() }}</textarea>
      </div>

      <div class="u-flex" style="flex-wrap: wrap; gap: var(--cl-sp-3);">
        <div class="cl-field" style="margin:0;">
          <label for="cap-person">Who is this for?</label>
          <select id="cap-person" class="cl-select" #personSel>
            <option value="">Household member</option>
            @for (p of people(); track p) {
              <option [value]="p">{{ p }}</option>
            }
          </select>
        </div>
        <div class="cl-field" style="margin:0;">
          <label for="cap-sample">Load an example transcript</label>
          <select id="cap-sample" class="cl-select" #sampleSel (change)="loadSample(sampleSel.value)">
            <option value="">— none —</option>
            @for (s of samples; track s.id) {
              <option [value]="s.id">{{ s.title }}</option>
            }
          </select>
        </div>
        <div class="cl-field" style="margin:0;">
          <label for="cap-file">Upload a .txt file</label>
          <input id="cap-file" class="cl-input" type="file" accept=".txt,text/plain" (change)="readFile(fileInput)" #fileInput />
        </div>
      </div>

      @if (speech.recogniserSupported) {
        <div class="u-stack" style="margin-top: var(--cl-sp-4);">
          <label class="cl-check" for="mic-consent">
            <input id="mic-consent" type="checkbox" [checked]="micConsent()" (change)="micConsent.set(micBox.checked)" #micBox />
            <span>Everyone present at this visit agreed to the recording.</span>
          </label>
          <button type="button" class="cl-btn"
                  [disabled]="!micConsent() && !listening()"
                  [attr.aria-pressed]="listening()" (click)="toggleMic()">
            {{ listening() ? 'Stop recording' : 'Record visit' }}
          </button>
          @if (listening()) {
            <p class="u-tiny u-mute" aria-live="polite">Listening… {{ partial() }}</p>
          }
        </div>
      } @else {
        <p class="cl-note">Recording is not available in this browser — paste or upload the visit text instead.</p>
      }

      <div class="u-flex" style="margin-top: var(--cl-sp-4);">
        <button type="button" class="cl-btn" [disabled]="busy() || !text().trim()" (click)="extract()">
          {{ busy() ? 'Extracting…' : 'Extract care items' }}
        </button>
        <button type="button" class="cl-btn ghost" (click)="clear()">Clear</button>
      </div>

      @if (result(); as r) {
        <div class="u-stack" style="margin-top: var(--cl-sp-5);">
          @if (r.crisis) {
            <div class="cl-callout bad" role="alert">
              <strong>The clinician flagged something urgent.</strong>
              <p>{{ r.message }}</p>
              <p class="u-tiny">No care item was created from this text.</p>
            </div>
          } @else {
            <div class="cl-grid cols-3">
              <div class="cl-metric"><span class="v">{{ r.added ?? 0 }}</span><span class="k">Items added</span></div>
              <div class="cl-metric"><span class="v">{{ r.engine ?? 'rules' }}</span><span class="k">Engine</span></div>
              <div class="cl-metric"><span class="v">{{ r.cost_usd != null ? '$' + r.cost_usd.toFixed(4) : '$0.0000' }}</span><span class="k">Cost</span></div>
            </div>
            <p class="cl-note">Model used: {{ r.model || 'rules fallback' }}.</p>
            @if (r.notes && r.notes.length) {
              <ul class="cl-list">
                @for (n of r.notes; track n) {
                  <li class="cl-callout warn">{{ n }}</li>
                }
              </ul>
            }
          }
        </div>
      }
    </div>
  `,
})
export class CapturePanelComponent implements OnDestroy {
  readonly people = input<string[]>([]);
  readonly extracted = output<string>();

  private readonly session = inject(SessionService);
  readonly speech = inject(SpeechService);

  readonly text = signal('');
  readonly partial = signal('');
  readonly listening = signal(false);
  readonly micConsent = signal(false);
  readonly result = signal<CaptureResponse | null>(null);
  readonly busy = this.session.busy;

  readonly samples: SampleOption[] = [
    ...SAMPLE_VISITS.map((s) => ({ id: s.id, title: `${s.title} (sample)`, transcript: s.transcript })),
    ...ADVERSARIAL_VISITS.map((a) => ({ id: a.id, title: `Refusal test: ${a.id}`, transcript: a.transcript })),
  ];

  loadSample(id: string): void {
    const s = this.samples.find((x) => x.id === id);
    if (s) this.text.set(s.transcript);
  }

  readFile(el: HTMLInputElement): void {
    const file = el.files?.[0];
    el.value = '';
    if (!file) return;
    file.text()
      .then((content) => this.text.set(content))
      .catch(() => this.session.setError('Could not read that file. Try pasting the text instead.'));
  }

  toggleMic(): void {
    if (this.listening()) {
      this.speech.stop();
      this.listening.set(false);
      return;
    }
    if (!this.micConsent()) return;
    const started = this.speech.start({
      onPartial: (p) => this.partial.set(p),
      onFinal: (t) => {
        this.text.update((v) => (v ? v + '\n' + t : t));
        this.partial.set('');
      },
      onError: (m) => {
        this.listening.set(false);
        this.session.setError(m);
      },
      onEnd: () => this.listening.set(false),
    });
    this.listening.set(started);
  }

  async extract(): Promise<void> {
    this.result.set(null);
    const transcript = this.text().trim();
    if (!transcript) return;
    const r = await this.session.capture(transcript);
    this.result.set(r);
    if (r && !r.crisis && (r.added ?? 0) > 0) {
      this.extracted.emit(transcript);
    }
  }

  clear(): void {
    this.text.set('');
    this.partial.set('');
    this.result.set(null);
  }

  ngOnDestroy(): void {
    this.speech.dispose();
  }
}
