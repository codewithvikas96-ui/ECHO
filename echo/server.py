from datetime import date
from functools import lru_cache
from base64 import b64encode
from hmac import compare_digest
from os import environ
from time import perf_counter
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator
from pypdf.errors import PdfReadError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import accounts
from .archive import Archive
from .embeddings import Embedder
from .demo import evaluate, seed
from .extract import Document, MAX_TEXT, MAX_UPLOAD, from_file, valid_url
from .model_setup import DATA_DIR, MODEL_ID, ROOT
from .ocr import find_tesseract

Kind = Literal["pdf", "text", "docx", "image", "webpage", "note"]
Mode = Literal["semantic", "contextual", "keyword"]

app = FastAPI(title="ECHO", version="1.0.0")
phone_address = environ.get("ECHO_PHONE_ADDRESS")
phone_auth = None
if phone_address:
    password = environ["ECHO_PHONE_PASSWORD"]
    if not password:
        raise RuntimeError("A phone session requires a password.")
    phone_auth = b"Basic " + b64encode(f"echo:{password}".encode("ascii"))
allowed_hosts = [phone_address] if phone_address else ["127.0.0.1", "localhost", "[::1]"]
accounts.initialize()
app.include_router(accounts.router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    if request.url.path.startswith("/api/account/"):
        return JSONResponse({"detail": "Use a 3–40 character username with letters, digits, dots, underscores or hyphens, and a password up to 256 characters."}, status_code=422)
    return await request_validation_exception_handler(request, error)


@lru_cache(maxsize=1)
def shared_model():
    return Embedder(DATA_DIR)


@lru_cache(maxsize=16)
def account_archive(user_id: str):
    return Archive(DATA_DIR / "users" / str(user_id), model_factory=shared_model)


def private_archive(user_id: Annotated[str, Depends(accounts.require_user)]):
    return account_archive(user_id)


PrivateArchive = Annotated[Archive, Depends(private_archive)]


@app.middleware("http")
async def local_browser_only(request: Request, call_next):
    if phone_auth is not None and not compare_digest(request.headers.get("authorization", "").encode(), phone_auth):
        return Response("Sign in with the credentials shown on your PC.", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="ECHO phone test"', "Cache-Control": "no-store"})
    origin = request.headers.get("origin")
    if request.url.path.startswith("/api/"):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("x-echo-request") != "1":
            return Response("Use the ECHO app to make changes.", status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return Response("Open ECHO directly on your device.", status_code=403)
        if origin:
            if phone_address:
                allowed_origin = origin == str(request.base_url).rstrip("/")
            else:
                allowed_origin = urlsplit(origin).hostname in {"127.0.0.1", "localhost", "::1"}
            if not allowed_origin:
                return Response("Open ECHO directly on your device.", status_code=403)
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_UPLOAD + 65_536:
            return Response("Files must be 16 MB or smaller.", status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# Validate Host before the authentication and origin middleware.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)


class SearchRequest(BaseModel):
    query: Annotated[str, Field(min_length=2, max_length=1000)]
    mode: Mode = "contextual"
    kinds: list[Kind] = Field(default_factory=list, max_length=6)
    date_from: date | None = None
    date_to: date | None = None
    top_k: int = Field(default=8, ge=1, le=30)

    @field_validator("query", mode="before")
    @classmethod
    def strip_query(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def date_order(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("The start date must precede the end date.")
        return self


class TextImport(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=MAX_TEXT)
    kind: Literal["note", "webpage"] = "note"
    url: str = Field(default="", max_length=2048)
    source_date: date | None = None


def require_model():
    if not all((DATA_DIR / "model" / name).is_file() for name in ("model.onnx", "tokenizer.json")):
        raise HTTPException(503, "The local model is missing. Run python -m echo.model_setup and try again.")


@app.get("/api/health")
def health():
    return {
        "model": MODEL_ID,
        "model_ready": all((DATA_DIR / "model" / name).is_file() for name in ("model.onnx", "tokenizer.json")),
        "ocr_available": bool(find_tesseract()),
        "vector_index": "FAISS",
        "storage": "SQLite",
    }


@app.get("/api/sources")
def sources(archive: PrivateArchive):
    return archive.list_sources()


@app.get("/api/sources/{source_id}")
def source_detail(source_id: int, archive: PrivateArchive):
    result = archive.detail(source_id)
    if result is None:
        raise HTTPException(404, "This source is no longer in your archive.")
    return result


@app.get("/api/sources/{source_id}/original")
def original(source_id: int, archive: PrivateArchive):
    source = archive.original(source_id)
    if not source or source["original"] is None:
        raise HTTPException(404, "No file was retained for this source.")
    is_pdf = source["kind"] == "pdf"
    disposition = "inline" if is_pdf else "attachment"
    return Response(
        source["original"],
        media_type="application/pdf" if is_pdf else "application/octet-stream",
        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(source['filename'], safe='')}"},
    )


@app.post("/api/sources/import")
def import_file(
    archive: PrivateArchive,
    file: Annotated[UploadFile, File()],
    source_date: Annotated[date | None, Form()] = None,
    url: Annotated[str, Form(max_length=2048)] = "",
):
    require_model()
    try:
        document = from_file(file.filename or "", file.file.read(MAX_UPLOAD + 1), url=url)
        source_id, created = archive.add(document, source_date)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except (PdfReadError, BadZipFile, KeyError, ParseError, DefusedXmlException) as error:
        raise HTTPException(422, "The file could not be parsed. Verify the format and try another copy.") from error
    return {"id": source_id, "created": created}


@app.post("/api/sources/text")
def import_text(data: TextImport, archive: PrivateArchive):
    require_model()
    try:
        title = data.title.strip()
        if not title:
            raise ValueError("Enter a title for this source.")
        url = valid_url(data.url) if data.kind == "webpage" else ""
        document = Document(title, data.kind, [(None, data.content)], url)
        source_id, created = archive.add(document, data.source_date)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"id": source_id, "created": created}


@app.delete("/api/sources/{source_id}", status_code=204)
def delete_source(source_id: int, archive: PrivateArchive):
    if not archive.delete(source_id):
        raise HTTPException(404, "This source is no longer in your archive.")


@app.post("/api/search")
def search(data: SearchRequest, archive: PrivateArchive):
    if data.mode != "keyword":
        require_model()
    start = perf_counter()
    results = archive.search(data.query, data.mode, data.kinds, data.date_from, data.date_to, data.top_k)
    return {"results": results, "latency_ms": round((perf_counter() - start) * 1000, 1)}


@app.post("/api/demo")
def add_demo(archive: PrivateArchive):
    require_model()
    return {"added": seed(archive)}


@app.post("/api/evaluation")
def run_evaluation(archive: PrivateArchive):
    require_model()
    return evaluate(archive)


DIST = ROOT / "dist"
if (DIST / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def frontend():
        return FileResponse(DIST / "index.html")

    @app.get("/echo.svg", include_in_schema=False)
    def favicon():
        return FileResponse(DIST / "echo.svg")

