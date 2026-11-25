import { useEffect, useMemo, useRef, useState } from 'react';
import { Files, FolderArchive, FolderOpen, ShieldCheck } from 'lucide-react';
import { api, message } from './api';
import type { Health } from './api';

const formats = '.pdf,.txt,.md,.docx,.html,.htm,.png,.jpg,.jpeg,.webp';
const pageSize = 30;

type DeviceFile = {
  name: string; path: string; size: number; skip?: string; load: (signal: AbortSignal) => Promise<File>;
};

function describe(file: DeviceFile, ocr: boolean | undefined) {
  const { path } = file;
  const ext = file.name.toLowerCase().split('.').pop() || '';
  const image = ['png', 'jpg', 'jpeg', 'webp'].includes(ext);
  let skip = file.skip || '';
  if (skip) return { ...file, type: ext.toUpperCase(), image, skip };
  if (path.split('/').some(part => part.startsWith('.') || ['node_modules', '__pycache__'].includes(part)) ||
      file.name.toLowerCase() === 'phone-password.txt') skip = 'Private or application file';
  else if (!file.name.includes('.') || !formats.split(',').includes('.' + ext)) skip = 'Unsupported format';
  else if (!file.size) skip = 'Empty file';
  else if (file.size > 16 * 1024 * 1024) skip = 'Over 16 MiB';
  else if (image && ocr === false) skip = 'OCR unavailable on host';
  return { ...file, type: ext.toUpperCase(), image, skip };
}

function bytes(size: number) {
  if (size < 1024) return size + ' B';
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KiB';
  return (size / 1024 / 1024).toFixed(1) + ' MiB';
}

type Entry = ReturnType<typeof describe>;
type Outcome = { done: boolean; label: string };

