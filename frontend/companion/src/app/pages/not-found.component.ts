import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'cl-not-found',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
  template: `
    <div class="cl-panel">
      <h1>Page not found</h1>
      <p class="u-mute">That page isn’t part of CareLoop Home.</p>
      <a class="cl-btn" routerLink="/plan">Back to the care plan</a>
    </div>
  `,
})
export class NotFoundComponent {}
