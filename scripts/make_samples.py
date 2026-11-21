"""Create demo and test files, with and without the WALLED_SENSITIVE marker.

    python scripts/make_samples.py [output-dir]      (default: samples/)

Needs Pillow and reportlab (both only for generating samples, not for running the agent).
"""

from __future__ import annotations

import io
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

MARKER = "WALLED_SENSITIVE"


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def text_image(lines: list[str], *, size: int = 34, dark: bool = False, width: int = 1100) -> Image.Image:
    bg, fg = ((33, 33, 33), (236, 236, 236)) if dark else ((255, 255, 255), (20, 20, 20))
    f = font(size)
    height = 60 + int(len(lines) * size * 1.6)
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((40, 30 + i * size * 1.6), line, fill=fg, font=f)
    return img


def pdf(path: pathlib.Path, pages: list[list[str]], *, split_marker: bool = False) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    for lines in pages:
        y = 780
        for line in lines:
            if split_marker and MARKER in line:
                before, after = line.split(MARKER, 1)
                c.drawString(72, y, before + "WALLED_")
                c.drawString(72 + c.stringWidth(before + "WALLED_ "), y, "SENSITIVE" + after)
            else:
                c.drawString(72, y, line)
            y -= 18
        c.showPage()
    c.save()


def scanned_pdf(path: pathlib.Path, img: Image.Image) -> None:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawImage(ImageReader(buf), 40, 400, width=515, height=515 * img.height / img.width)
    c.showPage()
    c.save()


def main(out: pathlib.Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    memo = ["Quarterly board memo", "", f"Classification: {MARKER}", "Revenue forecast: 12% growth", "Do not share outside the company."]
    clean = ["Team lunch menu", "", "Monday: pasta", "Tuesday: tacos", "Wednesday: salad bowls"]

    (out / "secret_notes.txt").write_text("\n".join(memo), encoding="utf-8")
    (out / "secret_notes_utf16.txt").write_text("\n".join(memo), encoding="utf-16")
    (out / "clean_notes.txt").write_text("\n".join(clean), encoding="utf-8")

    pdf(out / "secret_report.pdf", [["Annual report", "Page one is harmless."], ["Appendix", f"Tag: {MARKER}"]])
    pdf(out / "secret_report_split.pdf", [memo], split_marker=True)
    pdf(out / "clean_report.pdf", [clean])
    scanned_pdf(out / "secret_scanned.pdf", text_image(memo, size=40))

    text_image(memo).save(out / "secret_screenshot.png")
    text_image(memo, dark=True).save(out / "secret_dark_mode.png")
    text_image([f"label: {MARKER}"], size=18, width=420).save(out / "secret_small_text.png")
    photo = text_image(memo, size=44).rotate(3, expand=True, fillcolor=(235, 235, 230)).filter(ImageFilter.GaussianBlur(0.8))
    photo.convert("RGB").save(out / "secret_photo.jpg", quality=80)
    text_image(clean).save(out / "clean_screenshot.png")
    print(f"samples written to {out}")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "samples"))
