import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import type { CareItem, LedgerResult } from '@careloop/client';
import { kindLabel } from '@careloop/ui';
import { confidencePct, ledgerDetail, scheduleLine } from '../display';
import { QuoteBlockComponent } from './quote-block.component';

@Component({
  selector: 'cl-knows-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [QuoteBlockComponent],
  template: `
    <div class="cl-panel">
      @if (item(); as it) {
        <h3>How CareLoop knows — {{ it.person }}, {{ kind() }}</h3>

        <cl-quote-block
          [quote]="it.quote"
          [transcript]="transcript()"
          [qstart]="it.qstart ?? null"
          [qend]="it.qend ?? null"
          [source]="it.source" />

        <div class="cl-grid cols-2" style="margin-top: var(--cl-sp-4);">
          <div class="cl-rows">
            <div class="u-between"><span class="u-mute">Confidence</span><strong>{{ pct() }}%</strong></div>
            <div class="cl-bar" role="img" [attr.aria-label]="'Confidence ' + pct() + ' percent'">
              <i [style.width.%]="pct()"></i>
            </div>
            <div class="u-between"><span class="u-mute">Extraction engine</span><strong>{{ it.source }}</strong></div>
            <div class="u-between"><span class="u-mute">Schedule</span><strong>{{ sched() }}</strong></div>
            <div class="u-between"><span class="u-mute">Ledger</span><strong>{{ ledgerText() }}</strong></div>
          </div>
          <div class="cl-callout">
            <strong>Grounded, not generated.</strong>
            <p>Every line traces back to words the clinician actually said in this visit. Nothing here was invented
              to fill a gap — if the plan is unsure it says so.</p>
          </div>
        </div>

        <p class="cl-disclaimer">
          Dose policy: this companion view is the only place full doses appear. The voice surface (Alexa) shows
          <strong>no</strong> dosage numbers — Amazon health policy only permits dose content from an affiliated
          manufacturer, so it is masked there. Always confirm doses with your clinician.
        </p>
      } @else {
        <h3>How CareLoop knows</h3>
        <p class="cl-note">Choose “How CareLoop knows” on any item to see the clinician's exact words behind it.</p>
      }
    </div>
  `,
})
export class KnowsPanelComponent {
  readonly item = input<CareItem | null>(null);
  readonly transcript = input<string | null>(null);
  readonly ledger = input<LedgerResult | null>(null);

  readonly kind = computed(() => kindLabel(this.item()?.kind ?? null));
  readonly pct = computed(() => (this.item() ? confidencePct(this.item()!) : 0));
  readonly sched = computed(() => scheduleLine(this.item()?.schedule));
  readonly ledgerText = computed(() => ledgerDetail(this.ledger()));
}
