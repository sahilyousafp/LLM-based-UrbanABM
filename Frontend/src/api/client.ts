// API client — wraps fetch to the two backends.
// URL config is set at boot from persisted state / configStore.
export const apiConfig = {
  mapServerUrl: 'http://127.0.0.1:8000',
  labServerUrl: 'http://127.0.0.1:8100',
};

export const api = {
  get map(): string { return apiConfig.mapServerUrl.replace(/\/+$/, ''); },
  get lab(): string { return apiConfig.labServerUrl.replace(/\/+$/, ''); },

  async _fetch(base: string, path: string, opts: RequestInit = {}): Promise<unknown> {
    const url = base + path;
    const res = await fetch(url, opts);
    if (!res.ok) {
      const ct = res.headers.get('content-type') || '';
      let detail = '';
      try {
        if (ct.includes('application/json')) {
          const body = await res.json() as { error?: string; detail?: unknown };
          const d = body.error || body.detail;
          detail = Array.isArray(d)
            ? d.map((e: { msg?: string; loc?: unknown[] }) =>
                `${(e.loc || []).slice(-1)[0] ?? 'field'}: ${e.msg ?? e}`).join('; ')
            : typeof d === 'string' ? d : '';
        } else {
          detail = await res.text();
        }
      } catch { /* ignore body parse errors */ }
      throw new Error(detail || `${res.status} ${res.statusText}`);
    }
    const ct = res.headers.get('content-type') || '';
    return ct.includes('application/json') ? res.json() : res.text();
  },

  m<T = unknown>(path: string, opts?: RequestInit): Promise<T> {
    return this._fetch(this.map, path, opts) as Promise<T>;
  },

  l<T = unknown>(path: string, opts?: RequestInit): Promise<T> {
    return this._fetch(this.lab, path, opts) as Promise<T>;
  },

  postJSON<T = unknown>(base: string, path: string, body: unknown): Promise<T> {
    return this._fetch(base, path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }) as Promise<T>;
  },
};
