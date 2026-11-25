export type Kind = 'pdf' | 'text' | 'docx' | 'image' | 'webpage' | 'note';
export type Mode = 'contextual' | 'semantic' | 'keyword';
export type Source = {
  id: number;
  title: string;
  kind: Kind;
  url: string;
  filename: string;
  source_date: string;
  created_at: string;
  sample_key: string | null;
  passage_count: number;
};
export type Passage = { id: number; page: number | null; text: string };
export type SourceDetail = Omit<Source, 'passage_count'> & { passages: Passage[] };
export type Result = Omit<Source, 'passage_count'> & {
  chunk_id: number; page: number | null; excerpt: string; score: number; similarity: number | null; match_basis?: string;
};
export type Report = {
  k: number; sources: number; query_count: number; indexing_ms: number;
  results: {
    mode: Mode; precision: number; recall: number; mrr: number; ndcg: number;
    median_ms: number; p95_ms: number; queries: { query: string; rank: number | null }[];
  }[];
};
export type Health = { model: string; model_ready: boolean; ocr_available: boolean };

export const kindLabels: Record<Kind, string> = {
  pdf: 'PDF', text: 'Text file', docx: 'Word document', image: 'Screenshot', webpage: 'Webpage', note: 'Note',
};
export const modeLabels: Record<Mode, string> = {
  contextual: 'Context-aware', semantic: 'Semantic', keyword: 'Keyword',
};

let activeAccount: string | null = null;
export function setActiveAccount(username: string | null) {
  activeAccount = username;
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  headers.set('X-Echo-Request', '1');
  if (activeAccount && !headers.has('X-Echo-Account')) headers.set('X-Echo-Account', activeAccount);
  const response = await fetch('/api' + path, { ...options, headers, cache: 'no-store' });
  if (response.status === 401 && activeAccount && !path.startsWith('/account')) {
    window.dispatchEvent(new Event('echo-session-ended'));
  }
  if (!response.ok) {
    const type = response.headers.get('Content-Type') || '';
    if (type.includes('application/json')) {
      const body = await response.json();
      if (typeof body.detail === 'string') throw new Error(body.detail);
      if (Array.isArray(body.detail)) {
        throw new Error(body.detail.map((issue: { msg: string }) => issue.msg).join(' '));
      }
    }
    if (response.status === 413) throw new Error('Files must be 16 MB or smaller.');
    throw new Error('The local ECHO server could not complete this request.');
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export function post<T>(path: string, data?: unknown, signal?: AbortSignal): Promise<T> {
  return api<T>(path, {
    method: 'POST', signal,
    headers: data === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
}

export function message(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong. Try again.';
}

export function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
    .format(new Date(value + 'T00:00:00'));
}
