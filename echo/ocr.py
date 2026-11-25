from io import BytesIO
from math import sqrt
import os
from pathlib import Path
import shutil
import subprocess
from time import monotonic

from PIL import Image, ImageOps, ImageStat, UnidentifiedImageError


class NoReadableText(ValueError):
    """The image was processed successfully but contained no recognized text."""


def find_tesseract() -> str | None:
    executable = shutil.which("tesseract")
    if executable or os.name != "nt":
        return executable
    for variable, subdir in (("ProgramFiles", "Tesseract-OCR"), ("LOCALAPPDATA", "Programs/Tesseract-OCR")):
        if root := os.environ.get(variable):
            candidate = Path(root) / subdir / "tesseract.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def grayscale(data: bytes) -> Image.Image:
    with Image.open(BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as source:
        if source.width * source.height > 32_000_000:
            raise ValueError("Crop the image to its text or resize it to 32 megapixels or less.")
        image = ImageOps.exif_transpose(source)
    if image.has_transparency_data:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        gray = rgba.convert("L")
        if alpha.getextrema()[1] == 0:
            return Image.new("L", gray.size, 255)
        # Preserve light lettering on transparent backgrounds as well as dark lettering.
        background = 0 if ImageStat.Stat(gray, mask=alpha).mean[0] > 150 else 255
        return Image.composite(gray, Image.new("L", gray.size, background), alpha)
    return image.convert("L")


def recognize(executable: str, image: Image.Image, mode: int, deadline: float) -> tuple[str, float, float]:
    encoded = BytesIO()
    image.save(encoded, "PNG", compress_level=1)
    output = subprocess.run(
        [executable, "stdin", "stdout", "-l", "eng", "--psm", str(mode), "tsv"],
        input=encoded.getvalue(), capture_output=True, check=True, timeout=max(0.01, deadline - monotonic()),
    )
    lines: dict[tuple[str, ...], list[str]] = {}
    weighted_confidence = 0.0
    characters = 0
    for row in output.stdout.decode("utf-8", errors="replace").splitlines():
        fields = row.split("\t", 11)
        if len(fields) != 12 or fields[0] != "5":
            continue
        word = fields[11].strip()
        if word:
            lines.setdefault(tuple(fields[1:5]), []).append(word)
            length = sum(ch.isalnum() for ch in word)
            weighted_confidence += length * float(fields[10])
            characters += length
    text = "\n".join(" ".join(words) for words in lines.values())
    confidence = weighted_confidence / max(1, characters)
    # Reward additional legible text without letting long, noisy readings dominate.
    return text, confidence, (confidence / 100) ** 3 * sqrt(characters)


def extract_image(data: bytes) -> str:
    executable = find_tesseract()
    if not executable:
        raise ValueError("Install Tesseract OCR on the computer running ECHO to import images.")
    try:
        gray = grayscale(data)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("ECHO could not open this image. Export it as PNG, JPEG, or WebP and try again.") from error
    return read_printed_text(gray, executable)


def read_printed_text(gray: Image.Image, executable: str) -> str:
    if gray.getextrema()[0] == gray.getextrema()[1]:
        raise NoReadableText("No readable text found. Crop around clear, printed English text and try again.")

    # Use a light background and enlarge small glyphs while bounding the OCR working image.
    if ImageStat.Stat(gray).median[0] < 128:
        gray = ImageOps.invert(gray)
    enhanced = ImageOps.autocontrast(gray)
    scale = min(3, sqrt(6_000_000 / (gray.width * gray.height)), 3200 / max(gray.size))
    enhanced = enhanced.resize(tuple(max(1, round(n * scale)) for n in gray.size), Image.Resampling.LANCZOS)
    enhanced = ImageOps.expand(enhanced, border=12, fill=255)

    best = ("", 0.0, 0.0)
    deadline = monotonic() + 30
    try:
        # Keep the native reading as a candidate for photos and dense pages.
        for image, mode in ((gray, 3), (enhanced, 11)):
            candidate = recognize(executable, image, mode, deadline)
            best = max(best, candidate, key=lambda item: item[2])
        if best[1] < 80:
            for rotation in (None, Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180):
                upright = enhanced if rotation is None else enhanced.transpose(rotation)
                candidate = recognize(executable, upright, 6, deadline)
                best = max(best, candidate, key=lambda item: item[2])
                if best[1] >= 85:
                    break
    except subprocess.TimeoutExpired as error:
        if not best[0]:
            raise ValueError("OCR timed out. Crop around the text and try again.") from error
    except (subprocess.CalledProcessError, OSError) as error:
        if not best[0]:
            raise ValueError("OCR could not process this image. Check that Tesseract has English language data.") from error
    if best[2] == 0:
        raise NoReadableText("No readable text found. Crop around clear, printed English text and try again.")
    return best[0]
