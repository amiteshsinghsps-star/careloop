import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { ROLE_LABEL } from '@careloop/ui';
import { SessionService, type Person } from '../session.service';

const ROLES = ['caregiver', 'elder', 'viewer'] as const;

@Component({
  selector: 'cl-people-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="cl-panel">
      <h3>People in this household</h3>
      @if (session.people().length) {
        <table class="cl-table">
          <caption class="sr-only">Household members</caption>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Role</th>
              <th scope="col">Also known as</th>
            </tr>
          </thead>
          <tbody>
            @for (p of session.people(); track p.name) {
              <tr>
                <td>{{ p.name }}</td>
                <td>{{ roleOf(p) }}</td>
                <td>{{ p.aliases && p.aliases.length ? p.aliases.join(', ') : '—' }}</td>
              </tr>
            }
          </tbody>
        </table>
      } @else {
        <p class="cl-note">No people yet. Add the person this care plan is about.</p>
      }

      <form class="u-stack" (submit)="add($event, nameInput, roleSel, aliasInput)">
        <div class="cl-field">
          <label for="p-name">Name</label>
          <input id="p-name" class="cl-input" #nameInput autocomplete="off" required />
        </div>
        <div class="cl-field">
          <label for="p-role">Role</label>
          <select id="p-role" class="cl-select" #roleSel>
            @for (r of roles; track r) {
              <option [value]="r">{{ labels[r] }}</option>
            }
          </select>
        </div>
        <div class="cl-field">
          <label for="p-alias">Other names / nicknames (comma separated)</label>
          <input id="p-alias" class="cl-input" #aliasInput placeholder="Papa, Dad, Ramesh" />
        </div>
        <button type="submit" class="cl-btn" [disabled]="busy()">Add person</button>
      </form>
    </div>
  `,
})
export class PeoplePanelComponent {
  readonly session = inject(SessionService);
  readonly roles = ROLES;
  readonly labels = ROLE_LABEL as Record<string, string>;
  readonly busy = this.session.busy;

  roleOf(p: Person): string {
    return this.labels[p.role] ?? p.role;
  }

  async add(e: Event, nameInput: HTMLInputElement, roleSel: HTMLSelectElement, aliasInput: HTMLInputElement): Promise<void> {
    e.preventDefault();
    const name = nameInput.value.trim();
    const role = roleSel.value || 'elder';
    const aliases = aliasInput.value.split(',').map((a) => a.trim()).filter((a) => a.length > 0);
    if (!name) {
      this.session.setError('Please give the person a name.');
      return;
    }
    const ok = await this.session.addPerson(name, role, aliases);
    if (ok) {
      nameInput.value = '';
      aliasInput.value = '';
    }
  }
}
