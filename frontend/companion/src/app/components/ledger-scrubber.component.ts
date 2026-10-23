import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { STATUS_LABEL, kindLabel } from '@careloop/ui';
import { diffLedgerSnapshot, ledgerValueText } from '@careloop/client';
import type { LedgerItemSnapshot, LedgerSnapshot } from '@careloop/client';
import { SessionService } from '../session.service';

/** A moment the scrubber can land on is a distinct event time on the audit chain. The slider
 *  index walks those moments; the far-right position (index === number of stops) is "now". */
@Component({
  selector: 'cl-ledger-scrubber',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="cl-panel">
      <h3>Ledger time travel</h3>
      <p class="cl-note">Drag the slider (or use the arrow keys) to replay the audit chain up to an earlier
        moment and see the plan exactly as it stood then. The far-right position is now. Nothing here is
        edited or re-worded — every line is the server's own replay of the chain.</p>

      <p class="u-tiny u-mute" [hidden]="!busy()" aria-live="polite">Reading the ledger…</p>

      @if (error(); as err) {
        <div class="cl-errorbox" role="alert" aria-live="assertive" style="margin: var(--cl-sp-3) 0;">
          {{ err }}
        </div>
      }

      @if (now(); as n) {
        <div class="scrub-row">
          <label for="cl-scrub" class="cl-label">Jump to an earlier moment</label>
          <input id="cl-scrub" type="range" class="cl-range"
                 min="0" [attr.max]="stops().length" step="1"
                 [value]="idx()" [disabled]="busy()"
                 [attr.aria-valuenow]="idx()" aria-valuemin="0" [attr.aria-valuemax]="stops().length"
                 [attr.aria-valuetext]="valueText()"
                 (input)="onSlide($any($event.target).value)" />
          <span class="cl-range-when" aria-hidden="true">{{ dateLabel() }}</span>
          <button type="button" class="cl-btn quiet sm" (click)="jumpToNow()"
                  [attr.aria-pressed]="isNow()">Now</button>
        </div>

        <form class="scrub-form" (submit)="onManualJump($event)">
          <div class="cl-field" style="margin:0; flex:1; min-width: 12rem;">
            <label for="cl-scrub-date">…or type a moment</label>
            <input id="cl-scrub-date" class="cl-input" type="text" inputmode="text" autocomplete="off"
                   placeholder="e.g. 2026-10-18T14:30"
                   [value]="manual()" (input)="manual.set($any($event.target).value)" />
          </div>
          <button type="submit" class="cl-btn ghost" [disabled]="busy()">Go</button>
          <button type="button" class="cl-btn quiet" [disabled]="busy()" (click)="load()">Back to now</button>
        </form>

        <div class="scrub-status">
          <span class="cl-pill" [class.done]="shown()?.verified" [class.bad]="shown() && !shown()!.verified">
            {{ shown()?.verified ? 'Verified then' : 'Not verified then' }}
          </span>
          <span class="u-tiny u-mute">{{ chainLine() }}</span>
        </div>

        <!-- How this moment differs from now, using the tested live.ts reducer. -->
        @if (diff(); as d) {
          <div class="scrub-diff">
            @if (d.added.length || d.changed.length || d.stopped.length) {
              <p class="u-tiny u-mute"><strong>Since {{ dateLabel() }}:</strong></p>
              <ul class="cl-list">
                @for (ref of d.added; track ref) {
                  <li class="u-tiny"><span class="cl-pill done">New</span> {{ labelFor(ref) }}</li>
                }
                @for (ref of d.changed; track ref) {
                  <li class="u-tiny"><span class="cl-pill info">Changed</span> {{ labelFor(ref) }}</li>
                }
                @for (ref of d.stopped; track ref) {
                  <li class="u-tiny"><span class="cl-pill bad">Gone now</span> {{ labelFor(ref) }}</li>
                }
              </ul>
            } @else {
              <p class="u-tiny u-mute">The plan was the same as it is now at this moment.</p>
            }
          </div>
        }

        <h4 class="u-mute" style="margin-top: var(--cl-sp-5);">Plan as it was — {{ dateLabel() }}</h4>
        @if (shown() && shown()!.state.items.length) {
          <div class="cl-rows">
            @for (it of shown()!.state.items; track it.ref) {
              <div class="cl-item" [attr.data-kind]="it.kind">
                <div class="u-between">
                  <span class="who">{{ it.person || 'Unassigned' }}</span>
                  <span class="meta">
                    <span class="cl-pill">{{ labelKind(it) }}</span>
                    <span class="cl-pill">{{ labelStatus(it) }}</span>
                  </span>
                </div>
                <!-- Server's verbatim replay of the recorded span; never rewritten here. -->
                <p class="u-mute">{{ it.text }}</p>
              </div>
            }
          </div>
        } @else {
          <p class="cl-note">No care items existed at this moment.</p>
        }
      } @else if (!busy()) {
        <p class="cl-note">No ledger history yet. Capture a visit to start the chain.</p>
      }
    </div>
  `,
  styles: [`
    .scrub-row { display: flex; flex-wrap: wrap; align-items: center; gap: var(--cl-sp-3); margin-top: var(--cl-sp-4); }
    .scrub-row .cl-label { margin: 0; }
    .cl-range { flex: 1; min-width: 10rem; accent-color: var(--cl-accent); height: var(--cl-target); }
    .cl-range-when { font-weight: 600; min-width: 12rem; }
    .scrub-form { display: flex; flex-wrap: wrap; align-items: flex-end; gap: var(--cl-sp-3); margin-top: var(--cl-sp-4); }
    .scrub-status { display: flex; flex-wrap: wrap; align-items: center; gap: var(--cl-sp-3); margin-top: var(--cl-sp-4); }
  `],
})
export class LedgerScrubberComponent implements OnInit {
  readonly session = inject(SessionService);

  readonly now = signal<LedgerSnapshot | null>(null);
  readonly shown = signal<LedgerSnapshot | null>(null);
  readonly idx = signal(0);
  readonly isNow = signal(true);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly manual = signal('');

  ngOnInit(): void {
    void this.load();
  }

  /** Distinct event times on the chain, oldest first — the discrete moments the slider walks. */
  readonly stops = computed<number[]>(() => {
    const n = this.now();
    if (!n) return [];
    const uniq = new Set<number>();
    for (const r of n.history) uniq.add(r.at);
    return [...uniq].sort((a, b) => a - b);
  });

  async load(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    const r = await this.session.ledgerAt(null);
    this.busy.set(false);
    if (r.error || !r.snapshot) {
      this.error.set(r.error ?? 'Could not read the ledger.');
      return;
    }
    this.now.set(r.snapshot);
    this.shown.set(r.snapshot);
    this.isNow.set(true);
    this.idx.set(this.stops().length);
  }

  onSlide(raw: string): void {
    const v = Math.max(0, Math.min(this.stops().length, Number(raw) | 0));
    this.idx.set(v);
    if (v >= this.stops().length) {
      this.jumpToNow();
      return;
    }
    void this.fetchUntil(String(this.stops()[v]));
  }

  jumpToNow(): void {
    const n = this.now();
    if (!n) return;
    this.isNow.set(true);
    this.error.set(null);
    this.idx.set(this.stops().length);
    this.shown.set(n);
  }

  onManualJump(e: Event): void {
    e.preventDefault();
    const raw = this.manual().trim();
    if (!raw) {
      void this.load();
      return;
    }
    void this.fetchUntil(raw);
  }

  private async fetchUntil(until: string): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    const r = await this.session.ledgerAt(until);
    this.busy.set(false);
    if (r.error || !r.snapshot) {
      // A bad date surfaces the server's real 400 message verbatim — nothing is invented.
      this.error.set(r.error ?? 'Could not read the ledger.');
      return;
    }
    this.isNow.set(false);
    this.shown.set(r.snapshot);
  }

  readonly dateLabel = computed(() => {
    if (this.isNow()) return 'Now';
    const s = this.shown();
    if (!s) return '—';
    const d = new Date(s.as_of);
    return Number.isNaN(d.getTime())
      ? s.as_of
      : d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  });

  readonly valueText = computed(() => {
    const s = this.shown();
    if (!s) return '';
    return ledgerValueText(s.as_of, s.state.items.length);
  });

  readonly chainLine = computed(() => {
    const s = this.shown();
    if (!s) return '';
    if (this.isNow()) return `Whole chain · ${s.chain_events} events`;
    return `Chain had ${s.events} of ${s.chain_events} events at this moment`;
  });

  readonly diff = computed(() => {
    const past = this.shown();
    const live = this.now();
    if (this.isNow() || !past || !live) return null;
    return diffLedgerSnapshot(past.state.items, live.state.items);
  });

  labelKind(it: LedgerItemSnapshot): string {
    return kindLabel(it.kind);
  }

  labelStatus(it: LedgerItemSnapshot): string {
    return STATUS_LABEL[it.status] ?? it.status;
  }

  /** Resolve a diff ref to a readable label from the server's own fields — never the raw id. */
  labelFor(ref: string): string {
    const live = this.now()?.state.items.find((i) => i.ref === ref);
    const past = this.shown()?.state.items.find((i) => i.ref === ref);
    const it = live ?? past;
    if (!it) return 'Care item';
    return `${it.person || 'Unassigned'} · ${kindLabel(it.kind)} · ${STATUS_LABEL[it.status] ?? it.status}`;
  }
}
