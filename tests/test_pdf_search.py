from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from echo.archive import Archive
from echo.embeddings import Embedder
from echo.extract import Document, from_file
from echo.model_setup import DATA_DIR
from echo.ocr import find_tesseract


def make_pdf(scanned_page=False):
    stream = BytesIO()
    pdf = Canvas(stream, pagesize=(640, 400))
    text = pdf.beginText(32, 320)
    for line in ("Meeting minutes for the annual instrument review.", "A zirconium sensor in the spectro-",
                 "photometer recorded the temperature."):
        text.textLine(line)
    pdf.drawText(text)
    if scanned_page:
        pdf.showPage()
        image = Image.new("RGB", (1100, 460), "white")
        ImageDraw.Draw(image).multiline_text((35, 50), "Orchid field notes\nBarometric pressure\nPhotovoltaic panel",
                                             fill="black", font=ImageFont.load_default(size=50), spacing=20)
        pdf.drawImage(ImageReader(image), 20, 105, width=600, height=250)
        pdf.drawString(125, 12, "Laboratory field report prepared for internal review - 2")
    pdf.save()
    return stream.getvalue()


class PdfSearchTests(unittest.TestCase):
    def test_native_pdf_without_ocr(self):
        with patch("echo.extract.find_tesseract", return_value=None):
            doc = from_file("minutes.pdf", make_pdf())
            self.assertIn("spectrophotometer", doc.sections[0][1])
            with self.assertRaisesRegex(ValueError, "page 2 needs OCR"):
                from_file("slides.pdf", make_pdf(scanned_page=True))

    @unittest.skipUnless(find_tesseract(), "Requires local English Tesseract OCR")
    def test_refresh_recovers_page_words(self):
        original = make_pdf(scanned_page=True)
        with TemporaryDirectory() as folder:
            archive = Archive(Path(folder), Embedder(DATA_DIR))
            old = Document("Field notes", "pdf", [(2, "Obsolete placeholder")], filename="minutes.pdf", original=original)
            source_id, _ = archive.add(old)
            archive.search("placeholder", "semantic")  # Populate FAISS before the refresh.
            updated = from_file(old.filename, original)
            self.assertEqual(archive.add(updated, refresh_pdf_id=source_id), (source_id, False))
            self.assertEqual(archive.add(updated), (source_id, False))
            self.assertEqual(len(archive.list_sources()), 1)
            self.assertEqual(archive.detail(source_id)["title"], old.title)
            self.assertEqual(archive.original(source_id)["original"], original)
            self.assertEqual(archive.search("placeholder", "keyword"), [])
            for mode in ("keyword", "contextual", "semantic"):
                for word, page in (("zirconium", 1), ("spectrophotometer", 1), ("orchid", 2),
                                   ("barometric", 2), ("photovoltaic", 2)):
                    with self.subTest(mode=mode, word=word):
                        top = archive.search(word, mode)[0]
                        self.assertEqual((top["id"], top["page"]), (source_id, page))
                        self.assertIn(word, top["excerpt"].casefold())
