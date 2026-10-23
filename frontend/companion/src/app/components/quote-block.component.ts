import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

interface Segment { text: string; hl: boolean; }

@Component({
  selector: 'cl-quote-block',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <figure class="cl-quote">
      @if (segments(); as segs) {
        <div class="src-body">
          @for (s of segs; track $index) {
            @if (s.hl) { <mark>{{ s.text }}</mark> } @else { <span>{{ s.text }}</span> }
          }
        </div>
        <figcaption class="src">
          Clinician's own words · highlighted where the item came from
          @if (source()) { · {{ source() }} }
        </figcaption>
      } @else {
        <blockquote>&ldquo;{{ quote() }}&rdquo;</blockquote>
        <figcaption class="src">
          Extracted quote only ·
          the recording text was not kept — only the extracted quote is stored
        </figcaption>
      }
    </figure>
  `,
  styles: [`
    .src-body { white-space: pre-wrap; font-style: normal; font-family: var(--cl-font); font-size: var(--cl-text-base); }
    blockquote { margin: 0; }
  `],
})
export class QuoteBlockComponent {
  readonly quote = input.required<string>();
  readonly transcript = input<string | null>(null);
  readonly qstart = input<number | null>(null);
  readonly qend = input<number | null>(null);
  readonly source = input('');

  readonly segments = computed<Segment[] | null>(() => {
    const t = this.transcript();
    if (!t) return null;
    const qs = this.qstart();
    const qe = this.qend();
    if (qs != null && qe != null && qe > qs && qs >= 0 && qe <= t.length) {
      return [
        { text: t.slice(0, qs), hl: false },
        { text: t.slice(qs, qe), hl: true },
        { text: t.slice(qe), hl: false },
      ].filter((s) => s.text.length > 0);
    }
    return [{ text: t, hl: false }];
  });
}
