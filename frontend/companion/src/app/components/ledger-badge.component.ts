import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import type { LedgerResult } from '@careloop/client';
import { ledgerDetail } from '../display';

@Component({
  selector: 'cl-ledger-badge',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (ledger(); as l) {
      <span class="cl-pill" [class.done]="l.ok" [class.bad]="!l.ok">
        {{ l.ok ? 'Ledger intact' : 'Ledger broken' }}
      </span>
      <span class="u-tiny u-mute">{{ detail() }}</span>
    } @else {
      <span class="cl-pill">Ledger not checked</span>
    }
  `,
  styles: [`:host { display: inline-flex; gap: var(--cl-sp-2); align-items: center; }`],
})
export class LedgerBadgeComponent {
  readonly ledger = input<LedgerResult | null>(null);
  readonly detail = () => ledgerDetail(this.ledger());
}
