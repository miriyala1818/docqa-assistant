"""Paths, model names and decision thresholds.

Thresholds have defaults here but are overridden by data/thresholds.json, which is written by
`python -m eval.tune` using ONLY the dev split of the labelled questions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent      # docqa-assistant/backend
PROJECT_ROOT = BACKEND_DIR.parent                          # docqa-assistant
ROOT = BACKEND_DIR                                         # kept for backwards compatibility
FRONTEND_DIR = PROJECT_ROOT / "frontend"
REPORTS_DIR = PROJECT_ROOT / "reports"
KB_DIR = Path(os.environ.get("DOCQA_KB_DIR", PROJECT_ROOT / "knowledge_base"))
DATA_DIR = BACKEND_DIR / "data"
INDEX_DIR = DATA_DIR / "index"
MODEL_CACHE = BACKEND_DIR / "models"
THRESHOLDS_FILE = DATA_DIR / "thresholds.json"

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"

# Claude settings (used only when credentials are available; see app/llm.py)
LLM_MODEL = os.environ.get("DOCQA_LLM_MODEL", "claude-opus-5-5")
LLM_EFFORT = os.environ.get("DOCQA_LLM_EFFORT", "low")
LLM_MODE = os.environ.get("DOCQA_LLM", "auto")  # auto | on | off

# Retrieval sizes
K_SPARSE = 30
K_DENSE = 30
K_RERANK = 30
K_FINAL = 8
RRF_K = 60
EVIDENCE_PASSAGES = 6

DEFAULT_THRESHOLDS = {
    "nf_top": 0.0,            # evidence gate: top passage reranker score must reach this
    "sent_min": -1.0,         # a sentence must score at least this to be used in an answer
    "sent_margin": 3.0,       # extra sentences must be within this of the best sentence
    "claim_min": -1.0,        # min sentence score to take part in conflict / clarification checks
    "claim_margin": 4.0,      # ... and within this of the best sentence
    "clarify_margin": 1.5,    # plan-specific values must be this close to the best sentence to trigger a clarifying question
    "max_units": 3,           # max sentences / list blocks in an extractive answer
}


def load_thresholds() -> dict:
    th = dict(DEFAULT_THRESHOLDS)
    if THRESHOLDS_FILE.exists():
        try:
            th.update(json.loads(THRESHOLDS_FILE.read_text(encoding="utf-8")).get("thresholds", {}))
        except (OSError, ValueError):
            pass
    return th
