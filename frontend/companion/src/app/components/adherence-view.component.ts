import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { STATUS_LABEL } from '@careloop/ui';
import { adherenceRowLabel, adherenceRows, adherenceTotal, donePercent } from '@careloop/client';
import type { AdherenceRow } from '@careloop/client';
import { SessionService } from '../session.service';

/** M16 adherence, drawn straight from the tested @careloop/client functions. The bars and the
 *  table render from the SAME rows, so a screen reader and the graphic can never disagree. When
 *  the plan does not carry enough done/pending data for a person (total === 0) we say so plainly
 *  instead of drawing a confident-looking empty bar. */
@Component({
  selector: 'cl-adherence-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="cl-panel">
      <h3>Adherence</h3>
      <p class="cl-note">How much of the plan is marked {{ statusDone() }} versus {{ statusTodo() }}, by person.
        Superseded or removed items are not counted. This is a count of what has been ticked off, not a
        measurement of whether a dose was actually taken.</p>

      <div class="legend" aria-hidden="true">
        <span class="key done"></span> {{ statusDone() }}
        <span class="key todo"></span> {{ statusTodo() }}
      </div>

      @if (rows().length === 0 || grandTotal().total === 0) {
        <p class="cl-note">Nothing has been recorded yet, so there is no adherence to report.</p>
      } @else {
        <div class="bars">
          @for (row of rows(); track row.person) {
            <div class="bar-row">
              <div class="bar-head">
                <span class="who">{{ row.person }}</span>
                <span class="u-tiny u-mute">{{ pct(row) }}%</span>
              </div>
              @if (row.total) {
                <div class="cl-bar" role="img" [attr.aria-label]="label(row)">
                  <i [style.width.%]="pct(row)"></i>
                </div>
              } @else {
                <p class="u-tiny u-mute">Nothing outstanding today — not counted.</p>
              }
            </div>
          }
          <div class="bar-row total">
            <div class="bar-head">
              <span class="who">Everyone</span>
              <span class="u-tiny u-mute">{{ totalPct() }}%</span>
            </div>
            <div class="cl-bar" role="img" [attr.aria-label]="totalLabel()">
              <i [style.width.%]="totalPct()"></i>
            </div>
          </div>
        </div>

        <!-- Text alternative: the identical numbers, always in the DOM for assistive tech. -->
        <table class="cl-table" style="margin-top: var(--cl-sp-5);">
          <caption class="sr-only">Adherence by person, as plain text</caption>
          <thead>
            <tr>
              <th scope="col">Person</th>
              <th scope="col">{{ statusDone() }}</th>
              <th scope="col">{{ statusTodo() }}</th>
              <th scope="col">Counted</th>
            </tr>
          </thead>
          <tbody>
            @for (row of rows(); track row.person) {
              <tr>
                <td>{{ row.person }}</td>
                <td>{{ row.done }}</td>
                <td>{{ row.pending }}</td>
                <td>{{ row.total }}</td>
              </tr>
            }
            <tr>
              <th scope="row">Everyone</th>
              <td>{{ grandTotal().done }}</td>
              <td>{{ grandTotal().pending }}</td>
              <td>{{ grandTotal().total }}</td>
            </tr>
          </tbody>
        </table>
      }
    </div>
  `,
  styles: [`
    .legend { display: flex; align-items: center; gap: var(--cl-sp-2); font-size: var(--cl-text-sm);
      color: var(--cl-ink-2); margin: var(--cl-sp-3) 0 var(--cl-sp-5); }
    .legend .key { width: 14px; height: 14px; border-radius: 4px; display: inline-block; }
    .legend .key.done { background: var(--cl-accent); }
    .legend .key.todo { background: var(--cl-surface-2); border: 1px solid var(--cl-line); }
    .bars { display: grid; gap: var(--cl-sp-4); }
    .bar-row .bar-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: var(--cl-sp-1); }
    .bar-row .who { font-weight: 650; }
    .bar-row.total { border-top: 1px dashed var(--cl-line); padding-top: var(--cl-sp-4); }
    @media (prefers-reduced-motion: reduce) { .cl-bar > i { transition: none; } }
  `],
})
export class AdherenceViewComponent {
  private readonly session = inject(SessionService);

  readonly rows = computed<AdherenceRow[]>(() => adherenceRows(this.session.items()));
  readonly grandTotal = computed(() => adherenceTotal(this.rows()));

  statusDone(): string {
    return STATUS_LABEL.done ?? 'Done';
  }

  statusTodo(): string {
    return STATUS_LABEL.pending ?? 'To do';
  }

  pct(row: { done: number; total: number }): number {
    return donePercent(row);
  }

  label(row: AdherenceRow): string {
    return adherenceRowLabel(row);
  }

  totalPct(): number {
    return donePercent(this.grandTotal());
  }

  totalLabel(): string {
    return adherenceRowLabel({ person: 'Everyone', ...this.grandTotal() });
  }
}
