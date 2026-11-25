import { StrictMode, useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Archive, BookOpen, ChartNoAxesColumnIncreasing,
  ChevronDown, ChevronRight, CircleHelp, FileImage, FileText, FlaskConical, Globe2, HardDrive, LibraryBig,
  ListFilter, Plus, Search, Settings, ShieldCheck, Sparkles, SquarePen, Trash2, UploadCloud, X,
} from 'lucide-react';
import { api, formatDate, kindLabels, message, modeLabels, post } from './api';
import type { Health, Kind, Mode, Report, Result, Source, SourceDetail } from './api';
import './styles.css';
import { Account } from './Account';
import { StorageImport } from './StorageImport';

type Page = 'search' | 'library' | 'evaluation' | 'settings';
type Filters = { kind: Kind | ''; from: string; to: string };
const emptyFilters: Filters = { kind: '', from: '', to: '' };
const pages = {
  search: { label: 'Search', icon: Search },
  library: { label: 'Library', icon: LibraryBig },
  evaluation: { label: 'Evaluation', icon: ChartNoAxesColumnIncreasing },
  settings: { label: 'Settings', icon: Settings },
};
const prompts = [
  'Remember what I study',
  'Stay focused at work',
];

function pageFromHash(): Page {
  const value = location.hash.slice(1);
  return Object.hasOwn(pages, value) ? value as Page : 'search';
}

function SourceIcon({ kind }: { kind: Kind }) {
  const Icon = { pdf: FileText, text: FileText, docx: FileText, image: FileImage, webpage: Globe2, note: SquarePen }[kind];
  return <span className={'source-icon ' + kind}><Icon size={19} strokeWidth={1.9} /></span>;
}

function HighlightedExcerpt({ text, query }: { text: string; query: string }) {
  const words = new Set(query.toLowerCase().match(/[\p{L}\p{N}]+/gu) || []);
  const parts = text.split(/([\p{L}\p{N}]+)/u);
  const common = new Set(['the', 'and', 'for', 'how', 'can', 'what', 'from', 'that', 'with', 'find', 'this']);
  return <>{parts.map((part, index) => {
    const word = part.toLowerCase();
    return word.length > 2 && words.has(word) && !common.has(word) ? <mark key={index}>{part}</mark> : part;
  })}</>;
}

function SourceCard({ source, open }: { source: Source; open: () => void }) {
  return <button className="source-card" onClick={open}>
    <SourceIcon kind={source.kind} />
    <span className="catalog-title"><strong>{source.title}</strong>
      <small>{source.filename || kindLabels[source.kind]}</small></span>
    <time dateTime={source.source_date}>{formatDate(source.source_date)}</time>
    <ChevronRight size={16} aria-hidden="true" />
  </button>;
}