export function StorageImport({ username, health, onIndexed, onBusy }: {
  username: string; health: Health | null; onIndexed: () => void; onBusy: (busy: boolean) => void;
}) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [page, setPage] = useState(0);
  const [working, setWorking] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [status, setStatus] = useState('');
  const [previewing, setPreviewing] = useState(false);
  const [pickerError, setPickerError] = useState('');
  const outcomes = useRef(new Map<number, Outcome>());
  const run = useRef<AbortController | null>(null);
  const preview = useRef<AbortController | null>(null);
  const stop = useRef(false);
  useEffect(() => () => { run.current?.abort(); preview.current?.abort(); }, []);
  const counts = useMemo(() => ({
    images: entries.filter(e => !e.skip && e.image).length,
    docs: entries.filter(e => !e.skip && !e.image).length,
  }), [entries]);
  const totalBytes = useMemo(() => Array.from(selected).reduce((n, id) => n + entries[id].size, 0), [selected, entries]);

  function review(files: DeviceFile[]) {
    setEntries(files.map(file => describe(file, health?.ocr_available)));
    setSelected(new Set());
    outcomes.current.clear();
    setPage(0);
    setStatus('');
    setPickerError(files.length ? '' : 'This selection has no files. Try a different folder, ZIP, or select individual files.');
  }

  function reviewFiles(input: HTMLInputElement) {
    if (!input.files?.length) return;
    const files = Array.from(input.files, file => ({
      name: file.name, path: file.webkitRelativePath || file.name, size: file.size, load: async () => file,
    }));
    input.value = '';
    review(files);
  }

  async function reviewZip(input: HTMLInputElement) {
    const file = input.files?.[0];
    if (!file || preview.current) return;
    input.value = '';
    const controller = new AbortController();
    preview.current = controller;
    setPreviewing(true);
    review([]);
    setPickerError('');
    try {
      const { readZip } = await import('./storageZip');
      const files = await readZip(file, controller.signal);
      if (!controller.signal.aborted) review(files);
    } catch (cause) {
      if (!controller.signal.aborted) setPickerError('Could not open this ZIP. ' + message(cause));
    } finally {
      preview.current = null;
      if (!controller.signal.aborted) setPreviewing(false);
    }
  }

  function selectGroup(group: 'all' | 'images' | 'documents' | 'none') {
    const ids = new Set<number>();
    entries.forEach((entry, i) => {
      if (!entry.skip && !outcomes.current.get(i)?.done &&
          (group === 'all' || (group === 'images' && entry.image) || (group === 'documents' && !entry.image))) ids.add(i);
    });
    setSelected(ids);
  }

  async function indexSelected() {
    if (run.current || preview.current || !selected.size || !window.isSecureContext) return;
    const controller = new AbortController();
    run.current = controller;
    stop.current = false;
    setStopping(false);
    setWorking(true);
    onBusy(true);
    const batch = Array.from(selected);
    let completed = 0;
    let failed = 0;
    for (const [position, id] of batch.entries()) {
      if (stop.current || controller.signal.aborted) break;
      const entry = entries[id];
      setStatus(`Indexing ${position + 1} of ${batch.length} · ${entry.path}`);
      try {
        const file = await entry.load(controller.signal);
        if (controller.signal.aborted) break;
        const body = new FormData();
        // The relative directory is shown only in this browser. Send the basename to the host.
        body.append('file', file, entry.name);
        const result = await api<{ created: boolean }>('/sources/index-file', {
          method: 'POST', body, signal: controller.signal, headers: { 'X-Echo-Account': username },
        });
        outcomes.current.set(id, { done: true, label: result.created ? 'Indexed' : 'Already in library' });
        completed++;
      } catch (cause) {
        if (controller.signal.aborted) break;
        outcomes.current.set(id, { done: false, label: message(cause) });
        failed++;
      }
    }
    if (controller.signal.aborted) return;
    run.current = null;
    setWorking(false);
    onBusy(false);
    setSelected(new Set(batch.filter(id => !outcomes.current.get(id)?.done)));
    setStatus(`${completed} in library · ${failed} failed · ${batch.length - completed - failed} unprocessed`);
    if (completed) onIndexed();
  }

  const protectedConnection = window.isSecureContext;
  const pickerDisabled = working || previewing || !protectedConnection;
  const pages = Math.ceil(entries.length / pageSize);
  return <section className="storage-import">
    <div className="storage-scope"><ShieldCheck size={22} aria-hidden="true" /><div>
      <strong>One-time access for {username}</strong>
      <p>Your picker shows files from this device. Folder and ZIP selections include subfolders. Review which files ECHO may read.</p>
      <p>Approving sends the selected files to <strong>{location.host}</strong> for extraction. Your account stores filenames, extracted text, source metadata and search vectors. This import discards the uploaded originals after processing.</p>
      <p>Folder paths stay in this browser. Future changes require a new selection. Remove indexed content in Library. Someone with access to the host’s database files can read the stored text.</p>
      {!protectedConnection && <p role="alert"><strong>Storage indexing requires HTTPS on your phone.</strong> Open the HTTPS address printed by the ECHO phone launcher, or use localhost on the host PC.</p>}
    </div></div>
    <div className="storage-buttons">
      <label className="secondary storage-picker"><Files size={17} aria-hidden="true" /> Choose files or images
        <input type="file" multiple accept={formats} disabled={pickerDisabled} onChange={e => reviewFiles(e.currentTarget)} />
      </label>
      {'webkitdirectory' in HTMLInputElement.prototype && <label className="secondary storage-picker">
        <FolderOpen size={17} aria-hidden="true" /> Choose folder
        <input ref={element => { element?.setAttribute('webkitdirectory', ''); }} type="file" multiple
          disabled={pickerDisabled} onChange={e => reviewFiles(e.currentTarget)} />
      </label>}
      <label className="secondary storage-picker"><FolderArchive size={17} aria-hidden="true" /> Choose ZIP
        <input type="file" accept=".zip,application/zip" disabled={pickerDisabled} onChange={e => void reviewZip(e.currentTarget)} />
      </label>
    </div>
    {protectedConnection && <p>If your phone’s picker only opens folders, compress the folder in your Files app and choose the ZIP here. You can also select multiple files directly.</p>}
    {previewing && <p role="status">Reading ZIP file list on this device…</p>}
    {pickerError && <p className="error" role="alert">{pickerError}</p>}
    {!!entries.length && <>
      <p>{counts.docs} eligible documents · {counts.images} eligible images · {entries.length - counts.docs - counts.images} skipped</p>
      <div className="storage-buttons" aria-label="Select files by type">
        {(['all', 'documents', 'images', 'none'] as const).map(group => <button type="button" key={group} className="secondary"
          disabled={working || previewing} onClick={() => selectGroup(group)}>{group === 'none' ? 'Clear selection' : 'Select ' + group}</button>)}
      </div>
      <ul className="storage-files">{entries.slice(page * pageSize, (page + 1) * pageSize).map((entry, n) => {
        const id = page * pageSize + n;
        const outcome = outcomes.current.get(id);
        return <li key={id}><label><input type="checkbox" checked={selected.has(id)}
          disabled={working || previewing || !!entry.skip || outcome?.done} onChange={e => {
            const next = new Set(selected);
            if (e.target.checked) next.add(id); else next.delete(id);
            setSelected(next);
          }} /><span>{entry.path}<small>{entry.type} · {bytes(entry.size)} {entry.skip || outcome ? ' · ' + (entry.skip || outcome?.label) : ''}</small></span></label></li>;
      })}</ul>
      {pages > 1 && <div className="storage-buttons">
        <button type="button" className="secondary" disabled={!page} onClick={() => setPage(page - 1)}>Previous</button>
        <span>Page {page + 1} of {pages}</span>
        <button type="button" className="secondary" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</button>
      </div>}
      <p><strong>{selected.size} selected · {bytes(totalBytes)}</strong></p>
      {status && <p role="status">{status}</p>}
      {working ? <button type="button" className="secondary" disabled={stopping}
        onClick={() => { stop.current = true; setStopping(true); }}>Stop after current file</button> :
        <button type="button" className="primary" disabled={!selected.size || pickerDisabled} onClick={indexSelected}>Allow and index selected files</button>}
    </>}
  </section>;
}
