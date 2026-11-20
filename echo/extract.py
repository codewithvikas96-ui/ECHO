from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile
import re
import unicodedata

from bs4 import BeautifulSoup
from defusedxml.ElementTree import fromstring
from pypdf import PdfReader

from .ocr import extract_image

MAX_UPLOAD = 16 * 1024 * 1024
MAX_TEXT = 1_000_000
TYPES = {
    ".pdf": "pdf", ".txt": "text", ".md": "text", ".docx": "docx",
    ".html": "webpage", ".htm": "webpage",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
}
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


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


def extract_pdf(data: bytes) -> list[tuple[int, str]]:
    pdf = PdfReader(BytesIO(data))
    if pdf.is_encrypted:
        raise ValueError("Unlock the PDF before importing it.")
    if len(pdf.pages) > 600:
        raise ValueError("Split PDFs longer than 600 pages before importing.")
    return [(number, page.extract_text() or "") for number, page in enumerate(pdf.pages, 1)]


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
        raise ValueError("No readable text was found. For a scanned PDF, import a screenshot with OCR.")
    return chunks
