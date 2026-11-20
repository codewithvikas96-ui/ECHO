"""Regression cases for metadata and quoted-phrase retrieval in an isolated archive."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from echo.archive import Archive
from echo.extract import Document
from echo.model_setup import DATA_DIR


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
