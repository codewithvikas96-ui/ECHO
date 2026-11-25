"""Regression cases for metadata and quoted-phrase retrieval in an isolated archive."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from echo.archive import Archive
from echo.extract import Document
from echo.model_setup import DATA_DIR, reranker_ready


class SearchRankingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.archive = Archive(Path(cls.folder.name), Archive(DATA_DIR).get_model())
        cls.names = ["audit-kestrel-431.txt", "audit-sparrow-284.txt", "audit-robin-625.txt", "audit-finch-719.txt"]
        for number, filename in enumerate(cls.names):
            text = f"Verify the supplier invoice and reconcile the outstanding balance. Batch {number}."
            cls.archive.add(Document("Audit checklist", "text", [(None, text)], filename=filename))
        for kind in ["webpage", "pdf", "note"]:
            cls.archive.add(Document("Indoor air", kind, [(None, "Fresh air and cross ventilation cool an occupied room.")]))

    def test_named_file(self):
        for mode in ["contextual", "keyword"]:
            for filename in self.names:
                with self.subTest(filename=filename, mode=mode):
                    first = self.archive.search(filename.upper(), mode, top_k=1)[0]
                    self.assertEqual(first["filename"], filename)
                    self.assertEqual(first["match_basis"], "Exact filename")

    def test_preferred_source_type_and_explicit_filter(self):
        for kind in ["pdf", "note", "webpage"]:
            query = f"Find my {kind} about air circulation indoors"
            self.assertEqual(self.archive.search(query, "contextual")[0]["kind"], kind)
            restricted = self.archive.search(query, "contextual", kinds=["text"])
            self.assertEqual(restricted, [])

    def test_incidental_topic_and_positive_control(self):
        with TemporaryDirectory() as directory:
            archive = Archive(Path(directory), self.archive.get_model())
            archive.add(Document("Project notes", "text", [(None, "Use SQLite for metadata. Cloud deployment is optional. Use FAISS for a local vector index.")]))
            archive.add(Document("Weather observations", "note", [(None, "A cloud of mist settled over the meadow.")]))
            for mode in ["contextual", "semantic", "keyword"]:
                self.assertEqual(archive.search("cloud computing", mode), [], mode)
            real_id, _ = archive.add(Document("Infrastructure", "note", [(None, "Cloud computing delivers computing resources on demand over the internet, including virtual servers and managed databases.")]))
            for mode in ["contextual", "semantic", "keyword"]:
                self.assertEqual([hit["id"] for hit in archive.search("cloud computing", mode)], [real_id], mode)

    def test_quoted_word_order(self):
        for mode in ["contextual", "keyword"]:
            hits = self.archive.search('“cross ventilation”', mode)
            self.assertEqual({item["kind"] for item in hits}, {"pdf", "webpage", "note"})
            self.assertEqual(self.archive.search('"ventilation cross"', mode), [])

    def test_literal_technical_queries(self):
        with TemporaryDirectory() as directory:
            archive = Archive(Path(directory), self.archive.get_model())
            lstm, _ = archive.add(Document("15.LSTM Architecture", "pdf", [
                (1, "An introduction to recurrent networks."),
                (4, "The forget gate controls which values survive in the previous cell state."),
            ], filename="15.LSTM Architecture.pdf"))
            words, _ = archive.add(Document("Word2Vec reading material", "pdf", [(2, "A guide to word embeddings.")]))
            archive.add(Document("Gate maintenance", "note", [(None, "Forget the combination on an old garden gate.")]))
            for mode in ("keyword", "contextual", "semantic"):
                for query, expected in (("LTSM Architecture", lstm), ("word 2 vec", words), ("forget gate", lstm)):
                    with self.subTest(mode=mode, query=query):
                        hit = archive.search(query, mode)[0]
                        self.assertEqual(hit["id"], expected)
                        if query == "forget gate":
                            self.assertEqual((hit["page"], hit["match_basis"]), (4, "Exact passage phrase"))
                self.assertEqual(archive.search("forget gate", mode, kinds=["image"]), [])

    @unittest.skipUnless(reranker_ready(DATA_DIR), "Run echo.model_setup to install the local reranker")
    def test_reranker_resolves_context_vs_center_word(self):
        with TemporaryDirectory() as directory:
            archive = Archive(Path(directory), self.archive.get_model())
            for title, body in (
                ("Attention diagrams", "Attention uses queries, keys, and values to select how much one token influences another. A mask hides future tokens during training."),
                ("Vector learning", "In skip-gram, the input is the center word. The model predicts neighboring words in a context window from this center word."),
                ("Pooled context", "The continuous bag of words (CBOW) objective uses neighboring words as input and predicts the center word."),
            ):
                archive.add(Document(title, "note", [(None, body)]))
            hits = archive.search("How is the middle token inferred from nearby words?", "contextual", top_k=1)
            self.assertEqual([item["title"] for item in hits], ["Pooled context"])

    def test_title_paraphrases_and_unrelated_queries(self):
        cases = [
            ("Business travel reimbursement", "How do I get paid back for a work trip?"),
            ("Emergency evacuation routes", "Where should we go if the building is on fire?"),
            ("Rainwater harvesting", "How do I collect the water that runs off my roof?"),
            ("Password recovery instructions", "How do I regain access after forgetting my login?"),
            ("Extending laptop battery life", "How can a notebook computer last longer between charges?"),
            ("Sleep and memory consolidation", "How does sleeping help the brain retain what it learns?"),
        ]
        with TemporaryDirectory() as directory:
            archive = Archive(Path(directory), self.archive.get_model())
            for number, (title, _) in enumerate(cases, start=1):
                body = f"Revision {number}. See the signed checklist and updated instructions in the appendix."
                archive.add(Document(title, "pdf", [(1, body)]))
            for mode in ("semantic", "contextual"):
                for title, query in cases:
                    with self.subTest(mode=mode, title=title):
                        results = archive.search(query, mode, top_k=1)
                        self.assertEqual([r["title"] for r in results], [title])
                for query in ("cloud computing", "sourdough starter", "coral reef"):
                    self.assertEqual(archive.search(query, mode), [])
                self.assertEqual(archive.search(cases[0][1], mode, kinds=["note"]), [])

    def test_upgrade_archive_with_passage_only_embeddings(self):
        with TemporaryDirectory() as directory:
            archive = Archive(Path(directory), self.archive.get_model())
            source_id, _ = archive.add(Document("Rainwater harvesting", "pdf", [(1, "Read the appendix.")]))
            with archive.connect() as db:
                original = db.execute("SELECT id,vector FROM chunks").fetchone()
                db.execute("DROP TABLE title_vectors")
            reopened = Archive(Path(directory), self.archive.get_model())
            reopened.search("appendix", "keyword")
            with reopened.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM title_vectors").fetchone()[0], 0)
            result = reopened.search("How do I collect the water that runs off my roof?", "semantic")[0]
            self.assertEqual((result["id"], result["match_basis"]), (source_id, "Title meaning"))
            with reopened.connect() as db:
                self.assertEqual(tuple(db.execute("SELECT id,vector FROM chunks").fetchone()), tuple(original))
                self.assertEqual(db.execute("SELECT source_id FROM title_vectors").fetchone()[0], source_id)
            self.assertTrue(reopened.delete(source_id))
            with reopened.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM title_vectors").fetchone()[0], 0)

    def test_refresh_image_ocr_preserves_original_source(self):
        with TemporaryDirectory() as directory:
            archive = Archive(Path(directory), self.archive.get_model())
            image = Document("Poster", "image", [(None, "Old misreading")], filename="poster.png", original=b"same image")
            source_id, _ = archive.add(image)
            image.sections = [(None, "DREAM STRONGER")]
            self.assertEqual(archive.add(image), (source_id, False))
            self.assertEqual(archive.add(image), (source_id, False))
            self.assertEqual(len(archive.list_sources()), 1)
            self.assertEqual(archive.original(source_id)["original"], image.original)
            self.assertEqual(archive.search("misreading", "keyword"), [])
            for mode in ("keyword", "contextual"):
                self.assertEqual(archive.search("dream", mode)[0]["id"], source_id)
