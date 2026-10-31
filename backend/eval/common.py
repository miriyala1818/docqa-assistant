"""Shared helpers for evaluation: loading labels, graded relevance, metrics, scoring a response."""
from __future__ import annotations

import json
import math
from pathlib import Path

from app.ingestion.chunker import Chunk

QUESTIONS_FILE = Path(__file__).resolve().parent / "questions.json"
ANSWERABLE_STATUSES = {"ANSWERED", "ANSWERED_WITH_CONFLICT", "NEEDS_CLARIFICATION"}


def load_questions(split: str | None = None) -> list[dict]:
    qs = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))["questions"]
    return [q for q in qs if split in (None, "all") or q["split"] == split]


def grade(chunk: Chunk | dict, labels: list[dict]) -> int:
    doc = chunk["doc_id"] if isinstance(chunk, dict) else chunk.doc_id
    sec = (chunk["section"] if isinstance(chunk, dict) else chunk.section).lower()
    return max((l["grade"] for l in labels if l["doc"] == doc and l["section"].lower() in sec), default=0)


def retrieval_metrics(retrieved: list[Chunk], labels: list[dict], all_chunks: list[Chunk]) -> dict:
    grades = [grade(c, labels) for c in retrieved]
    first = next((i for i, g in enumerate(grades) if g == 2), None)
    ideal = sorted((grade(c, labels) for c in all_chunks), reverse=True)[:5]

    def dcg(gs):
        return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(gs))

    idcg = dcg(ideal)
    return {
        "recall@5": float(first is not None and first < 5),
        "recall@10": float(first is not None and first < 10),
        "mrr": 1.0 / (first + 1) if first is not None else 0.0,
        "ndcg@5": dcg(grades[:5]) / idcg if idcg else 0.0,
        "first_relevant_rank": first + 1 if first is not None else None,
    }


def response_texts(resp: dict) -> str:
    parts = [a["text"] for a in resp.get("answer", [])]
    parts += [o["text"] for o in resp.get("options", [])]
    parts += [c["text"] for conf in resp.get("conflicts", []) for c in conf["claims"]]
    if resp.get("clarifying_question"):
        parts.append(resp["clarifying_question"])
    return "\n".join(parts)


def keypoint_coverage(resp: dict, key_points: list[str]) -> tuple[float, list[str]]:
    if not key_points:
        return 1.0, []
    if resp["status"] == "NOT_FOUND":
        return 0.0, list(key_points)
    text = response_texts(resp).lower()
    missing = [k for k in key_points if k.lower() not in text]
    return 1 - len(missing) / len(key_points), missing


def citation_precision(resp: dict, labels: list[dict]) -> tuple[int, int]:
    """(# citations that point at a labelled-relevant passage, # citations attached to claims)."""
    by_n = {c["n"]: c for c in resp.get("citations", [])}
    used = set()
    for a in resp.get("answer", []):
        used.update(a.get("citations", []))
    used.update(o["citation"] for o in resp.get("options", []))
    for conf in resp.get("conflicts", []):
        used.update(c["citation"] for c in conf["claims"])
    ok = sum(1 for n in used if n in by_n and grade(by_n[n], labels) >= 1)
    return ok, len(used)


def score_response(q: dict, resp: dict) -> dict:
    cov, missing = keypoint_coverage(resp, q["key_points"])
    cp_ok, cp_n = citation_precision(resp, q["relevant"])
    claims = [a for a in resp.get("answer", []) if a["kind"] != "note"]
    return {
        "id": q["id"], "category": q["category"], "question": q["question"],
        "expected": q["expected_status"], "got": resp["status"], "status_ok": resp["status"] == q["expected_status"],
        "correctness": cov, "missing_key_points": missing,
        "citations_ok": cp_ok, "citations_n": cp_n,
        "claims_verified": sum(a["verified"] for a in claims), "claims_n": len(claims),
        "latency_ms": resp["timings_ms"]["total"],
    }


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["expected"] in ANSWERABLE_STATUSES]
    unans = [r for r in rows if r["expected"] == "NOT_FOUND"]
    conf = [r for r in rows if r["category"] == "conflict"]
    amb = [r for r in rows if r["category"] == "ambiguous"]
    nonconf = [r for r in rows if r["category"] != "conflict"]
    cit_n = sum(r["citations_n"] for r in rows)
    cl_n = sum(r["claims_n"] for r in rows)

    def rate(xs, f):
        return round(sum(1 for r in xs if f(r)) / len(xs), 3) if xs else None

    return {
        "n": len(rows),
        "status_accuracy": rate(rows, lambda r: r["status_ok"]),
        "abstention_accuracy": rate(unans, lambda r: r["got"] == "NOT_FOUND"),
        "false_refusal_rate": rate(ans, lambda r: r["got"] == "NOT_FOUND"),
        "conflict_recall": rate(conf, lambda r: r["got"] == "ANSWERED_WITH_CONFLICT"),
        "false_conflict_rate": rate(nonconf, lambda r: r["got"] == "ANSWERED_WITH_CONFLICT"),
        "clarify_rate": rate(amb, lambda r: r["got"] == "NEEDS_CLARIFICATION"),
        "correctness": round(sum(r["correctness"] for r in ans) / len(ans), 3) if ans else None,
        "citation_precision": round(sum(r["citations_ok"] for r in rows) / cit_n, 3) if cit_n else None,
        "groundedness": round(sum(r["claims_verified"] for r in rows) / cl_n, 3) if cl_n else None,
        "latency_p50_ms": round(percentile([r["latency_ms"] for r in rows], 0.5), 1),
        "latency_p95_ms": round(percentile([r["latency_ms"] for r in rows], 0.95), 1),
    }