function SelectKind({ kind, change }: { kind: Filters['kind']; change: (kind: Filters['kind']) => void }) {
  return <div className="select-wrap">
    <select aria-label="Source type" value={kind} onChange={event => change(event.target.value as Filters['kind'])}>
      <option value="">All source types</option>
      {Object.entries(kindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select><ChevronDown size={15} aria-hidden="true" />
  </div>;
}

function Modal({ title, children, onClose, wide = false }: {
  title: string; children: ReactNode; onClose: () => void; wide?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);
  return <dialog ref={dialog} className={wide ? 'modal wide' : 'modal'} aria-labelledby="dialog-title"
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="modal-body">
      <header className="modal-header"><h2 id="dialog-title">{title}</h2>
        <button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={21} /></button>
      </header>
      {children}
    </section>
  </dialog>;
}

type ImportItem = {
  id: number; name: string; url: string;
  status: 'Ready' | 'Indexing…' | 'Indexed' | 'Already in library' | 'Failed';
  error: string;
} & ({ kind: 'file'; file: File } | { kind: 'note' | 'webpage'; content: string });

async function uploadItem(item: ImportItem, date: string, username: string, signal: AbortSignal) {
  const headers = { 'X-Echo-Account': username };
  if (item.kind !== 'file') {
    return api<{ created: boolean }>('/sources/text', { method: 'POST', signal,
      headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify({
      title: item.name, content: item.content, url: item.url, kind: item.kind, source_date: date || null,
    }) });
  }
  if (item.file.size > 16 * 1024 * 1024) throw new Error('This file exceeds 16 MB.');
  const form = new FormData();
  form.set('file', item.file);
  if (date) form.set('source_date', date);
  if (item.url) form.set('url', item.url);
  return api<{ created: boolean }>('/sources/import', { method: 'POST', body: form, headers, signal });
}

function ImportDialog({ onClose, onAdded, onIndexed, onStorage, health, username }: {
  username: string;
  onStorage: () => void;
  onClose: () => void; onAdded: (notice: string) => void; onIndexed: () => void; health: Health | null;
}) {
  const [tab, setTab] = useState<'file' | 'note' | 'webpage'>('file');
  const [items, setItems] = useState<ImportItem[]>([]);
  const nextId = useRef(0);
  const transfer = useRef<AbortController | null>(null);
  useEffect(() => () => { transfer.current?.abort(); }, []);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [url, setUrl] = useState('');
  const [date, setDate] = useState('');
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const pending = items.filter(item => item.status === 'Ready' || item.status === 'Failed');
  const completed = items.filter(item => item.status === 'Indexed' || item.status === 'Already in library').length;

  function addFiles(selection: FileList | null) {
    if (!selection?.length || working) return;
    const added: ImportItem[] = Array.from(selection, file => ({
      id: nextId.current++, kind: 'file', name: file.name, file, url: '', status: 'Ready', error: '',
    }));
    setItems(previous => [...previous, ...added]);
  }

  function updateItem(id: number, changes: Partial<Pick<ImportItem, 'url' | 'status' | 'error'>>) {
    setItems(previous => previous.map(item => item.id === id ? { ...item, ...changes } : item));
  }

  const hasDraft = tab !== 'file' && Boolean(title || content || (tab === 'webpage' && url));

  function queueDraft(): ImportItem {
    if (tab === 'file') throw new Error('Choose a note or webpage.');
    const item: ImportItem = { id: nextId.current++, kind: tab, name: title.trim(), content,
      url: tab === 'webpage' ? url.trim() : '', status: 'Ready', error: '' };
    setItems(previous => [...previous, item]);
    setTitle('');
    setContent('');
    setUrl('');
    return item;
  }

  async function importItems(batch: ImportItem[], total: number) {
    const controller = new AbortController();
    transfer.current = controller;
    let indexed = 0;
    let succeeded = 0;
    for (const item of batch) {
      if (controller.signal.aborted) return;
      updateItem(item.id, { status: 'Indexing…', error: '' });
      try {
        const { created } = await uploadItem(item, date, username, controller.signal);
        updateItem(item.id, { status: created ? 'Indexed' : 'Already in library' });
        if (created) indexed++;
        succeeded++;
      } catch (error) {
        if (controller.signal.aborted) return;
        updateItem(item.id, { status: 'Failed', error: message(error) });
      }
    }
    if (total === 1 && succeeded === 1) {
      onAdded(indexed ? 'Source indexed and ready to search.' : 'This source is already in your library.');
    } else if (indexed) {
      onIndexed();
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setWorking(true);
    setError('');
    try {
      const draft = hasDraft ? queueDraft() : null;
      setTab('file');
      await importItems(draft ? [...pending, draft] : pending, items.length + (draft ? 1 : 0));
    } catch (error) {
      setError(message(error));
    } finally {
      setWorking(false);
    }
  }

  return <Modal title="Add sources" onClose={() => { if (!working) onClose(); }}>
    <form className="modal-form" onSubmit={submit}>
      <div className="segmented import-tabs" aria-label="Import method">
        {([['file', 'Upload files'], ['note', 'Write a note'], ['webpage', 'Save a webpage']] as const).map(([value, label]) =>
          <button type="button" key={value} disabled={working} aria-pressed={tab === value}
            className={tab === value ? 'selected' : ''} onClick={() => { setTab(value); setError(''); }}>{label}</button>)}
      </div>
      {tab === 'file' ? <>
        <input ref={input} type="file" multiple disabled={working} className="sr-only" tabIndex={-1} aria-label="Files to upload"
          accept=".pdf,.txt,.md,.docx,.html,.htm,.png,.jpg,.jpeg,.webp"
          onChange={event => { addFiles(event.target.files); event.target.value = ''; }} />
        <button type="button" disabled={working} className={'drop-zone' + (dragging ? ' dragging' : '')}
          onClick={() => input.current?.click()} onDragOver={event => { event.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)} onDrop={event => {
            event.preventDefault(); setDragging(false);
            addFiles(event.dataTransfer.files);
          }}>
          <UploadCloud size={24} />
          <strong>{items.length ? 'Choose more files' : 'Choose files or drop them here'}</strong>
          <span>PDF, TXT, Markdown, DOCX, or HTML · 16 MB per file</span>
          {health?.ocr_available && <span>PNG, JPEG, and WebP screenshots are also supported</span>}
        </button>
        {!items.length && <button type="button" className="secondary" onClick={onStorage}><HardDrive size={17} /> Index device storage…</button>}
      </> : <>
        <label>Title<input required={hasDraft || !pending.length} maxLength={240} value={title} onChange={event => setTitle(event.target.value)} placeholder={tab === 'note' ? 'Title of your note' : 'Title of the webpage'} /></label>
        {tab === 'webpage' && <label>Original URL (optional)<input type="url" maxLength={2048} value={url} onChange={event => setUrl(event.target.value)} placeholder="https://example.com/article" /></label>}
        <label>{tab === 'note' ? 'Note' : 'Page text'}<textarea required={hasDraft || !pending.length} maxLength={1000000} rows={6}
          value={content} onChange={event => setContent(event.target.value)}
          placeholder={tab === 'note' ? 'Write or paste your note' : 'Paste the text you want to find later'} /></label>
      </>}
        {items.length > 0 && <>
          <p className="queue-summary" role="status">{completed} of {items.length} in library</p>
          <ul className="file-queue">{items.map(item => <li key={item.id}>
            <div className="file-row"><span>{item.name}</span><small>{item.status}</small>
              {!working && (item.status === 'Ready' || item.status === 'Failed') &&
                <button type="button" className="icon-button" aria-label={'Remove ' + item.name + ' from selection'}
                  onClick={() => setItems(previous => previous.filter(file => file.id !== item.id))}><X size={15} /></button>}
            </div>
            {item.error && <p className="file-error">{item.error}</p>}
            {item.kind === 'file' && /\.html?$/i.test(item.name) && (item.status === 'Ready' || item.status === 'Failed') &&
              <label>Original URL for {item.name} (optional)<input type="url" disabled={working} maxLength={2048}
                value={item.url} onChange={event => updateItem(item.id, { url: event.target.value })} /></label>}
          </li>)}</ul>
        </>}
      <label className="date-field">{items.length > 1 ? 'Batch source date (optional)' : 'Source date (optional)'}<input type="date" disabled={working} value={date} onChange={event => setDate(event.target.value)} /></label>
      {error && <div className="error" role="alert">{error}</div>}
      <footer className="modal-actions batch-actions">
        {tab !== 'file' && <button type="button" className="secondary" disabled={working || !title.trim() || !content.trim()}
          onClick={event => { if (event.currentTarget.form?.reportValidity()) queueDraft(); }}>Add to batch</button>}
        <button type="button" className="secondary" onClick={onClose} disabled={working}>{tab === 'file' && completed ? 'Done' : 'Cancel'}</button>
        {(tab !== 'file' || !items.length || pending.length > 0 || working) &&
          <button className="primary" disabled={working || (tab === 'file' && !pending.length)}>
            {working ? 'Indexing…' : tab !== 'file' ? 'Add to library' : pending.some(item => item.status === 'Failed') ? 'Retry remaining sources' : 'Add to library'}
          </button>}
      </footer>
    </form>
  </Modal>;
}

function SourceDialog({ id, match, onClose, onDelete, onReindexed }: {
  id: number; match?: number; onClose: () => void; onDelete: (id: number) => void; onReindexed: () => void;
}) {
  const [detail, setDetail] = useState<SourceDetail>();
  const [error, setError] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [working, setWorking] = useState(false);
  const matched = useRef<HTMLElement>(null);
  const [replacement, setReplacement] = useState<File>();
  const [refreshStatus, setRefreshStatus] = useState('');
  const refreshRequest = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api<SourceDetail>('/sources/' + id, { signal: controller.signal }).then(setDetail).catch(error => {
      if (!controller.signal.aborted) setError(message(error));
    });
    return () => { controller.abort(); refreshRequest.current?.abort(); };
  }, [id]);
  useEffect(() => { matched.current?.scrollIntoView({ block: 'nearest' }); }, [detail]);

  async function remove() {
    setWorking(true);
    setError('');
    try {
      await api('/sources/' + id, { method: 'DELETE' });
      onDelete(id);
    } catch (error) {
      setError(message(error));
      setWorking(false);
    }
  }

  async function refreshPdf() {
    if (!detail || working) return;
    const controller = new AbortController();
    refreshRequest.current = controller;
    setError('');
    setWorking(true);
    setRefreshStatus('Reading PDF pages…');
    try {
      if (detail.has_original) {
        await post('/sources/' + id + '/reindex-pdf', undefined, controller.signal);
      } else {
        if (!replacement || replacement.name !== detail.filename) throw new Error('Choose ' + detail.filename + ' to refresh this source.');
        const body = new FormData();
        body.append('file', replacement);
        body.append('refresh_pdf_id', String(id));
        await api('/sources/index-file', { method: 'POST', body, signal: controller.signal });
      }
      const updated = await api<SourceDetail>('/sources/' + id, { signal: controller.signal });
      setDetail(updated);
      setRefreshStatus('PDF index refreshed. You can search the updated passages now.');
      onReindexed();
    } catch (error) {
      if (!controller.signal.aborted) { setError(message(error)); setRefreshStatus(''); }
    } finally {
      if (!controller.signal.aborted) setWorking(false);
    }
  }

  const selectedPassage = detail?.passages.find(passage => passage.id === match);
  const file = '/api/sources/' + id + '/original';
  return <Modal title={detail?.title || 'Source details'} wide onClose={() => { if (!working) onClose(); }}>
    {error && <div className="error" role="alert">{error}</div>}
    {!detail && !error && <p role="status">Opening source…</p>}
    {detail && <>
      <div className="detail-toolbar">
        <div className="detail-meta"><SourceIcon kind={detail.kind} /><span>{kindLabels[detail.kind]} · {formatDate(detail.source_date)}</span></div>
        {detail.url && <a className="secondary" href={detail.url} target="_blank" rel="noreferrer">Open original</a>}
        {detail.has_original && <a className="secondary"
          href={file + (detail.kind === 'pdf' && selectedPassage?.page ? '#page=' + selectedPassage.page : '')}
          target="_blank" rel="noreferrer">
          {detail.kind === 'pdf' ? 'Open PDF' : 'Download file'}</a>}
        {detail.kind === 'pdf' && detail.has_original && <button className="secondary" disabled={working}
          onClick={refreshPdf}>Refresh PDF index</button>}
      </div>
      {detail.kind === 'pdf' && !detail.has_original && <details>
        <summary>Refresh PDF index</summary>
        {window.isSecureContext ? <>
          <p>Choose {detail.filename} again. ECHO will send it once to <strong>{location.host}</strong> and keep its text, filename and search index in your account.</p>
          <input type="file" aria-label={'Choose ' + detail.filename} accept=".pdf,application/pdf" disabled={working}
            onChange={event => setReplacement(event.target.files?.[0])} />
          <button className="secondary" disabled={!replacement || working} onClick={refreshPdf}>Allow and refresh PDF index</button>
        </> : <p>Open ECHO over HTTPS or use localhost on the host PC to select this file.</p>}
      </details>}
      {refreshStatus && <p role="status">{refreshStatus}</p>}
      <div className="passages">
        {detail.passages.map((passage, idx) => <article key={passage.id} ref={passage.id === match ? matched : undefined}
          className={passage.id === match ? 'matched' : ''}>
          {(passage.page || detail.passages.length > 1 || passage.id === match) &&
            <h3>{passage.page ? 'Page ' + passage.page : 'Passage ' + (idx + 1)}{passage.id === match && ' · Search match'}</h3>}
          <p>{passage.text}</p>
        </article>)}
      </div>
      {confirm ? <div className="delete-confirm">
        <p>Remove this source and its indexed passages from ECHO?</p>
        <div><button className="secondary" disabled={working} onClick={() => setConfirm(false)}>Keep source</button>
          <button className="danger" disabled={working} onClick={remove}>{working ? 'Removing…' : 'Remove source'}</button></div>
      </div> : <footer className="modal-actions">
        <button className="plain danger-text" disabled={working} onClick={() => setConfirm(true)}><Trash2 size={16} /> Remove source</button>
      </footer>}
    </>}
  </Modal>;
}

function Evaluation() {
  const [report, setReport] = useState<Report>();
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  async function run() {
    setError('');
    setWorking(true);
    try { setReport(await post<Report>('/evaluation')); }
    catch (error) { setError(message(error)); }
    finally { setWorking(false); }
  }
  return <div className="subpage">
    <div className="page-heading"><span className="page-icon"><FlaskConical size={23} /></span><h1>Retrieval evaluation</h1>
      <p>Compare keyword, semantic, and context-aware search on the same labelled sample archive.</p></div>
    <section className="panel evaluation-panel">
      <div className="panel-title"><div><h2>Sample benchmark</h2><p>12 sources and 12 queries with predefined relevant results.</p></div>
        <button className="primary" disabled={working} onClick={run}><FlaskConical size={17} /> {working ? 'Running…' : 'Run benchmark'}</button></div>
      {error && <div className="error" role="alert">{error}</div>}
      {report && <>
        <div className="table-scroll"><table><thead><tr><th>Search mode</th><th>Precision@{report.k}</th><th>Recall@{report.k}</th><th>MRR@{report.k}</th><th>nDCG@{report.k}</th><th>Median</th><th>P95</th></tr></thead>
          <tbody>{report.results.map(result => <tr key={result.mode}><th scope="row">{modeLabels[result.mode]}</th>
            <td>{(result.precision * 100).toFixed(1)}%</td><td>{(result.recall * 100).toFixed(1)}%</td>
            <td>{result.mrr.toFixed(3)}</td><td>{result.ndcg.toFixed(3)}</td><td>{result.median_ms} ms</td><td>{result.p95_ms} ms</td></tr>)}</tbody></table></div>
        <p className="benchmark-note">Indexed {report.sources} sample sources in {report.indexing_ms} ms. Timings reflect this device after model warm-up.</p>
        <details className="benchmark-details"><summary>First relevant result for each query</summary>
          <div className="table-scroll"><table><thead><tr><th>Query</th>{report.results.map(result => <th key={result.mode}>{modeLabels[result.mode]}</th>)}</tr></thead>
            <tbody>{report.results[0].queries.map((item, index) => <tr key={item.query}><th scope="row">{item.query}</th>
              {report.results.map(result => <td key={result.mode}>{result.queries[index].rank ?? '—'}</td>)}</tr>)}</tbody></table></div>
        </details>
      </>}
    </section>
  </div>;
}

function App({ username, logout }: { username: string; logout: () => Promise<void> }) {
  const [page, setPage] = useState<Page>(pageFromHash);
  const [health, setHealth] = useState<Health | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<Mode>('contextual');
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [searched, setSearched] = useState<{ query: string; mode: Mode; results: Result[]; latency_ms: number }>();
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [libraryError, setLibraryError] = useState('');
  const [localError, setLocalError] = useState('');
  const [loading, setLoading] = useState(true);
  const [libraryKind, setLibraryKind] = useState<Filters['kind']>('');
  const [libraryQuery, setLibraryQuery] = useState('');
  const [libraryOrder, setLibraryOrder] = useState('recent');
  const [importOpen, setImportOpen] = useState(false);
  const [storageOpen, setStorageOpen] = useState(false);
  const [storageBusy, setStorageBusy] = useState(false);
  const [selectedSource, setSelectedSource] = useState<{ id: number; match?: number }>();
  const [notice, setNotice] = useState('');
  const [addingSample, setAddingSample] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const searchRequest = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    try { setSources(await api<Source[]>('/sources')); setLibraryError(''); }
    catch (error) { setLibraryError(message(error)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => {
    void refresh();
    api<Health>('/health').then(setHealth).catch(error => setLocalError(message(error)));
    const change = () => setPage(pageFromHash());
    window.addEventListener('hashchange', change);
    return () => window.removeEventListener('hashchange', change);
  }, [refresh]);
  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !document.querySelector('dialog[open]')) {
        event.preventDefault();
        if (page !== 'search') location.hash = 'search';
        requestAnimationFrame(() => input.current?.focus());
      }
    }
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [page]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 4300);
    return () => clearTimeout(timer);
  }, [notice]);

  async function search(value = query, method = mode, applied = filters) {
    if (value.trim().length < 2) return;
    setQuery(value);
    setMode(method);
    setSearchError('');
    searchRequest.current?.abort();
    if (applied.from && applied.to && applied.from > applied.to) {
      setSearching(false);
      setSearchError('Choose an end date on or after the start date.');
      return;
    }
    const controller = new AbortController();
    searchRequest.current = controller;
    setSearching(true);
    try {
      const result = await post<{ results: Result[]; latency_ms: number }>('/search', {
        query: value, mode: method, kinds: applied.kind ? [applied.kind] : [],
        date_from: applied.from || null, date_to: applied.to || null,
      }, controller.signal);
      if (!controller.signal.aborted) setSearched({ ...result, query: value.trim(), mode: method });
    } catch (error) {
      if (!controller.signal.aborted) { setSearchError(message(error)); setSearched(undefined); }
    } finally {
      if (!controller.signal.aborted) setSearching(false);
    }
  }

  async function addSample() {
    setAddingSample(true);
    setLibraryError('');
    try {
      const { added } = await post<{ added: number }>('/demo');
      await refresh();
      setNotice(added ? 'Sample archive added.' : 'The sample sources are already in your library.');
    } catch (error) { setLibraryError(message(error)); }
    finally { setAddingSample(false); }
  }

  const visibleSources = sources.filter(source => (!libraryKind || source.kind === libraryKind) &&
    (source.title + ' ' + source.filename).toLowerCase().includes(libraryQuery.trim().toLowerCase()));
  if (libraryOrder === 'title') visibleSources.sort((a, b) => a.title.localeCompare(b.title, undefined, { numeric: true }));
  if (libraryOrder === 'date') visibleSources.sort((a, b) => b.source_date.localeCompare(a.source_date));
  function resetLibrary() { setLibraryQuery(''); setLibraryKind(''); }
  const activeFilters = Boolean(filters.kind || filters.from || filters.to);
  const emptyLibrary = (filtered = false) => <div className="empty-state">
    <span className="empty-icon"><Archive size={23} /></span>
    <h3>{filtered ? 'No sources match these filters' : 'Your archive is empty'}</h3>
    {filtered && <button className="secondary" onClick={resetLibrary}>Clear library filters</button>}
    {!filtered && <><p>Add your own sources or try ECHO with a sample archive.</p><div className="empty-actions">
      <button className="primary" onClick={() => setImportOpen(true)}><Plus size={17} /> Add source</button>
      <button className="secondary" onClick={addSample} disabled={addingSample}>{addingSample ? 'Indexing…' : 'Load sample archive'}</button>
    </div></>}
  </div>;

  return <div className="app">
    <header className="app-header">
      <a className="brand" href="#search" aria-label="ECHO search">
        <img src="/echo.svg" width="40" height="40" alt="" /><span><b>ECHO</b><small>Personal archive</small></span>
      </a>
      <nav aria-label="Workspace">
        {Object.entries(pages).map(([value, { label, icon: Icon }]) => <a key={value} href={'#' + value}
          aria-current={page === value ? 'page' : undefined}><Icon size={19} strokeWidth={1.9} /><span>{label}</span></a>)}
      </nav>
      <button className="primary header-import" onClick={() => setImportOpen(true)}><Plus size={18} /> Add source</button>
    </header>
    <div className="workspace">
      <main className="content">
        {(localError || libraryError) && <div className="error app-error" role="alert">{localError || libraryError}</div>}
        {page === 'search' && <>
          <div className={'search-desk' + (searched ? ' has-results' : '')}>
          <section className="archive-heading">
            <div className="hero-text">
              <h1>Archive search</h1>
              <p>Find a passage by meaning, filename, or exact phrase.</p>
            </div>
            <a className="archive-cover" href="#library" onClick={resetLibrary}>
              <HardDrive size={18} aria-hidden="true" /><strong>Local index</strong>
              <small>{loading ? 'Opening index…' : sources.length + ' indexed sources'}</small>
            </a>
          </section>

          <section className="search-panel" aria-label="Search your archive">
            <form id="archive-search" onSubmit={event => { event.preventDefault(); void search(); }} className="search-form">
              <label className="search-input"><Search size={22} /><span className="sr-only">Describe what you remember</span>
                <input ref={input} value={query} maxLength={1000}
                  onChange={event => setQuery(event.target.value)} placeholder="Search by memory, phrase, or filename…" />
                <kbd aria-hidden="true">Ctrl K</kbd>
              </label>
              <button className="primary search-button" disabled={searching || query.trim().length < 2}>{searching ? 'Searching…' : 'Search'}</button>
            </form>
            <div className="search-controls">
              <div className="segmented search-modes" aria-label="Search mode">
                {(['contextual', 'semantic', 'keyword'] as Mode[]).map(item => <button key={item} type="button"
                  aria-pressed={mode === item} className={mode === item ? 'selected' : ''}
                  onClick={() => { setMode(item); if ((searched || searching) && query.trim().length >= 2) void search(query, item); }}>
                  {item === 'contextual' && <Sparkles size={15} />}{modeLabels[item]}</button>)}
              </div>
              <button className={'secondary filter-button' + (activeFilters ? ' active-filter' : '')}
                aria-expanded={filtersOpen} aria-controls="search-filters" onClick={() => setFiltersOpen(!filtersOpen)}>
                <ListFilter size={16} /> Filters{activeFilters && <span className="filter-dot" />}
              </button>
            </div>
            {filtersOpen && <div className="filter-grid" id="search-filters">
              <label>Source type<SelectKind kind={filters.kind} change={kind => setFilters({ ...filters, kind })} /></label>
              <label>From<input type="date" value={filters.from} onChange={event => setFilters({ ...filters, from: event.target.value })} /></label>
              <label>To<input type="date" value={filters.to} onChange={event => setFilters({ ...filters, to: event.target.value })} /></label>
              <div className="filter-actions">
                <button className="plain" onClick={() => { setFilters(emptyFilters); if (searched) void search(query, mode, emptyFilters); }}>Clear</button>
                <button className="secondary" disabled={query.trim().length < 2} onClick={() => void search()}>Apply filters</button>
              </div>
            </div>}
            {searchError && <div className="error" role="alert">{searchError}</div>}
            <p className="mode-hint">{{
              contextual: 'Uses passage meaning, topic words, and source names.',
              semantic: 'Finds sources by the meaning of their titles and passages.',
              keyword: 'Short topics require all significant words. Use quotes for an exact phrase.',
            }[mode]}</p>
          </section>
          </div>

          {!searched && sources.some(source => source.sample_key) && <div className="suggestions">
            <span>Try a search</span>{prompts.map(prompt => <button key={prompt} onClick={() => void search(prompt)}>{prompt}</button>)}
          </div>}

          <div className={searched ? 'retrieval-results' : 'archive-overview'}>
          {searched ? <section className="list-section" aria-label="Search results" aria-live="polite">
            <div className="section-heading"><div><h2>Results for “{searched.query}”</h2>
              <p>{searched.results.length} {searched.results.length === 1 ? 'source' : 'sources'} · {modeLabels[searched.mode]} · {searched.latency_ms} ms</p></div>
              <button className="plain link-button" onClick={() => {
                searchRequest.current?.abort(); setSearching(false); setSearched(undefined); setQuery(''); input.current?.focus();
              }}>Clear search <X size={15} /></button></div>
            {searched.results.length ? <div className="results-list">{searched.results.map(result => <button className="result-card"
              key={result.id} onClick={() => setSelectedSource({ id: result.id, match: result.chunk_id })}>
              <SourceIcon kind={result.kind} /><div className="result-body"><h3>{result.title}</h3>
                <p><HighlightedExcerpt text={result.excerpt} query={searched.query} /></p><span className="source-meta">{result.match_basis && <><strong className="match-basis">{result.match_basis}</strong><i /></>}{result.filename || kindLabels[result.kind]} <i /> {formatDate(result.source_date)}
                  {result.page && <> <i /> Page {result.page}</>}</span></div>
            </button>)}</div> : <div className="empty-state search-empty"><span className="empty-icon"><Search size={24} /></span>
              <h3>No relevant sources found</h3><p>Try a different description or add a source that covers this topic.</p>
              <button className="secondary" onClick={() => { input.current?.focus(); input.current?.select(); }}>Edit search</button></div>}
          </section> : <section className="list-section" aria-label="Recent sources">
            <div className="section-heading"><h2>Recent sources</h2>
              {sources.length > 0 && <a className="plain link-button" href="#library" onClick={resetLibrary}>View all</a>}</div>
            {loading ? <p role="status">Loading your archive…</p> : sources.length ?
              <div className="source-grid">{sources.slice(0, 5).map(source => <SourceCard key={source.id} source={source}
                open={() => setSelectedSource({ id: source.id })} />)}</div> : emptyLibrary()}
          </section>}
          {!searched && sources.length > 0 && <section className="source-shelf" aria-label="Browse library by source type">
            <h2>Browse by source</h2><div className="shelf-items">
              {Object.entries(kindLabels).map(([value, label]) => {
                const kind = value as Kind;
                const count = sources.filter(source => source.kind === kind).length;
                return count > 0 && <a key={kind} href="#library" onClick={() => { setLibraryKind(kind); setLibraryQuery(''); }}>
                  <SourceIcon kind={kind} /><span>{label}</span><b>{count}</b>
                </a>;
              })}
            </div></section>}
          </div>
        </>}

        {page === 'library' && <div className="subpage">
          <div className="page-heading"><span className="page-icon"><LibraryBig size={24} /></span><h1>Your library</h1>
            <p>Browse and manage the sources you’ve indexed.</p></div>
          <div className="library-toolbar"><label className="search-input"><Search size={19} /><span className="sr-only">Filter by title or filename</span>
            <input value={libraryQuery} onChange={event => setLibraryQuery(event.target.value)} placeholder="Filter by title or filename…" /></label>
            <SelectKind kind={libraryKind} change={setLibraryKind} />
            <div className="select-wrap"><select aria-label="Sort library" value={libraryOrder} onChange={e => setLibraryOrder(e.target.value)}>
              <option value="recent">Recently added</option><option value="title">Title A–Z</option><option value="date">Newest source date</option>
            </select><ChevronDown size={15} aria-hidden="true" /></div>
          </div>
          <section className="list-section"><div className="section-heading"><h2>{visibleSources.length} {visibleSources.length === 1 ? 'source' : 'sources'}</h2></div>
            {loading ? <p role="status">Loading your archive…</p> : visibleSources.length ?
              <div className="source-grid">{visibleSources.map(source => <SourceCard key={source.id} source={source}
                open={() => setSelectedSource({ id: source.id })} />)}</div> : emptyLibrary(Boolean(sources.length))}
          </section>
        </div>}

        {page === 'evaluation' && <Evaluation />}

        {page === 'settings' && <div className="subpage">
          <div className="page-heading"><span className="page-icon"><Settings size={24} /></span><h1>Settings</h1>
            <p>Review your workspace, model, and source controls.</p></div>
          <div className="settings-grid">
            <section className="panel settings-panel"><ShieldCheck size={22} /><h2>Account</h2>
              <p>Signed in as <strong>{username}</strong>.</p>
              <button className="secondary" onClick={() => void logout().catch(error => setLocalError(message(error)))}>Sign out</button></section>
            <section className="panel settings-panel"><HardDrive size={22} /><h2>Local storage</h2>
              <p>Choose files, a folder, or a ZIP. Review the contents before sending selected files to your ECHO host for indexing.</p>
              <button type="button" className="secondary" onClick={() => setStorageOpen(true)}>Index device storage…</button>
              <a className="plain link-button" href="#library">Manage sources</a></section>
            <section className="panel settings-panel"><Sparkles size={22} /><h2>Search model</h2>
              <p>{health?.model || 'all-MiniLM-L6-v2'} creates embeddings on this device. FAISS ranks passages by cosine similarity.</p>
              <span className="settings-status">{health ? health.model_ready ? 'Model installed' : 'Model setup required' : 'Checking model…'}</span></section>
            <section className="panel settings-panel"><BookOpen size={22} /><h2>Sample archive</h2>
              <p>Add 12 labelled example sources to try the import and search workflow.</p>
              <button className="secondary" onClick={addSample} disabled={addingSample}>{addingSample ? 'Indexing…' : 'Load sample archive'}</button></section>
            <section className="panel settings-panel"><CircleHelp size={22} /><h2>PDF and screenshot OCR</h2>
              <p>{health?.ocr_available ? 'Tesseract is available to read printed English text in screenshots and scanned PDF pages.' : 'Install Tesseract with English language data on the ECHO host to search scanned pages and screenshots.'}</p></section>
          </div>
        </div>}
      </main>
    </div>
    {importOpen && <ImportDialog username={username} health={health} onIndexed={refresh} onClose={() => setImportOpen(false)}
      onStorage={() => { setImportOpen(false); setStorageOpen(true); }}
      onAdded={text => { setImportOpen(false); setNotice(text); void refresh(); }} />}
    {storageOpen && <Modal wide title="Index device storage" onClose={() => { if (!storageBusy) setStorageOpen(false); }}>
      <StorageImport username={username} health={health} onIndexed={refresh} onBusy={setStorageBusy} />
    </Modal>}
    {selectedSource && <SourceDialog key={selectedSource.id} id={selectedSource.id} match={selectedSource.match}
      onClose={() => setSelectedSource(undefined)}
      onReindexed={() => { searchRequest.current?.abort(); setSearching(false); setSearched(undefined); void refresh(); }}
      onDelete={id => {
        searchRequest.current?.abort();
        setSearching(false);
        setSelectedSource(undefined);
        setNotice('Source removed from your archive.');
        setSearched(previous => previous && { ...previous, results: previous.results.filter(result => result.id !== id) });
        void refresh();
      }} />}
    {notice && <div className="toast" role="status"><ShieldCheck size={19} />{notice}<button className="icon-button" aria-label="Dismiss notification" onClick={() => setNotice('')}><X size={16} /></button></div>}
  </div>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><Account>{(username, logout) =>
  <App key={username} username={username} logout={logout} />}</Account></StrictMode>);
