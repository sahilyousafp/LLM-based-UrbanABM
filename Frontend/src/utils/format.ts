export const fmtPct = (n: number | null | undefined): string =>
  (n === null || n === undefined || Number.isNaN(n)) ? '—' : `${Math.round(n * 100)}%`;

export function escapeHtml(s: unknown): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string)
  );
}
