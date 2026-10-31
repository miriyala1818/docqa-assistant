import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastembed.rerank.cross_encoder import TextCrossEncoder

from app.config import MODEL_CACHE, RERANK_MODEL
from app.retrieval.index import get_index

idx = get_index()
texts = [c.embed_text for c in idx.chunks[:30]]
print("cpus", os.cpu_count(), "avg words", sum(len(t.split()) for t in texts) / len(texts))
for kw in ({}, {"threads": os.cpu_count()}, {"threads": 4}):
    m = TextCrossEncoder(RERANK_MODEL, cache_dir=str(MODEL_CACHE), **kw)
    list(m.rerank("warm", texts[:2]))
    for bs in (8, 32, 64):
        t = time.perf_counter()
        list(m.rerank("how do I reset my password", texts, batch_size=bs))
        print(kw, "bs", bs, f"{(time.perf_counter() - t) * 1000:.0f} ms")
