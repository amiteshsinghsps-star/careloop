import { ChangeDetectionStrategy, Component, ElementRef, computed, effect, inject, signal, viewChild } from '@angular/core';
import { KINDS, kindLabel } from '@careloop/ui';
import type { CareItem } from '@careloop/client';
import { SessionService, type DeleteSummary } from '../session.service';
import { ItemCardComponent } from '../components/item-card.component';
import { LedgerBadgeComponent } from '../components/ledger-badge.component';
import { KnowsPanelComponent } from '../components/knows-panel.component';
import { CapturePanelComponent } from '../components/capture-panel.component';
import { VisitsPanelComponent } from '../components/visits-panel.component';
import { PeoplePanelComponent } from '../components/people-panel.component';
import { ExportPanelComponent } from '../components/export-panel.component';
import { AdherenceViewComponent } from '../components/adherence-view.component';
import { LedgerScrubberComponent } from '../components/ledger-scrubber.component';
import { DangerPanelComponent } from '../components/danger-panel.component';

type Tab = 'plan' | 'capture' | 'adherence' | 'history' | 'knows' | 'visits' | 'people' | 'export' | 'danger';
const ALL = '__all';

@Component({
  selector: 'cl-plan',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ItemCardComponent, LedgerBadgeComponent, KnowsPanelComponent, CapturePanelComponent,
    VisitsPanelComponent, PeoplePanelComponent, ExportPanelComponent, AdherenceViewComponent,
    LedgerScrubberComponent, DangerPanelComponent,
  ],
  template: `
    <section class="cl-panel">
      <div class="u-between">
        <h2 style="margin:0;">Care plan</h2>
        <div class="u-flex" style="flex-wrap: wrap;">
          <span class="cl-pill">{{ peopleCount() }} people</span>
          <span class="cl-pill info">{{ pendingCount() }} to do</span>
          @if (session.fullDosesVisible()) {
            <span class="cl-pill done">Full doses visible</span>
          } @else {
            <span class="cl-pill warn">Doses limited</span>
          }
          <cl-ledger-badge [ledger]="session.ledger()" />
        </div>
      </div>
      <p class="cl-note">Signed in to this household. This is the only surface that shows full doses and the
        clinician's exact words.</p>

      <p class="u-tiny u-mute" [hidden]="!session.busy()" aria-live="polite">Working…</p>

      <div #errBox tabindex="-1" class="cl-errorbox" style="margin-top: var(--cl-sp-3);"
           [hidden]="!error()" role="alert" aria-live="assertive">{{ error() }}</div>
    </section>

    <div class="cl-tabs" role="tablist" aria-label="Care plan sections">
      @for (t of tabs; track t.id) {
        <button type="button" role="tab" [attr.aria-selected]="tab() === t.id"
                (click)="goTab(t.id)">{{ t.label }}</button>
      }
    </div>

    @if (tab() === 'plan') {
      <section class="cl-panel">
        <h3>Filter the plan</h3>
        <div class="cl-filters" role="group" aria-label="Filter by person">
          <button type="button" class="cl-chip" [attr.aria-pressed]="fPerson() === ALL" (click)="fPerson.set(ALL)">Everyone</button>
          @for (p of peopleNames(); track p) {
            <button type="button" class="cl-chip" [attr.aria-pressed]="fPerson() === p" (click)="fPerson.set(p)">{{ p }}</button>
          }
        </div>
        <div class="cl-filters" style="margin-top: var(--cl-sp-3);" role="group" aria-label="Filter by kind">
          <button type="button" class="cl-chip" [attr.aria-pressed]="fKind() === ALL" (click)="fKind.set(ALL)">All kinds</button>
          @for (k of kinds; track k) {
            <button type="button" class="cl-chip" [attr.aria-pressed]="fKind() === k" (click)="fKind.set(k)">{{ labelFor(k) }}</button>
          }
        </div>

        <div class="cl-rows" style="margin-top: var(--cl-sp-5);">
          @if (filtered().length) {
            @for (it of filtered(); track it.ref) {
              <cl-item-card [item]="it" [selected]="selectedRef() === it.ref" [busy]="session.busy()"
                (select)="onSelect($event)" (mark)="onMark($event)" (confirm)="onConfirm($event)"
                (askDelete)="onAskDelete($event)" />
            }
          } @else {
            <p class="cl-note">No items yet. Capture a visit to build the plan.</p>
          }
        </div>
      </section>
    }

    @if (tab() === 'capture') {
      <cl-capture-panel [people]="peopleNames()" (extracted)="onExtracted($event)" />
    }

    @if (tab() === 'adherence') {
      <cl-adherence-view />
    }

    @if (tab() === 'history') {
      <cl-ledger-scrubber />
    }

    @if (tab() === 'knows') {
      <cl-knows-panel [item]="selectedItem()" [transcript]="transcript()" [ledger]="session.ledger()" />
    }

    @if (tab() === 'visits') {
      <cl-visits-panel [visits]="session.visits()" />
    }

    @if (tab() === 'people') {
      <cl-people-panel />
    }

    @if (tab() === 'export') {
      <cl-export-panel />
    }

    @if (tab() === 'danger') {
      <cl-danger-panel (erased)="onErased()" />
    }

    @if (deleteTarget(); as target) {
      <div class="cl-backdrop" (click)="cancelDelete()">
        <div class="cl-dialog" role="alertdialog" aria-modal="true" aria-labelledby="del-title" (click)="$event.stopPropagation()">
          <h3 id="del-title">Delete this item?</h3>
          @if (deleteSummary(); as s) {
            <p class="u-mute">Reading it back to you before removing:</p>
            <blockquote class="cl-quote">&ldquo;{{ s.quote }}&rdquo;</blockquote>
            <p class="u-tiny u-mute">{{ s.person }} · {{ labelFor(s.kind) }}</p>
          }
          <div class="u-flex" style="margin-top: var(--cl-sp-4);">
            <button type="button" class="cl-btn danger" [disabled]="session.busy()" (click)="confirmDelete()">Yes, delete</button>
            <button type="button" class="cl-btn ghost" #keepBtn (click)="cancelDelete()">Keep it</button>
          </div>
        </div>
      </div>
    }
  `,
})
export class PlanComponent {
  readonly session = inject(SessionService);

