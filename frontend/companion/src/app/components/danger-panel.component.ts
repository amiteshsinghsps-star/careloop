import { ChangeDetectionStrategy, Component, inject, output, signal } from '@angular/core';
import { SessionService } from '../session.service';

@Component({
  selector: 'cl-danger-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="cl-panel">
      <h3>Danger zone</h3>
      <div class="cl-callout bad">
        <strong>Erase everything.</strong>
        <p>This crypto-shreds the whole household cell — every item, quote, visit and person, along with the key that
          decrypts them. It cannot be undone. The voice surface and any exported sheets you kept are unaffected.</p>
      </div>

      @if (receipt()) {
        <div class="cl-callout" role="status" aria-live="polite">
          <strong>Erased.</strong>
          <p>Your erasure receipt is <code>{{ receipt() }}</code>. Keep it if you need to prove deletion was requested.</p>
        </div>
      } @else {
        <div class="cl-field" style="margin-top: var(--cl-sp-4);">
          <label for="erase-ack">Type <strong>ERASE</strong> to confirm</label>
          <input id="erase-ack" class="cl-input" #ackInput autocomplete="off"
                 [value]="ack()" (input)="ack.set(ackInput.value)" (keyup)="onKey($event)" />
        </div>
        <div class="u-flex">
          <button type="button" class="cl-btn danger" [disabled]="ack().toUpperCase() !== 'ERASE' || busy()"
                  (click)="erase()">
            {{ busy() ? 'Erasing…' : 'Erase this household' }}
          </button>
          <span class="u-tiny u-mute">Requires the exact word ERASE.</span>
        </div>
      }
    </div>
  `,
})
export class DangerPanelComponent {
  readonly session = inject(SessionService);
  readonly erased = output<void>();
  readonly ack = signal('');
  readonly receipt = signal<string | null>(null);
  readonly busy = this.session.busy;

  onKey(e: KeyboardEvent): void {
    if (e.key === 'Enter') {
      e.preventDefault();
      this.erase();
    }
  }

  async erase(): Promise<void> {
    if (this.ack().toUpperCase() !== 'ERASE') {
      this.session.setError('Type ERASE to confirm deleting everything.');
      return;
    }
    const res = await this.session.eraseAll();
    if (!res) return;
    const r = res['receipt'];
    this.receipt.set(typeof r === 'string' ? r : 'erased');
    this.ack.set('');
    this.erased.emit();
  }
}
