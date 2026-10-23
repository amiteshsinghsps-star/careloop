import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { SessionService } from './session.service';
import { TogglesComponent } from './components/toggles.component';
import { ToastsComponent } from './components/toasts.component';

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet, TogglesComponent, ToastsComponent],
  template: `
    <a class="skip-link" href="#main">Skip to plan</a>
    <div class="cl-shell">
      <header class="cl-nav">
        <a class="brand" routerLink="/plan"><span class="brand-mark" aria-hidden="true"></span> CareLoop Home</a>
        <nav aria-label="Primary">
          <a href="/simulator">Voice surface</a>
          <a href="/judge">Judge</a>
          @if (session.signedIn()) {
            <a routerLink="/plan">Plan</a>
            <button type="button" class="cl-btn quiet sm" (click)="signOut()">Sign out</button>
          } @else {
            <a routerLink="/login">Sign in</a>
          }
          <cl-toggles />
        </nav>
      </header>

      <main id="main" class="cl-main">
        <div class="cl-wrap">
          <router-outlet />
        </div>
      </main>

      <footer class="cl-wrap">
        <p class="cl-disclaimer">
          CareLoop is a care-plan aid, not a doctor. Doses shown here come from your clinician's own words — confirm any
          change with them. The voice surface deliberately shows no dosage numbers.
        </p>
      </footer>
    </div>
    <cl-toasts />
  `,
})
export class AppComponent {
  readonly session = inject(SessionService);
  private readonly router = inject(Router);

  async signOut(): Promise<void> {
    await this.session.signOut();
    await this.router.navigate(['/login']);
  }
}