  readonly tabs: { id: Tab; label: string }[] = [
    { id: 'plan', label: 'Plan' },
    { id: 'capture', label: 'Capture a visit' },
    { id: 'adherence', label: 'Adherence' },
    { id: 'history', label: 'History' },
    { id: 'knows', label: 'How CareLoop knows' },
    { id: 'visits', label: 'Visits' },
    { id: 'people', label: 'People' },
    { id: 'export', label: 'Export' },
    { id: 'danger', label: 'Danger zone' },
  ];
  readonly kinds = KINDS;
  readonly ALL = ALL;

  readonly tab = signal<Tab>('plan');
  readonly fPerson = signal<string>(ALL);
  readonly fKind = signal<string>(ALL);
  readonly selectedRef = signal<string | null>(null);
  readonly transcript = signal<string | null>(null);
  readonly deleteTarget = signal<CareItem | null>(null);
  readonly deleteSummary = signal<DeleteSummary | null>(null);

  readonly busy = this.session.busy;
  readonly error = this.session.error;
  readonly peopleCount = computed(() => this.session.people().length);
  readonly pendingCount = this.session.pendingCount;

  readonly peopleNames = computed(() => this.session.people().map((p) => p.name));
  readonly filtered = computed(() =>
    this.session.items().filter((i) =>
      (this.fPerson() === ALL || i.person === this.fPerson()) &&
      (this.fKind() === ALL || i.kind === this.fKind())));
  readonly selectedItem = computed(() =>
    this.session.items().find((i) => i.ref === this.selectedRef()) ?? null);

  private readonly errBox = viewChild<ElementRef>('errBox');

  constructor() {
    effect(() => {
      const message = this.error();
      if (message) this.errBox()?.nativeElement.focus();
    });
  }

  labelFor(kind: string): string {
    return kindLabel(kind);
  }

  goTab(t: Tab): void {
    this.tab.set(t);
  }

  onSelect(item: CareItem): void {
    this.selectedRef.set(item.ref);
    this.tab.set('knows');
  }

  async onExtracted(transcript: string): Promise<void> {
    this.transcript.set(transcript);
    await this.session.refresh();
    this.tab.set('plan');
  }

  onMark(item: CareItem): void {
    void this.session.mark(item);
  }

  onConfirm(item: CareItem): void {
    void this.session.confirmItem(item.ref);
  }

  async onAskDelete(item: CareItem): Promise<void> {
    const summary = await this.session.previewDelete(item.ref);
    this.deleteTarget.set(item);
    this.deleteSummary.set(summary);
  }

  async confirmDelete(): Promise<void> {
    const target = this.deleteTarget();
    if (!target) return;
    await this.session.deleteItem(target.ref);
    this.cancelDelete();
  }

  cancelDelete(): void {
    this.deleteTarget.set(null);
    this.deleteSummary.set(null);
  }

  onErased(): void {
    this.selectedRef.set(null);
    this.transcript.set(null);
  }
}
