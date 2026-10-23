import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import type { Visit } from '@careloop/client';
import { formatTime } from '../display';

@Component({
  selector: 'cl-visits-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="cl-panel">
      <h3>Visits</h3>
      <p class="cl-note">Each visit is fingerprinted by a truncated hash of its transcript. The raw transcript is not
        stored — only the extracted quotes are.</p>
      @if (visits().length) {
        <table class="cl-table">
          <caption class="sr-only">Visit log</caption>
          <thead>
            <tr>
              <th scope="col">Visit</th>
              <th scope="col">Person</th>
              <th scope="col">Transcript hash</th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            @for (v of visits(); track v.id) {
              <tr>
                <td>{{ v.id }}</td>
                <td>{{ v.person }}</td>
                <td class="mono-hash">{{ v.hash }}</td>
                <td>{{ when(v) }}</td>
              </tr>
            }
          </tbody>
        </table>
      } @else {
        <p class="cl-note">No visits recorded yet.</p>
      }
    </div>
  `,
})
export class VisitsPanelComponent {
  readonly visits = input<Visit[]>([]);
  when(v: Visit): string {
    return formatTime(v.at);
  }
}
