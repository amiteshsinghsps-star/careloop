/** Small formatting helpers shared by the simulator and the judge page. Nothing here
 *  invents data: every value comes from the server, we only make it readable. */
import type { MetricsResponse, ToolResult } from '@careloop/client';

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  return `${Math.round(ms)} ms`;
}

export function fmtCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return '$0.0000';
  return `$${usd.toFixed(4)}`;
}

export function fmtClock(at: number): string {
  const d = new Date(at);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return iso;
}

/** The sentence the device would speak: `content[0].text`, exactly as returned. */
export function spokenText(result: ToolResult | undefined): string {
  const first = result?.content?.find((c) => c.type === 'text');
  return (first?.text ?? '').trim();
}

/** Highest p95 across tools — the number the status bar shows against the 500 ms SLO. */
export function worstP95(metrics: MetricsResponse | null): number | null {
  if (!metrics) return null;
  const values = Object.values(metrics.latency_ms)
    .map((p) => p.p95)
    .filter((v): v is number => typeof v === 'number');
  return values.length ? Math.max(...values) : null;
}

export function errorTotal(errors: Record<string, number> | undefined): number {
  return Object.values(errors ?? {}).reduce((a, b) => a + b, 0);
}

export function truncated(text: string, max = 240): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/** Clipboard with a legacy fallback; resolves false when neither path is available. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the textarea path */
  }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('aria-hidden', 'true');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

export function downloadText(filename: string, text: string, mime = 'application/json'): void {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
