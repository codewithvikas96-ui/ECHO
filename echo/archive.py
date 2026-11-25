from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime, timezone
from functools import cached_property
from hashlib import sha256
from json import dumps
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
MIXED_IDENTIFIER = re.compile(r"(?=[a-z0-9]*[a-z])(?=[a-z0-9]*[0-9])[a-z0-9]+")


def name_evidence(query_words, topic, query_terms, title, filename):
    if filename and f" {filename} " in f" {query_words} ":
        return "Exact filename"
    if title and title == query_words:
        return "Exact title"
    title_terms = set(title.split())
    compact = topic.replace(" ", "")
    if " " in topic and any(MIXED_IDENTIFIER.fullmatch(word) and word == compact
                                      for word in title_terms | set(filename.split())):
        return "Matching identifier"
    # Require the rest of the title to match before accepting one swapped pair (e.g. LTSM).
    if all(word.isalpha() for word in query_terms):
        title_terms = {word for word in title_terms if not word.isdecimal()}
    if 2 <= len(query_terms) == len(title_terms):
        missing, extra = query_terms - title_terms, title_terms - query_terms
        if len(missing) == len(extra) == 1:
            word, expected = next(iter(missing)), next(iter(extra))
            if word.isalpha() and len(word) == len(expected) >= 4 and any(
                word[:i] + word[i + 1] + word[i] + word[i + 2:] == expected for i in range(len(word) - 1)
            ):
                return "Similar title"
    return ""


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

    def _semantic_candidates(self, db, query_vector, floor: float) -> dict[int, float]:
        faiss.omp_set_num_threads(2)
        revision = db.execute("SELECT revision FROM catalog WHERE id=1").fetchone()[0]
        with self._index_lock:
            if revision != self._revision:
                index = faiss.IndexIDMap(faiss.IndexFlatIP(DIMENSIONS))
                # Positive IDs identify passages. Title IDs are negative and avoid FAISS's -1 sentinel.
                cursor = db.execute("""
                    SELECT id, vector FROM chunks
                    UNION ALL SELECT -source_id-1 AS id, vector FROM title_vectors
                """)
                while vectors := cursor.fetchmany(1024):
                    ids = np.array([row["id"] for row in vectors], dtype=np.int64)
                    matrix = np.stack([np.frombuffer(row["vector"], dtype="<f4") for row in vectors])
                    index.add_with_ids(np.ascontiguousarray(matrix, dtype=np.float32), ids)
                self._revision, self._index = revision, index
            _, scores, ids = self._index.range_search(query_vector, floor)
        return dict(zip(ids.tolist(), scores.tolist()))

    def search(self, query: str, mode: str, kinds=(), date_from=None, date_to=None, top_k=8) -> list[dict]:
        topic = TOPIC_PREFIX.sub("", query.strip()).strip() or query
        topic_words = normalized_words(topic)
        query_terms = terms(topic)
        literal_lookup = topic_words in query_terms
        short_topic = len(query_terms) <= 3
        query_words = normalized_words(query)
        hint = SOURCE_REQUEST.search(query_words) if mode == "contextual" else None
        preferred_kind = SOURCE_HINTS[hint.group(1)] if hint else None
        quoted = [normalized_words(value) for value in re.findall(r'["“]([^"”]+)["”]', query)]
        phrases = [phrase for phrase in quoted if phrase] if mode != "semantic" else []
        semantic_search = mode != "keyword"
        floor = TOPIC_MIN_SIMILARITY if short_topic else DESCRIPTION_MIN_SIMILARITY
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
        scope = " AND ".join(filters)
        model = None
        query_vector = None
        if semantic_search:
            self._index_missing_titles()
            model = self.get_model()
            query_vector = model.encode([topic])
        with self.connect() as db:
            db.execute("BEGIN")
            names = {}
            for source in db.execute(f"SELECT s.id, s.title, s.filename FROM sources s WHERE {scope}", params):
                title, filename = normalized_words(source["title"]), normalized_words(source["filename"])
                filename_terms = terms(Path(source["filename"]).stem)
                overlap = len(filename_terms & query_terms) / max(1, len(query_terms))
                basis = name_evidence(query_words, topic_words, query_terms, title, filename)
                in_name = phrases and all(f" {phrase} " in f" {title} " or f" {phrase} " in f" {filename} "
                                          for phrase in phrases)
                if basis or overlap == 1 or in_name:
                    names[source["id"]] = (basis, overlap)
            lexical = {}
            if query_terms:
                joiner = " AND " if short_topic else " OR "
                expression = joiner.join(f'"{word}"' for word in sorted(query_terms)[:48])
                lexical = {
                    row["rowid"]: max(0.0, -row["strength"])
                    for row in db.execute(f"""
                        SELECT keywords.rowid, bm25(keywords, 2.0, 1.0) AS strength
                        FROM keywords JOIN chunks c ON c.id=keywords.rowid JOIN sources s ON s.id=c.source_id
                        WHERE keywords MATCH ? AND {scope}
                    """, (expression, *params))
                }
            dense = self._semantic_candidates(db, query_vector, floor - 0.08) if semantic_search else {}
            source_ids = set(names) | {-idx - 1 for idx in dense if idx < 0}
            chunk_ids = set(lexical) | {idx for idx in dense if idx > 0}
            vector_column = ", c.vector" if semantic_search else ""
            rows = list(db.execute(f"""
                SELECT {SOURCE_COLUMNS}, c.id AS chunk_id, c.text, c.page{vector_column}
                FROM chunks c JOIN sources s ON s.id=c.source_id WHERE {scope} AND c.id IN (
                    SELECT value FROM json_each(?)
                    UNION ALL
                    SELECT id FROM chunks WHERE source_id IN (SELECT value FROM json_each(?))
                ) ORDER BY c.id
            """, (*params, dumps(list(chunk_ids)), dumps(list(source_ids)))))
        if not rows:
            return []
        semantic = {}
        title_led = set()
        if semantic_search:
            for row in rows:
                chunk_id = row["chunk_id"]
                passage = dense.get(chunk_id)
                if passage is None:
                    passage = float(np.frombuffer(row["vector"], dtype="<f4") @ query_vector[0])
                title = dense.get(-row["id"] - 1, 0.0)
                title_score = 0.8 * title + 0.2 * passage
                semantic[chunk_id] = max(passage, title_score)
                if title_score > passage:
                    title_led.add(chunk_id)
        minimum_similarity = max(floor, max(semantic.values(), default=0.0) - 0.22)
        lexical_max = max(lexical.values(), default=0) or 1.0
        candidates = []
        for row in rows:
            chunk_id = row["chunk_id"]
            keyword = lexical.get(chunk_id, 0.0) / lexical_max
            similarity = semantic.get(chunk_id, 0.0)
            name_basis, filename_overlap = names.get(row["id"], ("", 0.0))
            body = f" {normalized_words(row['text'])} "
            if phrases:
                fields = (body, f" {normalized_words(row['title'])} ", f" {normalized_words(row['filename'])} ")
                if any(not any(f" {phrase} " in field for field in fields) for phrase in phrases):
                    continue
            exact_word = literal_lookup and f" {topic_words} " in body
            exact_phrase = len(query_terms) >= 2 and f" {topic_words} " in body
            coverage = len(query_terms.intersection(body.split())) / max(1, len(query_terms))
            full_coverage = coverage == 1
            strong_lexical = bool(name_basis or filename_overlap == 1 or phrases or (short_topic and chunk_id in lexical))
            eligible = bool(name_basis or exact_word or exact_phrase or similarity >= minimum_similarity)
            basis = "Title meaning" if chunk_id in title_led else "Passage meaning"
            if mode == "keyword":
                if chunk_id not in lexical and not strong_lexical and not (exact_word or exact_phrase):
                    continue
                eligible = True
                score = max(keyword, filename_overlap)
                basis = "Matching words" if keyword >= filename_overlap else "Filename words"
            elif mode == "contextual":
                eligible |= strong_lexical
                # A rare shared word can dominate BM25 for a long natural-language query.
                keyword_evidence = max(keyword if short_topic else keyword * coverage, filename_overlap)
                score = (0.82 * similarity + 0.12 * keyword_evidence if literal_lookup
                         else 0.74 * similarity + 0.25 * keyword_evidence)
                if keyword:
                    basis = "Meaning and keywords" if similarity >= minimum_similarity else "Matching words"
                elif filename_overlap:
                    basis = "Filename words"
                if preferred_kind == row["kind"]:
                    score += 0.20
                    basis = "Meaning and source type"
            else:
                eligible |= short_topic and full_coverage
                score = similarity + 0.08 * keyword
            # Keep potential paraphrases for the second model to check.
            if not eligible and (literal_lookup or similarity < floor - 0.08):
                continue
            if full_coverage:
                score += 0.15
            if exact_word or exact_phrase:
                score += 1.0
                basis = "Exact passage word" if exact_word else "Exact passage phrase"
            if phrases:
                basis = "Quoted phrase"
            if name_basis:
                score += 2.0 if name_basis.startswith("Exact") else 0.9
                basis = name_basis
            result = {key: row[key] for key in row.keys() if key not in ("vector", "text")}
            result.update(excerpt=row["text"], score=score, match_basis=basis,
                          similarity=similarity if semantic_search else None, eligible=eligible)
            candidates.append(result)

        candidates.sort(key=lambda item: (-item["score"], item["id"]))
        if model and not literal_lookup and not phrases:
            self._rerank_candidates(model, topic, candidates, floor)
        ranked = {}
        for result in sorted(candidates, key=lambda item: (-item["score"], item["id"])):
            if result.pop("eligible") and result["id"] not in ranked:
                result["excerpt"] = excerpt_for(result["excerpt"], query_terms)
                ranked[result["id"]] = result
                if len(ranked) == top_k:
                    break
        return list(ranked.values())

    @staticmethod
    def _rerank_candidates(model, query, candidates, floor):
        # Reserve room for other sources when one file has many matching passages.
        first = {}
        second = {}
        for item in candidates:
            source_id = item["id"]
            if source_id in first:
                second.setdefault(source_id, item)
            else:
                first[source_id] = item
        pool = (list(first.values()) + list(second.values()))[:24]
        if not pool or (len(pool) == 1 and pool[0]["eligible"]):
            return
        pairs = [f"{item['title'][:100]}\n{item['excerpt']}" for item in pool]
        scores = model.rerank(query, pairs)
        if scores is None:
            return
        for item, logit in zip(pool, scores, strict=True):
            # Negative logits retain ordering evidence for questions about short passages.
            item["score"] += 0.32 * max(0.0, min(1.0, (logit + 10.0) / 10.0))
            if not item["eligible"] and logit >= 1.75 and item["similarity"] >= floor - 0.08:
                item["eligible"] = True
                item["match_basis"] = "Passage relevance"
