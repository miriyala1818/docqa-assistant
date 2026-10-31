"""Lazily loaded local ONNX models (no GPU, no API key needed)."""
from __future__ import annotations

import os
import threading

import numpy as np

from ..config import EMBED_MODEL, MODEL_CACHE, RERANK_MODEL

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

_lock = threading.Lock()
_embedder = None
_reranker = None


def embedder():
    global _embedder
    with _lock:
        if _embedder is None:
            from fastembed import TextEmbedding
            _embedder = TextEmbedding(EMBED_MODEL, cache_dir=str(MODEL_CACHE))
    return _embedder


def reranker():
    global _reranker
    with _lock:
        if _reranker is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            # 4 threads / batch 8 measured fastest on an 8-core laptop CPU (scripts/bench_reranker.py)
            _reranker = TextCrossEncoder(RERANK_MODEL, cache_dir=str(MODEL_CACHE), threads=min(4, os.cpu_count() or 4))
    return _reranker


def embed_passages(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    vecs = np.array(list(embedder().passage_embed(texts)), dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


_qcache: dict[str, np.ndarray] = {}
_rcache: dict[tuple[str, str], float] = {}
_CACHE_MAX = 50_000


def embed_query(text: str) -> np.ndarray:
    if text in _qcache:
        return _qcache[text]
    v = np.array(list(embedder().query_embed(text))[0], dtype=np.float32)
    v = v / np.linalg.norm(v)
    if len(_qcache) < _CACHE_MAX:
        _qcache[text] = v
    return v


def rerank_scores(query: str, texts: list[str]) -> list[float]:
    """Cross-encoder relevance logits. Pairs are memoised (the same question/passage pair always
    scores the same), which keeps repeated evaluation and threshold tuning cheap."""
    if not texts:
        return []
    missing = list(dict.fromkeys(t for t in texts if (query, t) not in _rcache))
    if missing:
        scores = [float(s) for s in reranker().rerank(query, missing, batch_size=8)]
        if len(_rcache) + len(missing) > _CACHE_MAX:
            _rcache.clear()
        _rcache.update({(query, t): s for t, s in zip(missing, scores)})
    return [_rcache[(query, t)] for t in texts]


def clear_caches() -> None:
    _qcache.clear()
    _rcache.clear()
