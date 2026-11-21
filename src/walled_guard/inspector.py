"""Find the sensitive marker in prompts and uploaded files.

Supported inputs:
* text  — any encoding we can recognise (UTF-8, UTF-16 with or without BOM, Latin-1)
* PDF   — the text layer of every page, the raw (decompressed) content streams, and,
          for scanned PDFs without a text layer, OCR of the embedded page images
* image — OCR (Tesseract) on several preprocessed variants, plus any text stored in
          the file itself (EXIF / PNG text chunks)

Matching is exact for typed text. Text that came out of a PDF or OCR is also matched
after whitespace is removed (PDFs often split one word into several runs), and OCR
text is matched with a small table of look-alike characters, because Tesseract reads
"I" as "l" or "1" and "_" as a space surprisingly often.
"""

from __future__ import annotations

import io
import re
import zlib
from dataclasses import dataclass, field

from . import ocr
from .rules import Hit, RuleEngine, dedupe

MARKER = "WALLED_SENSITIVE"
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_STREAM = 16 * 1024 * 1024            # largest decompressed PDF stream we read
MAX_INFLATED = 64 * 1024 * 1024          # total decompressed bytes per PDF

TEXT_EXTENSIONS = {
    "txt", "text", "md", "markdown", "csv", "tsv", "json", "jsonl", "log", "xml", "html", "htm",
    "yaml", "yml", "ini", "cfg", "conf", "toml", "env", "rtf", "tex", "sql", "py", "js", "ts",
    "java", "c", "h", "cpp", "cs", "go", "rb", "php", "sh", "srt", "vtt",
}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "heic", "heif"}


@dataclass
class Finding:
    """Result of inspecting one item."""

    name: str
    kind: str                       # "text" | "pdf" | "image" | "other"
    found: bool = False
    where: list[str] = field(default_factory=list)   # e.g. ["page 2"], ["OCR"], ["content"]
    error: str | None = None        # set when the item could not be inspected fully
    hits: list[Hit] = field(default_factory=list)    # which rules matched (masked samples only)

    def add(self, hits: list[Hit], where: str) -> None:
        if hits:
            self.hits = dedupe(self.hits + hits)
            self.found = True
            if where not in self.where:
                self.where.append(where)

    def to_json(self) -> dict[str, object]:
        return {"name": self.name, "kind": self.kind, "found": self.found,
                "where": self.where, "error": self.error, "hits": [h.to_json() for h in self.hits]}


def _default_engine() -> RuleEngine:
    from . import config
    return config.load().engine()


# ---------------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------------
_WS = re.compile(r"\s+")
_OCR_LOOKALIKES = str.maketrans({"0": "O", "1": "I", "l": "I", "|": "I", "!": "I", "5": "S", "$": "S", "€": "E"})
_TARGET_COMPACT = re.sub(r"[^A-Z]", "", MARKER)          # "WALLEDSENSITIVE"
_TARGET_FOLDED = _TARGET_COMPACT.replace("L", "I")        # OCR confuses 1 / l / I / L


def contains_marker(text: str) -> bool:
    """Exact match: what a user typed into a prompt or a plain text file."""
    return MARKER in text


def contains_marker_extracted(text: str) -> bool:
    """For text extracted from PDFs: tolerate whitespace and line breaks inside the marker."""
    return contains_marker(text) or MARKER in _WS.sub("", text)


def contains_marker_ocr(text: str) -> bool:
    """For OCR output: tolerate look-alike characters and a missing or misread underscore."""
    if contains_marker_extracted(text):
        return True
    plain = re.sub(r"[^A-Z]", "", text.upper())
    mapped = re.sub(r"[^A-Z]", "", text.translate(_OCR_LOOKALIKES).upper()).replace("L", "I")
    return _TARGET_COMPACT in plain or _TARGET_FOLDED in mapped


