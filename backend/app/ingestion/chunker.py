"""FR-02 Content preparation: section-aware chunking with contextual headers."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

from ..utils.textutil import plans_in, split_sentences
from .parsers import Block, ParsedDoc

MAX_TOKENS = 320      # target chunk size (approx. tokens = words * 1.3)
OVERLAP_TOKENS = 50   # carried into the next chunk of the same section
MIN_TOKENS = 12       # smaller sections are merged into the following sibling


def ntok(text: str) -> int:
    return int(len(text.split()) * 1.3) + 1


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    source: str
    fmt: str
    section_path: list[str]
    text: str                      # original text, used for citation excerpts
    page_start: int | None = None
    page_end: int | None = None
    anchor: str | None = None
    meta: dict = field(default_factory=dict)
    plans: list[str] = field(default_factory=list)

    @property
    def section(self) -> str:
        return " > ".join(self.section_path) if self.section_path else "Introduction"

    @property
    def embed_text(self) -> str:
        """Contextual header + text: what gets embedded and BM25-indexed."""
        return f"{self.doc_title} > {self.section}: {self.text}"

    @property
    def location(self) -> str:
        if self.page_start:
            return f"p. {self.page_start}" if self.page_start == self.page_end or not self.page_end \
                else f"pp. {self.page_start}-{self.page_end}"
        if self.anchor:
            return f"#{self.anchor}"
        return ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["section"] = self.section
        d["location"] = self.location
        return d


def _sections(doc: ParsedDoc):
    """Yield (heading_path, anchor, [blocks]) for each section with content."""
    stack: list[tuple[int, str]] = []
    anchor = None
    content: list[Block] = []
    title_norm = doc.title.strip().lower()
    root: str | None = None

    def path():
        # content directly under the document's own heading is labelled with that heading
        return [t for _, t in stack] or ([root] if root else [])

    for b in doc.blocks:
        if b.type == "heading":
            if content:
                yield path(), anchor, content
                content = []
            if b.level == 0 or (b.text.strip().lower() == title_norm and not stack):
                root = b.text  # the document's own heading/title is not part of child section paths
                anchor = b.anchor or anchor
                continue
            while stack and stack[-1][0] >= b.level:
                stack.pop()
            stack.append((b.level, b.text))
            anchor = b.anchor or anchor
        else:
            content.append(b)
    if content:
        yield path(), anchor, content


def _units(blocks: list[Block]):
    """Atomic units for packing: whole lists/tables (split by line only if huge), else sentences."""
    for b in blocks:
        if b.type in ("list", "table"):
            if ntok(b.text) <= MAX_TOKENS:
                yield b.text, b.page
            else:
                for line in b.text.split("\n"):
                    yield line, b.page
        else:
            for s in split_sentences(b.text):
                yield s, b.page


def chunk_document(doc: ParsedDoc) -> list[Chunk]:
    chunks: list[Chunk] = []
    sections = list(_sections(doc))
    # merge tiny sections into the next sibling so a lone sentence keeps useful context
    merged: list[tuple[list[str], str | None, list[Block]]] = []
    carry: list[Block] = []
    for i, (path, anchor, blocks) in enumerate(sections):
        blocks = carry + blocks
        carry = []
        size = sum(ntok(b.text) for b in blocks)
        nxt = sections[i + 1] if i + 1 < len(sections) else None
        if size < MIN_TOKENS and nxt and nxt[0][: len(path)] == path:
            carry = blocks
            continue
        merged.append((path, anchor, blocks))

    meta = {k: v for k, v in doc.meta.items() if k in ("version", "effective_date", "doc_type", "applies_to", "article_id")}
    for path, anchor, blocks in merged:
        units = list(_units(blocks))
        cur: list[tuple[str, int | None]] = []

        def emit(units_):
            text = _assemble(units_)
            pages = [p for _, p in units_ if p]
            chunks.append(Chunk(
                chunk_id=f"{doc.doc_id}#{len(chunks) + 1}", doc_id=doc.doc_id, doc_title=doc.title,
                source=doc.source, fmt=doc.fmt, section_path=list(path), text=text,
                page_start=min(pages) if pages else None, page_end=max(pages) if pages else None,
                anchor=anchor, meta=dict(meta), plans=plans_in(text),
            ))

        for u in units:
            if cur and sum(ntok(t) for t, _ in cur) + ntok(u[0]) > MAX_TOKENS:
                emit(cur)
                overlap: list[tuple[str, int | None]] = []
                for t, p in reversed(cur):
                    if sum(ntok(x) for x, _ in overlap) + ntok(t) > OVERLAP_TOKENS:
                        break
                    overlap.insert(0, (t, p))
                cur = overlap
            cur.append(u)
        if cur:
            emit(cur)
    return chunks


def _assemble(units: list[tuple[str, int | None]]) -> str:
    out = ""
    for t, _ in units:
        is_line = "\n" in t or re.match(r"^(\d+\.|[-*•])\s", t) or ";" in t and ":" in t
        if not out:
            out = t
        elif is_line or out.endswith("\n") or re.search(r"(^|\n)(\d+\.|[-*•])\s[^\n]*$", out):
            out = f"{out}\n{t}"
        else:
            out = f"{out} {t}"
    return out.strip()


def dedupe(chunks: list[Chunk]) -> list[Chunk]:
    """Drop near-identical chunks (same normalised text) across the collection."""
    seen: set[str] = set()
    out = []
    for c in chunks:
        h = hashlib.sha1(re.sub(r"\W+", " ", c.text.lower()).strip().encode()).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        out.append(c)
    return out
