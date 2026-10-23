import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { NgClass } from '@angular/common';
import type { CareItem } from '@careloop/client';
import { kindLabel } from '@careloop/ui';
import { confidencePct, scheduleLine, statusPill, statusText } from '../display';

@Component({
  selector: 'cl-item-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NgClass],
  template: `
    <article class="cl-item" [attr.data-kind]="item().kind" [attr.aria-current]="selected() ? 'true' : null">
      <header class="u-between">
        <span class="who">{{ item().person }}</span>
        <span class="meta">
          <span class="cl-pill">{{ kind() }}</span>
          <span class="cl-pill" [ngClass]="statusCls()">{{ status() }}</span>
          @if (item().needs_confirm) {
            <span class="cl-pill warn">Confirm with clinician</span>
          }
        </span>
      </header>

      @if (schedule()) {
        <p class="u-mute"><strong>When:</strong> {{ schedule() }}</p>
      }

      <!-- This is the dose-bearing surface: full, unmasked clinician text. -->
      <p class="dose-text">{{ item().text }}</p>
      @if (item().quote) {
        <blockquote class="cl-quote">&ldquo;{{ item().quote }}&rdquo;</blockquote>
      }

      <div class="u-flex">
        <span class="u-tiny u-mute">Confidence {{ pct() }}%</span>
        <span class="cl-bar" role="img" [attr.aria-label]="'Confidence ' + pct() + ' percent'">
          <i [style.width.%]="pct()"></i>
        </span>
      </div>

      <div class="actions">
        <button type="button" class="cl-btn ghost sm" (click)="select.emit(item())">
          How CareLoop knows
        </button>
        @if (item().status === 'pending') {
          <button type="button" class="cl-btn sm" [disabled]="busy()" (click)="mark.emit(item())">Done</button>
        } @else if (item().status === 'done') {
          <span class="cl-pill done">Marked done</span>
        }
        @if (item().needs_confirm) {
          <button type="button" class="cl-btn quiet sm" [disabled]="busy()" (click)="confirm.emit(item())">
            Confirm
          </button>
        }
        @if (item().status !== 'deleted') {
          <button type="button" class="cl-btn danger ghost sm" [disabled]="busy()"
                  (click)="askDelete.emit(item())">Delete</button>
        }
      </div>
    </article>
  `,
  styles: [`.dose-text { font-size: var(--cl-text-lg); color: var(--cl-ink); margin: var(--cl-sp-1) 0; }`],
})
export class ItemCardComponent {
  readonly item = input.required<CareItem>();
  readonly selected = input(false);
  readonly busy = input(false);

  readonly select = output<CareItem>();
  readonly mark = output<CareItem>();
  readonly confirm = output<CareItem>();
  readonly askDelete = output<CareItem>();

  private readonly it = this.item;
  readonly kind = computed(() => kindLabel(this.it().kind));
  readonly status = computed(() => statusText(this.it()));
  readonly statusCls = computed(() => statusPill(this.it()));
  readonly schedule = computed(() => scheduleLine(this.it().schedule));
  readonly pct = computed(() => confidencePct(this.it()));
}
