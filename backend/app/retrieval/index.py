"""FR-03 Natural-language retrieval: dual index (BM25 + dense), RRF fusion,
cross-encoder reranking, glossary query expansion and metadata filtering."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..config import INDEX_DIR, K_DENSE, K_FINAL, K_RERANK, K_SPARSE, KB_DIR, RRF_K
from ..ingestion.chunker import Chunk, chunk_document, dedupe
from ..ingestion.parsers import SUPPORTED, parse_file
from ..utils.textutil import expand_query, tokenize
from . import models

MODES = ("bm25", "dense", "hybrid", "hybrid_rerank", "full")
INDEX_VERSION = 3  # bump when parsing/chunking changes so stored indexes are rebuilt


@dataclass
class Hit:
    idx: int
    chunk: Chunk
    scores: dict = field(default_factory=dict)
    rank_score: float = 0.0     # score used for final ordering (reranker logit if reranked)


EXPANSION_PENALTY = 0.5


def ce_scores(query: str, expansion: list[str], texts: list[str]) -> list[float]:
    """Cross-encoder scores. When the glossary expanded the query (paraphrase / terminology
    variation, e.g. 'login credentials' -> 'password'), the expanded query is scored too and the
    better score is kept, minus a small penalty so original wording wins ties."""
    base = models.rerank_scores(query, texts)
    if not expansion:
        return base
    expanded = models.rerank_scores(f"{query} ({', '.join(expansion)})", texts)
    return [max(b, e - EXPANSION_PENALTY) for b, e in zip(base, expanded)]


class BM25:
    def __init__(self, docs_tokens: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(t) for t in docs_tokens]
        self.len = np.array([len(t) for t in docs_tokens], dtype=np.float32)
        self.avgdl = float(self.len.mean()) if len(self.len) else 1.0
        df = Counter(term for t in docs_tokens for term in set(t))
        n = len(docs_tokens)
        self.idf = {term: math.log(1 + (n - d + 0.5) / (d + 0.5)) for term, d in df.items()}

    def scores(self, q_tokens: list[str]) -> np.ndarray:
        s = np.zeros(len(self.tf), dtype=np.float32)
        for term in set(q_tokens):
            idf = self.idf.get(term)
            if idf is None:
                continue
            qtf = q_tokens.count(term)
            for i, tf in enumerate(self.tf):
                f = tf.get(term)
                if f:
                    denom = f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avgdl)
                    s[i] += idf * (f * (self.k1 + 1) / denom) * (1 + 0.3 * (qtf - 1))
        return s


class Index:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.chunks: list[Chunk] = []
        self.emb = np.zeros((0, 384), dtype=np.float32)
        self.bm25: BM25 | None = None
        self.vocab: set[str] = set()
        self.lock = threading.RLock()

    # ------------------------------------------------------------ building
    def build(self, kb_dir: Path = KB_DIR) -> dict:
        t0 = time.time()
        with self.lock:
            docs, chunks, errors = {}, [], []
            for path in sorted(kb_dir.iterdir()):
                if path.suffix.lower() not in SUPPORTED:
                    continue
                try:
                    pd = parse_file(path)
                except Exception as e:  # keep going: one bad file must not break the index
                    errors.append({"file": path.name, "error": str(e)})
                    continue
                cs = chunk_document(pd)
                docs[pd.doc_id] = {"doc_id": pd.doc_id, "title": pd.title, "source": pd.source, "format": pd.fmt,
                                   "sha256": pd.sha256, "meta": pd.meta, "n_chunks": len(cs)}
                chunks.extend(cs)
            before = len(chunks)
            chunks = dedupe(chunks)
            self.docs, self.chunks = docs, chunks
            self.emb = models.embed_passages([c.embed_text for c in chunks])
            self._finalize()
            self.save()
        return {"documents": len(docs), "chunks": len(chunks), "duplicates_removed": before - len(chunks),
                "errors": errors, "seconds": round(time.time() - t0, 2)}

    def _finalize(self):
        toks = [tokenize(c.embed_text) for c in self.chunks]
        self.bm25 = BM25(toks)
        self.vocab = {t for c in self.chunks for t in tokenize(c.embed_text, keep_stop=True)}

    def save(self, d: Path = INDEX_DIR):
        d.mkdir(parents=True, exist_ok=True)
        (d / "chunks.json").write_text(json.dumps({"index_version": INDEX_VERSION, "docs": self.docs,
                                                   "chunks": [c.to_dict() for c in self.chunks]},
                                                  indent=1), encoding="utf-8")
        np.save(d / "embeddings.npy", self.emb)

    def load(self, d: Path = INDEX_DIR) -> bool:
        f = d / "chunks.json"
        if not f.exists() or not (d / "embeddings.npy").exists():
            return False
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("index_version") != INDEX_VERSION:
            return False  # parser/chunker changed since the index was built
        fields = Chunk.__dataclass_fields__.keys()
        with self.lock:
            self.docs = data["docs"]
            self.chunks = [Chunk(**{k: v for k, v in c.items() if k in fields}) for c in data["chunks"]]
            self.emb = np.load(d / "embeddings.npy")
            self._finalize()
        return True

    def is_stale(self, kb_dir: Path = KB_DIR) -> bool:
        """True if files were added, removed or edited since the index was built."""
        current = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in kb_dir.iterdir() if p.suffix.lower() in SUPPORTED}
        return current != {d["source"]: d["sha256"] for d in self.docs.values()}

    def add_file(self, src: Path, kb_dir: Path = KB_DIR) -> dict:
        kb_dir.mkdir(parents=True, exist_ok=True)
        dst = kb_dir / src.name
        if src.resolve() != dst.resolve():
            shutil.copyfile(src, dst)
        return self.build(kb_dir)

    def remove_doc(self, doc_id: str, kb_dir: Path = KB_DIR) -> bool:
        doc = self.docs.get(doc_id)
        if not doc:
            return False
        (kb_dir / doc["source"]).unlink(missing_ok=True)
        self.build(kb_dir)
        return True

    # ------------------------------------------------------------ search
    def _filter_mask(self, version: str | None, doc_ids: list[str] | None) -> np.ndarray:
        mask = np.ones(len(self.chunks), dtype=bool)
        if doc_ids:
            mask &= np.array([c.doc_id in doc_ids for c in self.chunks])
        if version:
            vm = np.array([str(c.meta.get("version", "")) == version for c in self.chunks])
            if vm.any():  # only filter when some document actually carries that version
                mask &= vm | ~np.array([bool(c.meta.get("version")) for c in self.chunks])
        return mask

    def search(self, query: str, mode: str = "full", version: str | None = None,
               doc_ids: list[str] | None = None, k_final: int = K_FINAL) -> tuple[list[Hit], dict]:
        """Return the top passages and diagnostic info for the given retrieval mode."""
        assert mode in MODES, mode
        info: dict = {"mode": mode}
        mask = self._filter_mask(version, doc_ids)
        use_expansion = mode == "full"
        q_sparse = query
        if use_expansion:
            q_sparse, added = expand_query(query)
            info["expansion"] = added

        cand: dict[int, dict] = {}
        if mode in ("bm25", "hybrid", "hybrid_rerank", "full"):
            s = self.bm25.scores(tokenize(q_sparse))
            s[~mask] = -1
            order = [i for i in np.argsort(-s)[:K_SPARSE] if s[i] > 0]
            for r, i in enumerate(order):
                cand.setdefault(int(i), {})["bm25"] = float(s[i])
                cand[int(i)]["bm25_rank"] = r + 1
        if mode in ("dense", "hybrid", "hybrid_rerank", "full"):
            qv = models.embed_query(query)
            s = self.emb @ qv
            s[~mask] = -1
            order = np.argsort(-s)[:K_DENSE]
            for r, i in enumerate(order):
                cand.setdefault(int(i), {})["dense"] = float(s[i])
                cand[int(i)]["dense_rank"] = r + 1

        for i, sc in cand.items():
            sc["rrf"] = sum(1.0 / (RRF_K + sc[k]) for k in ("bm25_rank", "dense_rank") if k in sc)
        if mode == "bm25":
            key = "bm25"
        elif mode == "dense":
            key = "dense"
        else:
            key = "rrf"
        ranked = sorted(cand.items(), key=lambda kv: -kv[1].get(key, -1e9))
        hits = [Hit(i, self.chunks[i], sc, sc.get(key, 0.0)) for i, sc in ranked]

        if mode in ("hybrid_rerank", "full") and hits:
            top = hits[:K_RERANK]
            texts = [h.chunk.embed_text for h in top]
            ce = ce_scores(query, info.get("expansion", []), texts)
            for h, s in zip(top, ce):
                h.scores["rerank"] = s
                h.rank_score = s
            hits = sorted(top, key=lambda h: -h.rank_score)
        return hits[:k_final], info

    def stats(self) -> dict:
        return {"documents": len(self.docs), "chunks": len(self.chunks),
                "formats": dict(Counter(d["format"] for d in self.docs.values()))}


INDEX = Index()


def get_index() -> Index:
    with INDEX.lock:
        if not INDEX.chunks:
            if not INDEX.load() or INDEX.is_stale():
                INDEX.build()
    return INDEX
