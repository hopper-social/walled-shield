from pathlib import Path

import pytest

from walled_guard import inspector, ocr

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
needs_samples = pytest.mark.skipif(not SAMPLES.exists(), reason="run scripts/make_samples.py first")
needs_ocr = pytest.mark.skipif(not ocr.available(), reason="Tesseract not installed")


@pytest.mark.parametrize("text, hit", [
    ("hello", False),
    ("WALLED_SENSITIVE", True),
    ("prefix WALLED_SENSITIVE suffix", True),
    ("xxWALLED_SENSITIVExx", True),
    ("walled_sensitive", False),           # the marker is case-sensitive in typed text
    ("WALLED SENSITIVE", False),
    ("", False),
])
def test_prompt_matching(text, hit):
    assert bool(inspector.inspect_prompt(text)) is hit


def test_extracted_text_tolerates_line_breaks():
    assert inspector.contains_marker_extracted("WALLED_\nSENSITIVE")
    assert not inspector.contains_marker("WALLED_\nSENSITIVE")


def test_ocr_tolerates_lookalikes():
    assert inspector.contains_marker_ocr("WALLED SENS1TIVE")
    assert inspector.contains_marker_ocr("WA11ED_SENSITIVE")
    assert not inspector.contains_marker_ocr("WALLET SENSITIVE")


@pytest.mark.parametrize("name, data, kind", [
    ("x.pdf", b"%PDF-1.7 ...", "pdf"),
    ("renamed.txt", b"%PDF-1.4", "pdf"),            # magic bytes win over the extension
    ("x.png", b"\x89PNG\r\n\x1a\n", "image"),
    ("notes", b"just some text", "text"),
    ("blob.bin", b"\x00\x01\x02\x03", "other"),
    ("big.zip", b"", "other"),
    ("big.pdf", b"", "pdf"),
])
def test_classify(name, data, kind):
    assert inspector.classify(name, "", data) == kind


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "latin-1"])
def test_text_encodings(encoding):
    data = "Résumé — WALLED_SENSITIVE".encode(encoding) if encoding != "latin-1" else "Resume WALLED_SENSITIVE".encode(encoding)
    assert inspector.inspect_file("a.txt", data).found


def test_corrupt_pdf_reports_error_not_crash():
    r = inspector.inspect_file("broken.pdf", b"%PDF-1.4\n garbage")
    assert not r.found and r.error


@needs_samples
@pytest.mark.parametrize("path", sorted(SAMPLES.glob("*")) if SAMPLES.exists() else [], ids=lambda p: p.name)
def test_samples(path):
    if path.suffix.lower() in (".png", ".jpg") or "scanned" in path.name:
        if not ocr.available():
            pytest.skip("Tesseract not installed")
    r = inspector.inspect_file(path.name, path.read_bytes())
    assert r.found is path.name.startswith("secret_"), r


@needs_samples
def test_pdf_reports_page():
    r = inspector.inspect_file("secret_report.pdf", (SAMPLES / "secret_report.pdf").read_bytes())
    assert r.where == ["page 2"]


def test_image_without_ocr_is_unchecked(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: None)
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "PNG")
    r = inspector.inspect_file("x.png", buf.getvalue())
    assert not r.found and "Tesseract" in (r.error or "")


def test_marker_in_image_metadata_needs_no_ocr(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: None)
    from PIL import Image, PngImagePlugin
    import io
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "WALLED_SENSITIVE")
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "PNG", pnginfo=info)
    r = inspector.inspect_file("x.png", buf.getvalue())
    assert r.found and r.where == ["image metadata"]


def test_oversized_file_is_unchecked():
    r = inspector.inspect_file("huge.txt", b"a" * (inspector.MAX_FILE_BYTES + 1))
    assert not r.found and r.error