# ---------------------------------------------------------------------------------
# type detection
# ---------------------------------------------------------------------------------
def classify(name: str, mime: str, data: bytes) -> str:
    head = data[:16]
    if head.startswith(b"%PDF") or data[:1024].find(b"%PDF-") != -1:
        return "pdf"
    if (head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff") or head[:6] in (b"GIF87a", b"GIF89a")
            or head.startswith(b"BM") or (head[:4] == b"RIFF" and data[8:12] == b"WEBP")
            or head[:4] in (b"II*\x00", b"MM\x00*") or data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1")):
        return "image"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if mime == "application/pdf" or ext == "pdf":
        return "pdf"
    if mime.startswith("image/") or ext in IMAGE_EXTENSIONS:
        return "image"
    if mime.startswith("text/") or ext in TEXT_EXTENSIONS or mime in ("application/json", "application/xml"):
        return "text"
    return "text" if data and _looks_textual(data) else "other"


def _looks_textual(data: bytes) -> bool:
    sample = data[:4096]
    if not sample:
        return True
    if sample.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return True
    if b"\x00" in sample:
        return False
    printable = sum(32 <= b < 127 or b in (9, 10, 13) or b >= 128 for b in sample)
    return printable / len(sample) > 0.9


def decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    if len(data) >= 4 and data[1:2] == b"\x00" and data[3:4] == b"\x00":   # UTF-16-LE without BOM
        return data.decode("utf-16-le", "replace")
    if len(data) >= 4 and data[0:1] == b"\x00" and data[2:3] == b"\x00":   # UTF-16-BE without BOM
        return data.decode("utf-16-be", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


# ---------------------------------------------------------------------------------
# inspectors
# ---------------------------------------------------------------------------------
def inspect_prompt(text: str, engine: RuleEngine | None = None) -> list[Hit]:
    """Rules that match a typed prompt (empty list: nothing sensitive)."""
    return (engine or _default_engine()).scan(text, "typed")


def inspect_file(name: str, data: bytes, mime: str = "", engine: RuleEngine | None = None) -> Finding:
    engine = engine or _default_engine()
    kind = classify(name, mime, data)
    finding = Finding(name=name, kind=kind)
    finding.add(engine.scan_filename(name), "file name")
    if len(data) > MAX_FILE_BYTES:
        finding.error = f"file larger than {MAX_FILE_BYTES // (1024 * 1024)} MB was not inspected"
        return finding
    try:
        if kind == "pdf":
            _inspect_pdf(data, finding, engine)
        elif kind == "image":
            _inspect_image(data, finding, engine)
        elif kind == "text" or data:
            finding.add(engine.scan(decode_text(data), "extracted"), "content")
            if not finding.hits and kind == "other":
                finding.add(engine.scan(data.decode("latin-1"), "extracted", builtins=False), "content")
    except Exception as exc:  # never let a malformed file crash the agent
        finding.error = f"could not inspect: {type(exc).__name__}"
    return finding


def _inspect_pdf(data: bytes, finding: Finding, engine: RuleEngine) -> None:
    # 1. text layer, page by page
    pages_without_text: list[int] = []
    unreadable = False
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                finding.error = "encrypted PDF could not be opened"
                return
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            hits = engine.scan(text, "extracted")
            if hits:
                finding.add(hits, f"page {number}")
            elif not text.strip():
                pages_without_text.append(number)
        if finding.found:
            return
    except ImportError:
        reader = None
    except Exception:
        reader = None
        unreadable = True

    # 2. raw streams (catches text that pypdf could not lay out, metadata, form fields)
    raw_hits = _raw_pdf_hits(data, engine)
    if raw_hits:
        finding.add(raw_hits, "PDF content stream")
        return
    if unreadable:
        finding.error = "PDF could not be read (damaged or unsupported)"
        return

    # 3. scanned pages: OCR the embedded images
    if reader is not None and pages_without_text and ocr.available():
        for number in pages_without_text[:50]:
            for image in reader.pages[number - 1].images:
                hits = _first_ocr_hits(image.data, engine)
                if hits:
                    finding.add(hits, f"page {number} (OCR)")
                    break
    elif pages_without_text and not ocr.available():
        finding.error = "scanned pages not checked: OCR engine (Tesseract) is not installed"


_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)


def _raw_pdf_hits(data: bytes, engine: RuleEngine) -> list[Hit]:
    """Search decompressed content streams (split text runs, form fields, metadata)."""
    found: dict[str, Hit] = {}

    def scan(chunk: str) -> None:
        # Built-in number detectors are skipped here: raw PDF syntax is full of digit runs
        # (positions, xref tables). They run on the extracted page text instead.
        for h in engine.scan(chunk, "extracted", builtins=False):
            found.setdefault(h.rule_id, h)

    scan(data.decode("latin-1"))
    budget = MAX_INFLATED                      # a "zip bomb" stream must not exhaust memory
    for match in _STREAM.finditer(data):
        if budget <= 0:
            break
        try:
            inflated = zlib.decompressobj().decompress(match.group(1), min(budget, MAX_STREAM))
        except zlib.error:
            continue
        budget -= len(inflated)
        scan(inflated.decode("latin-1"))       # scanned and dropped right away
    return list(found.values())


def _first_ocr_hits(data: bytes, engine: RuleEngine) -> list[Hit]:
    for text in ocr.texts(data):          # cheapest variant first; stop at the first that matches
        hits = engine.scan(text, "ocr")
        if hits:
            return hits
    return []


def _inspect_image(data: bytes, finding: Finding, engine: RuleEngine) -> None:
    meta = engine.scan(data.decode("latin-1"), "extracted", builtins=False)   # EXIF, PNG text, XMP
    if meta:
        finding.add(meta, "image metadata")
        return
    if not ocr.available():
        finding.error = "image not checked: OCR engine (Tesseract) is not installed"
        return
    finding.add(_first_ocr_hits(data, engine), "image text (OCR)")
