"""FR-01 Multi-format ingestion.

Every parser returns the same intermediate form: a ParsedDoc with metadata and an ordered
list of Blocks (heading / para / list / table) that carry page (PDF) or anchor (HTML)
locations. Everything downstream is format-independent.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from ..utils.textutil import normalize

SUPPORTED = {".pdf": "pdf", ".html": "html", ".htm": "html", ".md": "markdown", ".markdown": "markdown", ".txt": "txt"}


@dataclass
class Block:
    type: str            # heading | para | list | table
    text: str
    level: int = 0       # heading level (1 = top)
    page: int | None = None
    anchor: str | None = None


@dataclass
class ParsedDoc:
    doc_id: str
    title: str
    source: str
    fmt: str
    sha256: str
    meta: dict = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)


# ---------------------------------------------------------------- metadata helpers
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), "%Y-%m-%d"),
    (re.compile(rf"\b(\d{{1,2}} (?:{_MONTHS}) \d{{4}})\b"), "%d %B %Y"),
    (re.compile(rf"\b((?:{_MONTHS}) \d{{1,2}},? \d{{4}})\b"), "%B %d %Y"),
]


def parse_date(text: str) -> str | None:
    for rx, fmt in _DATE_PATTERNS:
        m = rx.search(text)
        if m:
            try:
                return datetime.strptime(m.group(1).replace(",", ""), fmt).date().isoformat()
            except ValueError:
                continue
    return None


def sniff_meta(head_text: str, filename: str) -> dict:
    """Extract version / effective date / product from the first lines or filename."""
    meta: dict = {}
    m = re.search(r"\bversion[:\s]+v?(\d+(?:\.\d+)+)", head_text, re.IGNORECASE)
    if not m:
        m = re.search(r"[_\-\s]v(\d+(?:[._]\d+)+)", filename, re.IGNORECASE)
    if m:
        meta["version"] = m.group(1).replace("_", ".")
    m = re.search(r"(?:effective|last updated|updated|released)[:\s]+([^\n|]{6,40})", head_text, re.IGNORECASE)
    if m:
        d = parse_date(m.group(1))
        if d:
            meta["effective_date"] = d
    m = re.search(r"applies to[:\s]+([^\n]+)", head_text, re.IGNORECASE)
    if m:
        meta["applies_to"] = m.group(1).strip()[:80]
    return meta


def _doc_type(name: str, title: str) -> str:
    s = f"{name} {title}".lower()
    for key, t in (("release", "release_notes"), ("faq", "faq"), ("question", "faq"), ("manual", "manual"),
                   ("handbook", "manual"), ("guide", "guide"), ("polic", "policy"), ("limit", "reference"),
                   ("kb", "support_article"), ("support", "support_article")):
        if key in s:
            return t
    return "document"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _doc_id(path: Path) -> str:
    return re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")


# ---------------------------------------------------------------- PDF
_LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-•*])\s+")


def parse_pdf(path: Path) -> ParsedDoc:
    import pymupdf

    # open from memory: never holds a lock on the file (matters on Windows for temp uploads)
    doc = pymupdf.open(stream=path.read_bytes(), filetype="pdf")
    lines = []  # (page, y0, y1, size, bold, text, block_no)
    for pno, page in enumerate(doc, start=1):
        h = page.rect.height
        for bno, b in enumerate(page.get_text("dict")["blocks"]):
            for ln in b.get("lines", []):
                spans = [s for s in ln["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = normalize("".join(s["text"] for s in ln["spans"]))
                size = max(s["size"] for s in spans)
                bold = all((s["flags"] & 16) or "bold" in s["font"].lower() for s in spans)
                lines.append((pno, ln["bbox"][1] / h, ln["bbox"][3] / h, round(size, 1), bold, text, (pno, bno)))

    # Remove running headers/footers: short lines in the top/bottom 8% that repeat on many pages
    n_pages = doc.page_count
    edge = [re.sub(r"\d+", "#", l[5].lower()) for l in lines if l[1] < 0.08 or l[2] > 0.92]
    rep = {t for t, c in Counter(edge).items() if n_pages > 1 and c >= max(2, n_pages * 0.5)}
    lines = [l for l in lines if not ((l[1] < 0.08 or l[2] > 0.92) and re.sub(r"\d+", "#", l[5].lower()) in rep)]

    body_size = Counter(l[3] for l in lines).most_common(1)[0][0] if lines else 10
    heading_sizes = sorted({l[3] for l in lines if l[3] >= body_size * 1.12}, reverse=True)
    level_of = {s: i + 1 for i, s in enumerate(heading_sizes)}

    blocks: list[Block] = []
    cur: list[str] = []
    cur_key = None
    cur_page = None

    def flush():
        nonlocal cur, cur_key
        if cur:
            _emit_text_lines(cur, cur_page, blocks)
        cur, cur_key = [], None

    for pno, y0, y1, size, bold, text, key in lines:
        is_heading = (size in level_of or (bold and size >= body_size and len(text) < 80 and not text.endswith(".")))
        if is_heading:
            flush()
            level = level_of.get(size, len(heading_sizes) + 1)
            if blocks and blocks[-1].type == "heading" and blocks[-1].level == level and blocks[-1].page == pno \
                    and not re.match(r"^\d", text):
                blocks[-1].text += " " + text  # wrapped heading line
            else:
                blocks.append(Block("heading", text, level=level, page=pno))
            continue
        if key != cur_key:
            flush()
            cur_key, cur_page = key, pno
        cur.append(text)
    flush()

    title = blocks[0].text if blocks and blocks[0].type == "heading" else (doc.metadata.get("title") or path.stem)
    head_text = "\n".join(b.text for b in blocks[:4])
    meta = sniff_meta(head_text, path.name)
    meta["pages"] = n_pages
    pd = ParsedDoc(_doc_id(path), title, path.name, "pdf", _sha(path), meta, blocks)
    doc.close()
    return pd


def _emit_text_lines(lines: list[str], page: int | None, blocks: list[Block]) -> None:
    """Join wrapped lines; keep numbered/bulleted items as one list block."""
    items: list[str] = []
    para: list[str] = []
    for t in lines:
        if _LIST_ITEM.match(t):
            if para and not items:
                blocks.append(Block("para", _join(para), page=page))
                para = []
            items.append(t)
        elif items:
            items[-1] = _join([items[-1], t])
        else:
            para.append(t)
    if items:
        blocks.append(Block("list", "\n".join(items), page=page))
    if para:
        blocks.append(Block("para", _join(para), page=page))


def _join(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if out.endswith("-") and p[:1].islower():
            out = out[:-1] + p          # de-hyphenate words split across lines
        else:
            out = f"{out} {p}" if out else p
    return normalize(out)


# ---------------------------------------------------------------- HTML
def parse_html(path: Path) -> ParsedDoc:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    meta_tags = {m.get("name", "").lower(): m.get("content", "") for m in soup.find_all("meta") if m.get("name")}
    title = normalize(soup.title.get_text()) if soup.title else path.stem
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "form"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup

    blocks: list[Block] = []
    anchor = None
    for el in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "table", "pre"]):
        if el.find_parent(["ul", "ol", "table", "pre"]) is not None:
            continue
        name = el.name
        if name[0] == "h" and len(name) == 2:
            anchor = el.get("id") or anchor
            blocks.append(Block("heading", normalize(el.get_text(" ")), level=int(name[1]), anchor=el.get("id")))
        elif name in ("ul", "ol"):
            items = [normalize(li.get_text(" ")) for li in el.find_all("li", recursive=False)]
            if name == "ol":
                items = [f"{i}. {t}" for i, t in enumerate(items, 1)]
            else:
                items = [f"- {t}" for t in items]
            blocks.append(Block("list", "\n".join(items), anchor=anchor))
        elif name == "table":
            rows = [[normalize(c.get_text(" ")) for c in tr.find_all(["th", "td"])] for tr in el.find_all("tr")]
            blocks.append(Block("table", _linearize_table(rows), anchor=anchor))
        else:
            t = normalize(el.get_text(" "))
            if t:
                blocks.append(Block("para", t, anchor=anchor))

    head_text = "\n".join([f"version: {meta_tags.get('version', '')}", f"effective: {meta_tags.get('effective_date', '')}"]
                          + [b.text for b in blocks[:4]])
    meta = sniff_meta(head_text, path.name)
    if meta_tags.get("version"):
        meta["version"] = meta_tags["version"]
    if meta_tags.get("effective_date"):
        meta["effective_date"] = parse_date(meta_tags["effective_date"]) or meta.get("effective_date")
    return ParsedDoc(_doc_id(path), title, path.name, "html", _sha(path), meta, blocks)


def _linearize_table(rows: list[list[str]]) -> str:
    """Turn a table into one self-describing line per row, e.g.
    'Storage per workspace: Basic: 10 GB; Pro: 100 GB; Enterprise: 1 TB'.
    The header row gives the keys so each row is meaningful on its own."""
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    header, body = rows[0], rows[1:]
    if not body:
        return " | ".join(header)
    out = []
    for r in body:
        cells = r + [""] * (len(header) - len(r))
        first = cells[0]
        rest = "; ".join(f"{h}: {c}" for h, c in zip(header[1:], cells[1:]) if c)
        out.append(f"{header[0]} {first}; {rest}" if header[0].lower() in ("plan", "id") else f"{first}: {rest}")
    return "\n".join(out)


# ---------------------------------------------------------------- Markdown
def parse_markdown(path: Path) -> ParsedDoc:
    text = path.read_text(encoding="utf-8", errors="ignore")
    meta: dict = {}
    fm = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if fm:
        try:
            meta = {k: str(v) for k, v in (yaml.safe_load(fm.group(1)) or {}).items()}
        except yaml.YAMLError:
            meta = {}
        text = text[fm.end():]

    blocks: list[Block] = []
    lines = text.split("\n")
    i = 0
    para: list[str] = []

    def flush_para():
        nonlocal para
        if para:
            blocks.append(Block("para", normalize(" ".join(para))))
            para = []

    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("```"):
            flush_para()
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            blocks.append(Block("para", "\n".join(lines[i + 1:j])))  # code block kept intact
            i = j + 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            flush_para()
            blocks.append(Block("heading", normalize(m.group(2)), level=len(m.group(1))))
        elif line.strip().startswith("|"):
            flush_para()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append([normalize(_md_inline(c)) for c in cells])
                i += 1
            blocks.append(Block("table", _linearize_table(rows)))
            continue
        elif _LIST_ITEM.match(line):
            flush_para()
            items = []
            while i < len(lines) and (_LIST_ITEM.match(lines[i]) or (lines[i].startswith("  ") and items)):
                if _LIST_ITEM.match(lines[i]):
                    items.append(normalize(_md_inline(lines[i].strip())))
                else:
                    items[-1] += " " + normalize(lines[i])
                i += 1
            blocks.append(Block("list", "\n".join(items)))
            continue
        elif not line.strip():
            flush_para()
        else:
            para.append(_md_inline(line))
        i += 1
    flush_para()

    title = meta.get("title") or next((b.text for b in blocks if b.type == "heading"), path.stem)
    sniffed = sniff_meta("\n".join(b.text for b in blocks[:3]), path.name)
    for k, v in sniffed.items():
        meta.setdefault(k, v)
    if "effective_date" in meta:
        meta["effective_date"] = parse_date(meta["effective_date"]) or meta["effective_date"]
    meta.pop("title", None)
    return ParsedDoc(_doc_id(path), title, path.name, "markdown", _sha(path), meta, blocks)


def _md_inline(s: str) -> str:
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: m.group(1) or m.group(2), s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return s


# ---------------------------------------------------------------- TXT
def parse_txt(path: Path) -> ParsedDoc:
    text = path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")
    paras = [p.strip("\n") for p in re.split(r"\n\s*\n", text) if p.strip()]
    blocks: list[Block] = []
    title = None
    extra_meta: dict = {}
    meta_line = re.compile(r"^(version|last updated|updated|effective(?: date)?)\s*:", re.IGNORECASE)
    for p in paras:
        plines = []
        for l in p.split("\n"):
            l = normalize(l)
            if not l or meta_line.match(l):
                continue  # metadata lines are captured by sniff_meta, not indexed as content
            m = re.fullmatch(r"support article ([\w-]+)", l, re.IGNORECASE)
            if m:
                extra_meta["article_id"] = m.group(1)
                continue
            plines.append(l)
        if not plines:
            continue
        first = plines[0]
        is_caps = first.upper() == first and re.search(r"[A-Z]", first) and len(first) < 90
        is_numbered = re.match(r"^\d+(\.\d+)*\s+[A-Z]", first) and len(first) < 80 and not first.endswith(".")
        underlined = len(plines) > 1 and re.fullmatch(r"[=\-]{3,}", plines[1])
        if is_caps or is_numbered or underlined:
            level = 1 if (title is None) else 2
            if title is None:
                title = first.title() if is_caps else first
            blocks.append(Block("heading", first, level=level))
            rest = plines[2:] if underlined else plines[1:]
            if rest:
                _emit_text_lines(rest, None, blocks)
        else:
            _emit_text_lines(plines, None, blocks)
    head_text = "\n".join(text.split("\n")[:6])
    meta = sniff_meta(head_text, path.name)
    meta.update(extra_meta)
    return ParsedDoc(_doc_id(path), title or path.stem, path.name, "txt", _sha(path), meta, blocks)


# ---------------------------------------------------------------- dispatch
def parse_file(path: Path) -> ParsedDoc:
    fmt = SUPPORTED.get(path.suffix.lower())
    if fmt is None:
        raise ValueError(f"Unsupported file type: {path.suffix}")
    pd = {"pdf": parse_pdf, "html": parse_html, "markdown": parse_markdown, "txt": parse_txt}[fmt](path)
    pd.meta.setdefault("doc_type", _doc_type(path.stem, pd.title))
    pd.meta = {k: v for k, v in pd.meta.items() if v not in (None, "", "None")}
    # Metadata-only lines ("Version 3.1 · Effective ...", "Last updated: ...") are captured in
    # pd.meta above; they are boilerplate, not answer content.
    pd.blocks = [b for b in pd.blocks if not (b.type == "para" and len(b.text) < 160 and re.match(
        r"^(version\s+v?\d|last updated|updated:|effective\b|released:)", b.text, re.IGNORECASE))]
    # A leading level-1 heading is the document's own heading: use it as the root of the section path
    if pd.blocks and pd.blocks[0].type == "heading":
        pd.blocks[0].level = 0
        pd.meta.setdefault("heading", pd.blocks[0].text)
    return pd
