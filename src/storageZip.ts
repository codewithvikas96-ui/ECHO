import { BlobReader, ZipReader } from '@zip.js/zip.js';

const MAX_FILE_BYTES = 16 * 1024 * 1024;

export async function readZip(file: File, signal: AbortSignal) {
  signal.throwIfAborted();
  if (file.size > 512 * 1024 * 1024) throw new Error('Choose a ZIP archive up to 512 MiB.');
  const reader = new ZipReader(new BlobReader(file));
  const files = [];
  try {
    let count = 0;
    for await (const entry of reader.getEntriesGenerator()) {
      signal.throwIfAborted();
      if (++count > 10_000) throw new Error('Split this ZIP into archives of at most 10,000 entries.');
      if (entry.directory) continue;
      const path = entry.filename.replaceAll('\\', '/');
      const parts = path.split('/');
      const name = parts.at(-1) ?? '';
      const unsafe = parts.some(part => !part || part === '..' || part.includes(':') || /[\x00-\x1f]/.test(part));
      files.push({
        name, path, size: entry.uncompressedSize,
        skip: unsafe ? 'Invalid ZIP path' : entry.encrypted ? 'Encrypted ZIP entry' : '',
        async load(signal: AbortSignal) {
          signal.throwIfAborted();
          if (unsafe || entry.encrypted || entry.uncompressedSize > MAX_FILE_BYTES) {
            throw new Error('This ZIP entry cannot be extracted.');
          }
          const chunks: Uint8Array<ArrayBuffer>[] = [];
          let size = 0;
          const output = new WritableStream<Uint8Array>({
            write(chunk) {
              size += chunk.length;
              if (size > entry.uncompressedSize || size > MAX_FILE_BYTES) throw new Error('ZIP entry exceeds its size limit.');
              chunks.push(new Uint8Array(chunk));
            },
          });
          await entry.getData(output, { signal, checkSignature: true, useWebWorkers: false });
          if (size !== entry.uncompressedSize) throw new Error('ZIP entry size did not match.');
          return new File(chunks, name);
        },
      });
    }
  } finally {
    // BlobReader does not hold an open file handle. Entry readers still reference the selected Blob.
    await reader.close();
  }
  return files;
}
