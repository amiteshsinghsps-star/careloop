import { ChangeDetectionStrategy, Component, OnDestroy, inject, signal } from '@angular/core';
import { STATUS_LABEL, kindLabel } from '@careloop/ui';
import { buildCareSheet, canNativeShare } from '@careloop/client';
import type { SheetItem } from '@careloop/client';
import { SessionService } from '../session.service';
import { formatTime, scheduleLine, statusText } from '../display';

interface BlobLink { name: string; url: string; }

@Component({
  selector: 'cl-export-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="cl-panel">
      <h3>Export the care plan</h3>
      <p class="cl-note">The exported sheet is the dose-bearing version — it includes full amounts and timings, meant for
        a family member or the clinic, not for the voice surface. This view may show doses.</p>

      <div class="u-flex" style="flex-wrap: wrap; align-items: flex-end;">
        <div class="cl-field" style="margin:0;">
          <label for="exp-person">Who?</label>
          <select id="exp-person" class="cl-select" [value]="person()" (change)="person.set($any($event.target).value)">
            <option value="">Everyone in the household</option>
            @for (p of session.people(); track p.name) {
              <option [value]="p.name">{{ p.name }}</option>
            }
          </select>
        </div>
        <button type="button" class="cl-btn" [disabled]="busy()" (click)="generate()">
          {{ busy() ? 'Preparing…' : 'Prepare export' }}
        </button>
      </div>

      @if (markdown(); as md) {
        <div class="u-stack" style="margin-top: var(--cl-sp-4);">
          <h4 class="u-mute">Care plan (Markdown)</h4>
          <pre class="cl-md" tabindex="0" aria-label="Exported care plan markdown">{{ md }}</pre>
          <div class="u-flex" style="flex-wrap: wrap;">
            @for (link of links(); track link.name) {
              <a class="cl-btn ghost sm" [href]="link.url" [download]="link.name">{{ link.name }}</a>
            }
          </div>
          <p class="u-tiny u-mute">Links open a download. Leave this page to release the temporary files.</p>
        </div>
      } @else if (!busy()) {
        <p class="cl-note" style="margin-top: var(--cl-sp-4);">Nothing prepared yet.</p>
      }

      <hr style="margin: var(--cl-sp-5) 0; border: none; border-top: 1px solid var(--cl-line);" />

      <h4 class="u-mute" style="margin-top:0;">Print or share a care sheet</h4>
      <p class="cl-note">A plain, printable sheet built from the plan — full doses stay exactly as the server wrote
        them (including any “your prescribed amount” masking), and no internal ids appear.</p>
      <div class="u-flex" style="flex-wrap: wrap; margin-top: var(--cl-sp-3);">
        <button type="button" class="cl-btn quiet" (click)="printSheet()">Print care sheet</button>
        @if (canShare) {
          <button type="button" class="cl-btn ghost" (click)="shareSheet()">Share care sheet</button>
        }
      </div>
      @if (sheetError(); as err) {
        <p class="cl-note warn" role="status" aria-live="polite" style="margin-top: var(--cl-sp-3);">{{ err }}</p>
      }
    </div>
  `,
})
export class ExportPanelComponent implements OnDestroy {
  readonly session = inject(SessionService);
  readonly markdown = signal<string | null>(null);
  readonly links = signal<BlobLink[]>([]);
  readonly person = signal('');
  readonly sheetError = signal<string | null>(null);
  readonly busy = this.session.busy;
  /** Offer the Web Share sheet only where the platform actually exposes navigator.share. */
  readonly canShare = canNativeShare();

  async generate(): Promise<void> {
    this.revoke();
    this.markdown.set(null);
    const res = await this.session.exportSheet(this.person() || null);
    if (!res) return;
    const md = res.markdown ?? '';
    this.markdown.set(md);
    const built: BlobLink[] = [this.toBlob('careplan.md', md, 'text/markdown')];
    (res.ics ?? []).forEach((ics, i) => built.push(this.toBlob(`appointment-${i + 1}.ics`, ics, 'text/calendar')));
    this.links.set(built);
  }

  /** Map the plan (optionally one person) to the pure care-sheet formatter. Text/quote pass
   *  through verbatim — we never rewrite a dose or a masked amount. */
  private sheetText(): string {
    const who = this.person();
    const items = this.session.items().filter((i) => !who || i.person === who);
    const sheetItems: SheetItem[] = items.map((i) => ({
      person: i.person || 'Unassigned',
      kindLabel: kindLabel(i.kind),
      text: i.text,
      quote: i.quote,
      schedule: scheduleLine(i.schedule),
      recordedOn: formatTime(i.created_at),
      statusLabel: statusText(i) || STATUS_LABEL[i.status] || i.status,
    }));
    return buildCareSheet(sheetItems, {
      personFilter: who || null,
      generatedOn: new Date().toLocaleString(undefined,
        { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
    });
  }

  printSheet(): void {
    this.sheetError.set(null);
    const text = this.sheetText();
    const frame = document.createElement('iframe');
    frame.setAttribute('aria-hidden', 'true');
    frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;';
    document.body.appendChild(frame);
    const doc = frame.contentWindow?.document;
    if (!doc) {
      document.body.removeChild(frame);
      this.sheetError.set('Printing is not available in this browser.');
      return;
    }
    doc.open();
    doc.write('<!doctype html><html lang="en"><head><title>CareLoop care sheet</title>'
      + '<style>body{font-family:Georgia,serif;color:#111;margin:2rem}'
      + 'pre{white-space:pre-wrap;word-break:break-word;font:inherit;line-height:1.5}</style></head><body>');
    doc.write('</body></html>');
    doc.close();
    // textContent (not innerHTML) — the sheet is plain text; never interpret its characters as HTML.
    const pre = doc.createElement('pre');
    pre.textContent = text;
    doc.body.appendChild(pre);
    frame.contentWindow?.focus();
    frame.contentWindow?.print();
    setTimeout(() => document.body.removeChild(frame), 1000);
  }

  async shareSheet(): Promise<void> {
    this.sheetError.set(null);
    if (!canNativeShare() || !navigator.share) {
      this.sheetError.set('Sharing is not supported here — use Print instead.');
      return;
    }
    try {
      await navigator.share({ title: 'CareLoop care sheet', text: this.sheetText() });
    } catch {
      /* the caregiver dismissed the share sheet — nothing to report */
    }
  }

  private toBlob(name: string, content: string, type: string): BlobLink {
    const url = URL.createObjectURL(new Blob([content], { type }));
    return { name, url };
  }

  private revoke(): void {
    for (const l of this.links()) URL.revokeObjectURL(l.url);
    this.links.set([]);
  }

  ngOnDestroy(): void {
    this.revoke();
  }
}
