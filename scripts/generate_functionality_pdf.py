#!/usr/bin/env python3
"""Generate the Acadex functionality guide PDF from Markdown using only stdlib."""
from __future__ import annotations

import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "Acadex_Functionality_Guide.md"
OUT = ROOT / "docs" / "Acadex_Functionality_Guide.pdf"

PAGE_W = 612
PAGE_H = 792
MARGIN_X = 54
MARGIN_TOP = 58
MARGIN_BOTTOM = 54
LINE_H = 13
BODY_SIZE = 9.5
TITLE_SIZE = 18
H1_SIZE = 14
H2_SIZE = 11
FONT = "F1"
BOLD = "F2"


def clean_inline(text: str) -> str:
    text = text.replace("**", "")
    text = text.replace("`", "")
    return text.rstrip()


def escape_pdf(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap_text(text: str, max_chars: int) -> list[str]:
    if not text:
        return [""]
    return textwrap.wrap(text, width=max_chars, break_long_words=False, replace_whitespace=False) or [""]


def parse_markdown(markdown: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(("p", clean_inline(" ".join(paragraph))))
            paragraph = []

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line:
            flush_paragraph()
            blocks.append(("space", ""))
            continue
        if line.startswith("# "):
            flush_paragraph()
            blocks.append(("title", clean_inline(line[2:])))
        elif line.startswith("## "):
            flush_paragraph()
            blocks.append(("h1", clean_inline(line[3:])))
        elif line.startswith("### "):
            flush_paragraph()
            blocks.append(("h2", clean_inline(line[4:])))
        elif line.startswith("- "):
            flush_paragraph()
            blocks.append(("bullet", clean_inline(line[2:])))
        else:
            paragraph.append(line)
    flush_paragraph()
    return blocks


def make_pdf(blocks: list[tuple[str, str]]) -> bytes:
    pages: list[list[str]] = []
    current: list[str] = []
    y = PAGE_H - MARGIN_TOP

    def new_page() -> None:
        nonlocal current, y
        if current:
            pages.append(current)
        current = []
        y = PAGE_H - MARGIN_TOP

    def ensure(lines_needed: int = 1, extra: int = 0) -> None:
        nonlocal y
        if y - (lines_needed * LINE_H) - extra < MARGIN_BOTTOM:
            new_page()

    def add_text(x: int, text: str, size: float = BODY_SIZE, font: str = FONT, leading: int = LINE_H) -> None:
        nonlocal y
        current.append(f"BT /{font} {size} Tf 1 0 0 1 {x} {y:.1f} Tm ({escape_pdf(text)}) Tj ET")
        y -= leading

    max_body_chars = 92
    max_bullet_chars = 86

    for kind, text in blocks:
        if kind == "space":
            y -= 3
            continue
        if kind == "title":
            ensure(2, 8)
            add_text(MARGIN_X, text, TITLE_SIZE, BOLD, 22)
            y -= 4
        elif kind == "h1":
            ensure(3, 6)
            y -= 5
            for line in wrap_text(text, 76):
                add_text(MARGIN_X, line, H1_SIZE, BOLD, 17)
        elif kind == "h2":
            ensure(2, 4)
            y -= 3
            for line in wrap_text(text, 84):
                add_text(MARGIN_X, line, H2_SIZE, BOLD, 15)
        elif kind == "bullet":
            lines = wrap_text(text, max_bullet_chars)
            ensure(len(lines), 2)
            add_text(MARGIN_X + 10, "- " + lines[0], BODY_SIZE, FONT)
            for line in lines[1:]:
                add_text(MARGIN_X + 22, line, BODY_SIZE, FONT)
        else:
            lines = wrap_text(text, max_body_chars)
            ensure(len(lines), 2)
            for line in lines:
                add_text(MARGIN_X, line, BODY_SIZE, FONT)
            y -= 2

    if current:
        pages.append(current)

    # Add footers now that page count is known.
    total = len(pages)
    for idx, page in enumerate(pages, start=1):
        footer = f"BT /{FONT} 8 Tf 1 0 0 1 {MARGIN_X} 30 Tm ({escape_pdf('Acadex Functionality Guide')}) Tj ET"
        num = f"BT /{FONT} 8 Tf 1 0 0 1 {PAGE_W - MARGIN_X - 70} 30 Tm ({escape_pdf(f'Page {idx} of {total}')}) Tj ET"
        page.extend([footer, num])

    objects: list[bytes] = []

    def add_obj(payload: str | bytes) -> int:
        if isinstance(payload, str):
            payload = payload.encode("latin-1", errors="replace")
        objects.append(payload)
        return len(objects)

    catalog_id = add_obj("<< /Type /Catalog /Pages 2 0 R >>")
    pages_placeholder_id = add_obj(b"")
    font_id = add_obj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    bold_id = add_obj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

    page_ids: list[int] = []
    for page in pages:
        stream = "\n".join(page).encode("latin-1", errors="replace")
        content_id = add_obj(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        page_id = add_obj(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /{FONT} {font_id} 0 R /{BOLD} {bold_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_placeholder_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()

    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx, payload in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{idx} 0 obj\n".encode())
        data.extend(payload)
        data.extend(b"\nendobj\n")
    xref_at = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    data.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        data.extend(f"{off:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    )
    return bytes(data)


def main() -> None:
    markdown = SRC.read_text(encoding="utf-8")
    # Replace em/en dashes and curly punctuation with PDF core-font-friendly equivalents.
    markdown = markdown.translate(str.maketrans({"—": "-", "–": "-", "“": '"', "”": '"', "’": "'"}))
    blocks = parse_markdown(markdown)
    OUT.write_bytes(make_pdf(blocks))
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
