import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { SessionService } from '../session.service';

@Component({
  selector: 'cl-toasts',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="cl-toast" aria-live="polite" aria-atomic="true">
      @if (session.toast(); as msg) {
        <div class="cl-toastcard u-between" role="status">
          <span>{{ msg }}</span>
          <button type="button" class="cl-btn quiet sm" (click)="session.dismissToast()">Dismiss</button>
        </div>
      }
    </div>
  `,
})
export class ToastsComponent {
  readonly session = inject(SessionService);
}
