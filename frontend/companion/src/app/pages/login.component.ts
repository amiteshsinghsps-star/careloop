import { ChangeDetectionStrategy, Component, ElementRef, inject, signal, viewChild } from '@angular/core';
import { Router } from '@angular/router';
import { SessionService } from '../session.service';

type Mode = 'signin' | 'create';

@Component({
  selector: 'cl-login',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="cl-panel">
      <h1>CareLoop Home</h1>
      <p class="u-mute">The dose-bearing view of your family's care plan. Sign in with the household code your clinician
        gave you.</p>

      <div class="cl-tabs" role="tablist" aria-label="Choose">
        <button type="button" role="tab" [attr.aria-selected]="mode() === 'signin'" (click)="setMode('signin')">Sign in</button>
        <button type="button" role="tab" [attr.aria-selected]="mode() === 'create'" (click)="setMode('create')">Create a household</button>
      </div>

      <div #errBox tabindex="-1" class="cl-errorbox" [hidden]="!error()" role="alert" aria-live="polite">{{ error() }}</div>

      <form class="u-stack" style="margin-top: var(--cl-sp-4);" (submit)="submit($event)">
        <div class="cl-field">
          <label for="code">Household code</label>
          <input id="code" class="cl-input" #codeInput autocomplete="username" autocapitalize="characters"
                 [value]="code()" (input)="onCode(codeInput)" required maxlength="8" />
        </div>

        <div class="cl-field">
          <label for="pin">PIN</label>
          <input id="pin" class="cl-input" #pinInput type="password" inputmode="numeric" autocomplete="current-password"
                 [value]="pin()" (input)="pin.set(pinInput.value)" required />
        </div>

        @if (mode() === 'create') {
          <div class="cl-field">
            <label for="name">Your name</label>
            <input id="name" class="cl-input" #nameInput autocomplete="name" [value]="name()" (input)="name.set(nameInput.value)" required />
          </div>
          <label class="cl-check" for="consent">
            <input id="consent" type="checkbox" [checked]="consent()" (change)="consent.set(consentBox.checked)" #consentBox />
            <span>I agree that CareLoop may store this household's care items on this device. I can erase everything at
              any time.</span>
          </label>
        }

        <button type="submit" class="cl-btn block" [disabled]="busy()">
          {{ busy() ? 'Please wait…' : (mode() === 'signin' ? 'Sign in' : 'Create household') }}
        </button>
      </form>

      @if (mode() === 'signin') {
        <p class="cl-note" style="margin-top: var(--cl-sp-4);">
          Demo household — code <strong>CASA01</strong>, PIN <strong>481542</strong>.
        </p>
      }
    </div>
  `,
})
export class LoginComponent {
  private readonly session = inject(SessionService);
  private readonly router = inject(Router);

  readonly mode = signal<Mode>('signin');
  readonly code = signal('');
  readonly pin = signal('');
  readonly name = signal('');
  readonly consent = signal(false);
  readonly busy = this.session.busy;
  readonly error = this.session.error;

  private readonly errBox = viewChild<ElementRef>('errBox');

  constructor() {
    // A failed session probe (from the route guard) can leave a stray message; the login page starts clean.
    this.session.clearError();
  }

  setMode(m: Mode): void {
    this.mode.set(m);
    this.session.clearError();
  }

  onCode(el: HTMLInputElement): void {
    const up = el.value.toUpperCase();
    el.value = up;
    this.code.set(up);
  }

  async submit(e: Event): Promise<void> {
    e.preventDefault();
    const code = this.code().trim();
    const pin = this.pin().trim();
    if (this.mode() === 'create') {
      if (!this.consent()) {
        this.session.setError('Please confirm you agree before creating a household.');
        this.focusError();
        return;
      }
      const ok = await this.session.signUp(code, pin, this.name().trim() || 'you');
      if (ok) {
        await this.router.navigate(['/plan']);
      } else {
        this.focusError();
      }
      return;
    }
    const ok = await this.session.signIn(code, pin);
    if (ok) {
      await this.router.navigate(['/plan']);
    } else {
      this.focusError();
    }
  }

  private focusError(): void {
    // The error region is always in the DOM (hidden when empty), so it is queryable now.
    this.errBox()?.nativeElement.focus();
  }
}
