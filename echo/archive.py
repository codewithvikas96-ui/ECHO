from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime, timezone
from functools import cached_property
from hashlib import sha256
from pathlib import Path
from threading import Lock
import re
import sqlite3

import faiss
import numpy as np

from .embeddings import DIMENSIONS, Embedder
from .extract import Document, chunk_sections

STOP_WORDS = set(
    "a an and are as at be by did do does find for from how i in is it me my of on or show "
    "that the them then there they this those to was were what when where which who why will with you".split()
)
SOURCE_COLUMNS = "s.id, s.title, s.kind, s.url, s.filename, s.source_date, s.created_at, s.sample_key"
SOURCE_HINTS = {
    "webpage": "webpage", "web page": "webpage", "website": "webpage", "article": "webpage",
    "pdf": "pdf", "note": "note", "screenshot": "image", "word document": "docx", "text file": "text",
}
SOURCE_REQUEST = re.compile(
    r"\b(?:my|the|that|which|a|an) (" + "|".join(SOURCE_HINTS) + r")\b",
    re.IGNORECASE,
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    filename TEXT NOT NULL,
    original BLOB,
    source_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    sample_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    page INTEGER,
    text TEXT NOT NULL,
    vector BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source_id, id);
CREATE TABLE IF NOT EXISTS title_vectors (
    source_id INTEGER PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    vector BLOB NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS keywords USING fts5(title, body, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS catalog (id INTEGER PRIMARY KEY CHECK (id = 1), revision INTEGER NOT NULL);
INSERT OR IGNORE INTO catalog VALUES (1, 0);
"""


def terms(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", text.casefold())) - STOP_WORDS


def normalized_words(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def excerpt_for(text: str, query_terms: set[str], width: int = 230) -> str:
    """Center the preview on the passage window with the most distinct query words."""
    if len(text) <= width:
        return text
    hits = [(m.start(), m.group().casefold()) for m in re.finditer(r"[^\W_]+", text)
            if m.group().casefold() in query_terms]
    start, best = 0, 0
    for position, _ in hits:
        left = max(0, min(position - 40, len(text) - width))
        count = len({word for offset, word in hits if left <= offset < left + width})
        if count > best:
            start, best = left, count
    end = start + width
    if start and text[start - 1].isalnum():
        while start > 0 and text[start - 1].isalnum():
            start -= 1
    while end < len(text) and text[end].isalnum():
        end += 1
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


# Short topics supply less context than descriptions and need stronger semantic evidence.
TOPIC_MIN_SIMILARITY = 0.43
DESCRIPTION_MIN_SIMILARITY = 0.27
TOPIC_PREFIX = re.compile(r"^(?:please\s+)?(?:tell me about|what is|what are|search for|find information about)\s+", re.I)


class Archive:
    def __init__(self, directory: Path, embedder: Embedder | None = None, *,
                 model_factory: Callable[[], Embedder] | None = None):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "archive.sqlite3"
        self.directory = directory
        self._shared_embedder = embedder
        self._model_factory = model_factory
        self._model_lock = Lock()
        self._index_lock = Lock()
        self._revision = -1
        self._index = faiss.IndexIDMap(faiss.IndexFlatIP(DIMENSIONS))
        with self.connect() as db:
            db.executescript(SCHEMA)

    @cached_property
    def embedder(self) -> Embedder:
        return self._shared_embedder or (self._model_factory() if self._model_factory else Embedder(self.directory))

    def get_model(self) -> Embedder:
        with self._model_lock:
            return self.embedder

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def list_sources(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(f"""
                SELECT {SOURCE_COLUMNS}, COUNT(c.id) AS passage_count
                FROM sources s LEFT JOIN chunks c ON c.source_id=s.id
                GROUP BY s.id ORDER BY s.id DESC
            """)
            return [dict(row) for row in rows]

    def detail(self, source_id: int) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN")
            row = db.execute(f"SELECT {SOURCE_COLUMNS}, original IS NOT NULL AS has_original FROM sources s WHERE id=?",
                             (source_id,)).fetchone()
            if not row:
                return None
            passages = db.execute("SELECT id, page, text FROM chunks WHERE source_id=? ORDER BY id", (source_id,))
            return {**dict(row), "has_original": bool(row["has_original"]), "passages": [dict(passage) for passage in passages]}

    def original(self, source_id: int):
        with self.connect() as db:
            return db.execute("SELECT filename, kind, original FROM sources WHERE id=?", (source_id,)).fetchone()

    def add(self, document: Document, source_date: date | None = None, sample_key: str | None = None,
            *, refresh_pdf_id: int | None = None) -> tuple[int, bool]:
        model = self.get_model()
        chunks = chunk_sections(document, model.chunk_tokenizer)
        content = "\n".join(text for _, text in chunks)
        key = sha256(f"{document.kind}\x00{document.url}\x00{content}".encode()).hexdigest()
        with self.connect() as db:
            existing = db.execute("SELECT id FROM sources WHERE fingerprint=?", (key,)).fetchone()
        if existing and refresh_pdf_id is None:
            return existing["id"], False
        vectors = model.encode([text for _, text in chunks] + [document.title])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM sources WHERE fingerprint=?", (key,)).fetchone()
            previous_file = None
            if refresh_pdf_id is not None:
                previous_file = db.execute("""
                    SELECT id,title FROM sources WHERE id=? AND kind='pdf' AND filename=? AND original IS ?
                """, (refresh_pdf_id, document.filename, document.original)).fetchone()
                if not previous_file or document.kind != "pdf":
                    raise ValueError("Select the same PDF for this library entry.")
                if existing and existing["id"] != refresh_pdf_id:
                    raise ValueError("Another source already contains this text. The existing index was kept.")
            if existing:
                return existing["id"], False
            if previous_file is None and document.kind in {"image", "pdf"} and document.original:
                previous_file = db.execute(
                    "SELECT id, title FROM sources WHERE kind=? AND original=? ORDER BY id LIMIT 1",
                    (document.kind, document.original),
                ).fetchone()
            if previous_file:
                # Refresh derived OCR in place so bookmarks, dates and originals survive a retry.
                source_id, title = previous_file["id"], previous_file["title"]
                db.execute("UPDATE sources SET fingerprint=? WHERE id=?", (key, source_id))
                db.execute("DELETE FROM keywords WHERE rowid IN (SELECT id FROM chunks WHERE source_id=?)", (source_id,))
                db.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
            else:
                title = document.title
                source = db.execute("""
                    INSERT INTO sources (title,kind,url,filename,original,source_date,created_at,fingerprint,sample_key)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (title, document.kind, document.url, document.filename, document.original,
                      (source_date or date.today()).isoformat(), datetime.now(timezone.utc).isoformat(), key, sample_key))
                source_id = source.lastrowid
            if title == document.title:
                db.execute("INSERT OR REPLACE INTO title_vectors VALUES (?,?)",
                           (source_id, vectors[-1].astype("<f4", copy=False).tobytes()))
            for (page, text), vector in zip(chunks, vectors[:-1], strict=True):
                chunk = db.execute(
                    "INSERT INTO chunks (source_id,page,text,vector) VALUES (?,?,?,?)",
                    (source_id, page, text, vector.astype("<f4", copy=False).tobytes()),
                )
                db.execute("INSERT INTO keywords(rowid,title,body) VALUES(?,?,?)", (chunk.lastrowid, title, text))
            db.execute("UPDATE catalog SET revision=revision+1")
        return source_id, previous_file is None

    def delete(self, source_id: int) -> bool:
        with self.connect() as db:
            db.execute("DELETE FROM keywords WHERE rowid IN (SELECT id FROM chunks WHERE source_id=?)", (source_id,))
            deleted = db.execute("DELETE FROM sources WHERE id=?", (source_id,)).rowcount
            if deleted:
                db.execute("UPDATE catalog SET revision=revision+1")
        return bool(deleted)

    def _index_missing_titles(self):
        # Older archives only have passage vectors. Encode in batches outside the write transaction.
        with self.connect() as db:
            while missing := db.execute("""
                SELECT s.id, s.title FROM sources s LEFT JOIN title_vectors t ON t.source_id=s.id
                WHERE t.source_id IS NULL ORDER BY s.id LIMIT 64
            """).fetchall():
                vectors = self.get_model().encode([row["title"] for row in missing])
                with db:
                    for row, vector in zip(missing, vectors, strict=True):
                        db.execute("""
                            INSERT OR IGNORE INTO title_vectors (source_id, vector)
                            SELECT id, ? FROM sources WHERE id=? AND title=?
                        """, (vector.astype("<f4", copy=False).tobytes(), row["id"], row["title"]))
                    db.execute("UPDATE catalog SET revision=revision+1")

    def _semantic_scores(self, db, rows, query_vector) -> tuple[dict[int, float], set[int]]:
        faiss.omp_set_num_threads(2)
        revision = db.execute("SELECT revision FROM catalog WHERE id=1").fetchone()[0]
        with self._index_lock:
            if revision != self._revision:
                index = faiss.IndexIDMap(faiss.IndexFlatIP(DIMENSIONS))
                # Positive IDs identify passages. Title IDs are negative and avoid FAISS's -1 sentinel.
                vectors = list(db.execute("""
                    SELECT id, vector FROM chunks
                    UNION ALL SELECT -source_id-1 AS id, vector FROM title_vectors
                """))
                if vectors:
                    ids = np.array([row["id"] for row in vectors], dtype=np.int64)
                    matrix = np.stack([np.frombuffer(row["vector"], dtype="<f4") for row in vectors])
                    index.add_with_ids(np.ascontiguousarray(matrix, dtype=np.float32), ids)
                self._revision, self._index = revision, index
            if not self._index.ntotal:
                return {}, set()
            scores, ids = self._index.search(query_vector, self._index.ntotal)
        similarities = dict(zip(ids[0].tolist(), scores[0].tolist()))
        combined = {}
        title_led = set()
        for row in rows:
            passage = similarities[row["chunk_id"]]
            title = similarities.get(-row["id"] - 1, 0.0)
            # A descriptive title can recover a generic passage. Passage evidence still picks
            # the excerpt when multiple chunks share that title.
            combined[row["chunk_id"]] = max(passage, 0.8 * title + 0.2 * passage)
            if combined[row["chunk_id"]] > passage:
                title_led.add(row["chunk_id"])
        return combined, title_led

    def search(self, query: str, mode: str, kinds=(), date_from=None, date_to=None, top_k=8) -> list[dict]:
        topic = TOPIC_PREFIX.sub("", query.strip()).strip() or query
        query_terms = terms(topic)
        short_topic = len(query_terms) <= 3
        query_words = normalized_words(query)
        hint = SOURCE_REQUEST.search(query_words) if mode == "contextual" else None
        preferred_kind = SOURCE_HINTS[hint.group(1)] if hint else None
        phrases = [normalized_words(value) for value in re.findall(r'["“]([^"”]+)["”]', query)]
        phrases = [phrase for phrase in phrases if phrase] if mode != "semantic" else []
        filters = ["1=1"]
        params = []
        if kinds:
            filters.append(f"s.kind IN ({','.join('?' for _ in kinds)})")
            params.extend(kinds)
        if date_from:
            filters.append("s.source_date >= ?")
            params.append(str(date_from))
        if date_to:
            filters.append("s.source_date <= ?")
            params.append(str(date_to))
        if mode != "keyword":
            self._index_missing_titles()
        with self.connect() as db:
            db.execute("BEGIN")
            rows = list(db.execute(f"""
                SELECT {SOURCE_COLUMNS}, c.id AS chunk_id, c.text, c.page
                FROM chunks c JOIN sources s ON s.id=c.source_id
                WHERE {' AND '.join(filters)}
            """, params))
            if not rows:
                return []
            lexical = {}
            if query_terms and mode != "semantic":
                joiner = " AND " if short_topic else " OR "
                expression = joiner.join(f'"{word}"' for word in sorted(query_terms)[:48])
                lexical = {
                    row["rowid"]: max(0.0, -row["strength"])
                    for row in db.execute(
                        "SELECT rowid, bm25(keywords, 2.0, 1.0) AS strength FROM keywords WHERE keywords MATCH ?",
                        (expression,),
                    )
                }
            semantic = {}
            title_led = set()
            if mode != "keyword":
                semantic, title_led = self._semantic_scores(db, rows, self.get_model().encode([topic]))
        minimum_similarity = TOPIC_MIN_SIMILARITY if short_topic else DESCRIPTION_MIN_SIMILARITY
        # Keep weak tail results out even when one source strongly matches the query.
        minimum_similarity = max(minimum_similarity, max(semantic.values(), default=0.0) - 0.22)
        best_by_source = {}
        lexical_max = max((lexical.get(row["chunk_id"], 0.0) for row in rows), default=0.0) or 1.0
        for row in rows:
            chunk_id = row["chunk_id"]
            keyword = lexical.get(chunk_id, 0.0) / lexical_max
            similarity = semantic.get(chunk_id, 0.0)
            title = normalized_words(row["title"])
            filename = normalized_words(row["filename"])
            exact_filename = bool(filename and f" {filename} " in f" {query_words} ")
            exact_title = bool(title and title == query_words)
            filename_terms = terms(Path(row["filename"]).stem)
            filename_overlap = len(filename_terms & query_terms) / max(1, len(query_terms))
            if phrases:
                fields = [f" {value} " for value in (normalized_words(row["text"]), title, filename)]
                if any(not any(f" {phrase} " in field for field in fields) for phrase in phrases):
                    continue
            basis = "Title meaning" if chunk_id in title_led else "Passage meaning"
            exact_name = exact_filename or exact_title
            filename_match = bool(query_terms and query_terms <= filename_terms)
            # Evidence determines admission before relative ranking or metadata boosts.
            phrase_match = bool(phrases) and query_terms <= terms(" ".join(phrases))
            strong_lexical = exact_name or filename_match or phrase_match or (short_topic and chunk_id in lexical)
            if mode == "semantic" and similarity < minimum_similarity:
                continue
            if mode == "contextual" and not strong_lexical and similarity < minimum_similarity:
                continue
            if mode == "keyword":
                if chunk_id not in lexical and not (filename_match or exact_name or phrase_match):
                    continue
                score = max(keyword, filename_overlap)
                basis = "Matching words" if keyword >= filename_overlap else "Filename words"
            elif mode == "semantic":
                score = similarity
            else:
                title_match = len(query_terms & terms(row["title"])) / max(1, len(query_terms))
                score = 0.82 * similarity + 0.12 * max(keyword, filename_overlap) + 0.06 * title_match
                if keyword:
                    basis = "Meaning and keywords" if similarity >= minimum_similarity else "Matching words"
                elif filename_match:
                    basis = "Filename words"
                if preferred_kind == row["kind"]:
                    score += 0.08
                    basis = "Meaning and source type"
            if mode != "semantic":
                if phrases:
                    basis = "Quoted phrase"
                if exact_filename or exact_title:
                    # Exact names outrank approximate hits. Passage relevance breaks ties.
                    score += 2
                    basis = "Exact filename" if exact_filename else "Exact title"
            previous = best_by_source.get(row["id"])
            if previous is None or score > previous["score"]:
                result = dict(row)
                result["excerpt"] = result.pop("text")
                result["score"] = score
                result["match_basis"] = basis
                result["similarity"] = similarity if mode != "keyword" else None
                best_by_source[row["id"]] = result
        ranked = sorted(best_by_source.values(), key=lambda result: (-result["score"], result["id"]))[:top_k]
        for result in ranked:
            result["excerpt"] = excerpt_for(result["excerpt"], query_terms)
        return ranked
