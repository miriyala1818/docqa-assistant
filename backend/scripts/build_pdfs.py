"""Render the HTML sources in corpus_src/ into multi-page PDFs in knowledge_base/.

Each page gets a repeating running header and a "Page N" footer, so the PDF parser's
header/footer removal is exercised on realistic input.
"""
import io
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent              # backend/scripts
SRC = HERE / "corpus_src"
OUT = HERE.parent.parent / "knowledge_base"         # docqa-assistant/knowledge_base

PDFS = {
    "orbitly_user_manual_v3.1.html": ("orbitly_user_manual_v3.1.pdf", "Orbitly User Manual v3.1 - Internal"),
    "orbitly_admin_handbook.html": ("orbitly_admin_handbook.pdf", "Orbitly Administrator Handbook v1.4 - Internal"),
}


def render(src: Path, dst: Path, header: str) -> int:
    story = pymupdf.Story(html=src.read_text(encoding="utf-8"))
    mediabox = pymupdf.paper_rect("a5")  # small pages -> several pages per document
    where = mediabox + (36, 54, -36, -54)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    more = 1
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()

    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    for i, page in enumerate(doc, start=1):
        page.insert_text((36, 30), header, fontsize=7, color=(0.4, 0.4, 0.4))
        page.insert_text((36, mediabox.height - 24), f"Page {i}", fontsize=7, color=(0.4, 0.4, 0.4))
    doc.set_metadata({"title": src.stem.replace("_", " ").title(), "producer": "docqa corpus builder"})
    doc.save(str(dst))
    n = doc.page_count
    doc.close()
    return n


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for src_name, (dst_name, header) in PDFS.items():
        pages = render(SRC / src_name, OUT / dst_name, header)
        print(f"{dst_name}: {pages} pages")
