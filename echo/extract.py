from contextlib import closing
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
from zipfile import ZipFile
import re
import unicodedata

from bs4 import BeautifulSoup
from defusedxml.ElementTree import fromstring
from pypdf import PdfReader
import pypdfium2 as pdfium

from .ocr import NoReadableText, extract_image, find_tesseract, read_printed_text

MAX_UPLOAD = 16 * 1024 * 1024
MAX_TEXT = 1_000_000
TYPES = {
    ".pdf": "pdf", ".txt": "text", ".md": "text", ".docx": "docx",
    ".html": "webpage", ".htm": "webpage",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
}
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PDF_RENDER_LOCK = Lock()  # PDFium calls, including object destruction, must be serialized.


@dataclass
class Document:
    title: str
    kind: str
    sections: list[tuple[int | None, str]]
    url: str = ""
    filename: str = ""
    original: bytes | None = None


def valid_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if (
        len(value) > 2048 or parsed.scheme not in {"http", "https"}
        or not parsed.hostname or parsed.username is not None
        or "\\" in value or any(char.isspace() for char in value)
    ):
        raise ValueError("Use an HTTP or HTTPS address for the original page.")
    return value


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\x00", "").replace("\r\n", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def pdf_page_image(data: bytes, index: int):
    with PDF_RENDER_LOCK, pdfium.PdfDocument(data) as doc, closing(doc[index]) as page:
        width, height = page.get_size()
        if min(width, height) <= 0:
            raise ValueError(f"Invalid dimensions on PDF page {index + 1}.")
        scale = min(3.0, 3200 / max(width, height), (6_000_000 / (width * height)) ** 0.5)
        with closing(page.render(scale=scale, grayscale=True)) as bitmap:
            return bitmap.to_pil().convert("L")


def clean_pdf_text(text: str) -> str:
    text = normalize(text)
    text = re.sub(r"(?<=\w)[\u00ad\u2010-]\n(?=[a-z])", "", text)
    return text.replace("\u00ad", "")


def has_large_page_image(data: bytes, index: int) -> bool:
    # A logo on a sparse, otherwise selectable cover page does not require OCR.
    with PDF_RENDER_LOCK, pdfium.PdfDocument(data) as doc, closing(doc[index]) as page:
        x0, y0, x1, y1 = page.get_bbox()
        page_area = (x1 - x0) * (y1 - y0)
        for image in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
            left, bottom, right, top = image.get_bounds()
            width = max(0, min(x1, right) - max(x0, left))
            height = max(0, min(y1, top) - max(y0, bottom))
            if page_area > 0 and width * height >= page_area * 0.25:
                return True
    return False


def extract_pdf(data: bytes) -> list[tuple[int, str]]:
    pdf = PdfReader(BytesIO(data))
    if pdf.is_encrypted:
        raise ValueError("Unlock the PDF before importing it.")
    if len(pdf.pages) > 600:
        raise ValueError("Split PDFs longer than 600 pages before importing.")
    sections = []
    total = 0
    executable = find_tesseract()
    started = monotonic()
    for number, page in enumerate(pdf.pages, 1):
        text = clean_pdf_text(page.extract_text() or "")
        words = re.findall(r"[^\W\d_]{2,}", text)
        # Scanned slides often have a selectable footer, page number or decorative bullet layer.
        sparse_scan = len(words) < 40 and bool(page.images)
        unusable = not any(char.isalnum() for char in text)
        damaged = text.count("\ufffd") > len(words)
        if sparse_scan or damaged or (unusable and page.get_contents() is not None):
            if executable:
                if monotonic() - started > 180:
                    raise ValueError("PDF OCR took too long. Split the scanned pages into smaller PDFs and retry.")
                try:
                    read = clean_pdf_text(read_printed_text(pdf_page_image(data, number - 1), executable))
                except NoReadableText:
                    read = ""
                except pdfium.PdfiumError as error:
                    raise ValueError(f"Could not render PDF page {number} for OCR.") from error
                if read:
                    # Keep formulae and usable native lines that OCR missed.
                    flat = " ".join(read.casefold().split())
                    extra = [line for line in text.splitlines() if any(c.isalnum() for c in line)
                             and " ".join(line.casefold().split()) not in flat]
                    text = "\n".join([read, *extra])
            elif damaged or (sparse_scan and (not words or has_large_page_image(data, number - 1))):
                raise ValueError(f"PDF page {number} needs OCR. Install Tesseract with English data and import again.")
        if any(char.isalnum() for char in text):
            total += len(text)
            if total > MAX_TEXT:
                raise ValueError("This PDF exceeds one million extracted characters. Split it before importing.")
            sections.append((number, text))
    if not sections:
        raise ValueError("No readable text found in this PDF. Check the scan quality and English OCR installation.")
    return sections


def extract_docx(data: bytes) -> str:
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.getinfo("word/document.xml")
        if document_xml.file_size > MAX_UPLOAD:
            raise ValueError("The extracted Word document is too large.")
        root = fromstring(archive.read(document_xml))
    paragraphs = root.iter(f"{WORD_NS}p")
    return "\n\n".join("".join(part.text or "" for part in p.iter(f"{WORD_NS}t")) for p in paragraphs)


def extract_html(data: bytes, fallback_title: str) -> tuple[str, str, str]:
    soup = BeautifulSoup(data, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else fallback_title
    canonical = soup.select_one('link[rel="canonical"]')
    try:
        url = valid_url(str(canonical.get("href", ""))) if canonical else ""
    except ValueError:
        url = ""
    for element in soup(["script", "style", "noscript", "nav", "header", "footer", "template", "aside"]):
        element.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return title or fallback_title, main.get_text(" ", strip=True), url


def from_file(filename: str, data: bytes, title: str = "", url: str = "") -> Document:
    filename = Path(filename.replace("\\", "/")).name[:220]
    kind = TYPES.get(Path(filename).suffix.lower())
    if not kind:
        raise ValueError("Choose a PDF, TXT, Markdown, DOCX, HTML, or supported screenshot.")
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError("Choose a non-empty file up to 16 MB.")
    title = title.strip() or Path(filename).stem
    if kind == "pdf":
        sections = extract_pdf(data)
    elif kind == "docx":
        sections = [(None, extract_docx(data))]
    elif kind == "webpage":
        page_title, content, canonical = extract_html(data, title)
        return Document(page_title[:240], kind, [(None, content)], valid_url(url) or canonical, filename)
    elif kind == "image":
        sections = [(None, extract_image(data))]
    else:
        sections = [(None, decode(data))]
    return Document(title[:240], kind, sections, filename=filename, original=data)


def chunk_sections(document: Document, tokenizer) -> list[tuple[int | None, str]]:
    sections = [(page, normalize(text)) for page, text in document.sections]
    if sum(len(text) for _, text in sections) > MAX_TEXT:
        raise ValueError("This source exceeds one million extracted characters. Split it before importing.")
    chunks = []
    for page, text in sections:
        if not text:
            continue
        # Use token offsets so long passages fit the encoder and keep their original wording.
        offsets = tokenizer.encode(text, add_special_tokens=False).offsets
        if not offsets:
            continue
        if len(offsets) <= 192:
            chunks.append((page, text))
            continue
        start = 0
        while start < len(offsets):
            end = min(start + 192, len(offsets))
            left = offsets[start][0]
            right = offsets[end - 1][1]
            chunks.append((page, text[left:right].strip()))
            if end == len(offsets):
                break
            start = end - 32
        if len(chunks) > 4000:
            raise ValueError("This source has too many passages. Split it before importing.")
    if not chunks:
        raise ValueError("No searchable passages were found in this source.")
    return chunks
