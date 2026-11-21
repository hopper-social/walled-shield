"""OCR with Tesseract, tuned for "is this word in the picture?" rather than perfect text.

Screenshots and photos vary a lot (dark mode, small fonts, low contrast), so the image is
tried in several variants and the caller stops at the first variant whose text matches.
Uses pytesseract when installed, otherwise calls the ``tesseract`` command directly.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator

MAX_PIXELS = 40_000_000     # refuse decompression bombs
TIMEOUT_S = 20


# Browsers start the agent with a short PATH, and the Windows installer does not add
# Tesseract to PATH at all, so also look in the usual install locations.
_KNOWN = ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract", "/usr/bin/tesseract",
          r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]


def binary() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    return next((p for p in _KNOWN if os.path.isfile(p)), None)


def available() -> bool:
    return binary() is not None


def _variants(data: bytes) -> Iterator["object"]:
    from PIL import Image, ImageFilter, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with Image.open(io.BytesIO(data)) as source:
        source.seek(0)                                  # first frame of GIF/TIFF/WebP
        img = ImageOps.exif_transpose(source).convert("RGB")
    gray = ImageOps.grayscale(img)
    if max(gray.size) < 1400:                           # small text OCRs much better upscaled
        factor = 1400 / max(gray.size)
        gray = gray.resize((int(gray.width * factor), int(gray.height * factor)), Image.Resampling.LANCZOS)
    contrast = ImageOps.autocontrast(gray)
    yield contrast                                      # 1. as is
    yield ImageOps.invert(contrast)                     # 2. light text on dark background
    yield contrast.point(lambda v: 255 if v > 150 else 0).filter(ImageFilter.MedianFilter(3))  # 3. binarised


def _ocr(image: "object", psm: int) -> str:
    exe = binary() or "tesseract"
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = exe
        return str(pytesseract.image_to_string(image, config=f"--psm {psm}", timeout=TIMEOUT_S))
    except ImportError:
        pass
    fd, path = tempfile.mkstemp(suffix=".png")          # reopened by tesseract, so not auto-deleted (Windows)
    os.close(fd)
    try:
        image.save(path)                                # type: ignore[attr-defined]
        result = subprocess.run([exe, path, "stdout", "--psm", str(psm)],
                                capture_output=True, text=True, timeout=TIMEOUT_S)
        return result.stdout
    finally:
        os.unlink(path)


def texts(data: bytes) -> Iterator[str]:
    """Yield OCR text for successive image variants (cheapest and most likely first)."""
    if not available():
        return
    for index, variant in enumerate(_variants(data)):
        yield _ocr(variant, 3 if index == 0 else 11)   # 3 = page layout, 11 = sparse text
        if index == 0:
            yield _ocr(variant, 11)
