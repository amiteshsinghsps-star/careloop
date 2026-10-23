import { ChangeDetectionStrategy, Component, DOCUMENT, inject, signal } from '@angular/core';

type Theme = 'light' | 'dark';
type TextSize = 'normal' | 'large';
const THEME_KEY = 'cl-theme';
const TEXT_KEY = 'cl-text';

@Component({
  selector: 'cl-toggles',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="u-flex" role="group" aria-label="Accessibility and appearance">
      <button
        type="button"
        class="cl-btn ghost sm"
        [attr.aria-pressed]="largeText()"
        (click)="toggleText()">
        {{ largeText() ? 'Text: large' : 'Text: normal' }}
      </button>
      <button
        type="button"
        class="cl-btn ghost sm"
        [attr.aria-pressed]="theme() === 'dark'"
        (click)="toggleTheme()">
        {{ theme() === 'dark' ? 'Dark' : 'Light' }}
      </button>
    </div>
  `,
})
export class TogglesComponent {
  private readonly doc = inject(DOCUMENT);
  readonly theme = signal<Theme>('light');
  readonly largeText = signal<boolean>(false);

  constructor() {
    const prefersDark = this.doc.defaultView?.matchMedia('(prefers-color-scheme: dark)').matches ?? false;
    const storedTheme = this.read(THEME_KEY);
    const next: Theme = storedTheme === 'light' || storedTheme === 'dark' ? storedTheme : prefersDark ? 'dark' : 'light';
    this.theme.set(next);
    this.largeText.set(this.read(TEXT_KEY) === 'large');
    this.apply();
  }

  toggleTheme(): void {
    this.theme.set(this.theme() === 'dark' ? 'light' : 'dark');
    this.write(THEME_KEY, this.theme());
    this.apply();
  }

  toggleText(): void {
    this.largeText.set(!this.largeText());
    this.write(TEXT_KEY, this.largeText() ? 'large' : 'normal');
    this.apply();
  }

  private apply(): void {
    const el = this.doc.documentElement;
    el.setAttribute('data-theme', this.theme());
    if (this.largeText()) el.setAttribute('data-text', 'large');
    else el.removeAttribute('data-text');
  }

  private read(key: string): string | null {
    try { return this.doc.defaultView?.localStorage.getItem(key) ?? null; } catch { return null; }
  }

  private write(key: string, value: string): void {
    try { this.doc.defaultView?.localStorage.setItem(key, value); } catch { /* private mode */ }
  }
}
