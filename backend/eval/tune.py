"""Tune decision thresholds on the DEV split only (the test split is never looked at here).

    python -m eval.tune

Grid-searches the evidence-gate / sentence / conflict / clarification thresholds, maximising
dev status accuracy (+ half of key-point correctness as a tie-breaker), and writes the winner to
data/thresholds.json. Reranker scores are memoised, so each grid point only re-runs the
decision logic.
"""
from __future__ import annotations

import itertools
import json
import time

from app.config import DEFAULT_THRESHOLDS, THRESHOLDS_FILE
from app.qa import answer
from app.retrieval.index import get_index
from eval.common import load_questions, score_response, summarize

GRID = {
    "nf_top": [-4.0, -3.0, -2.0, -1.0, 0.0, 1.0],
    "sent_min": [-3.0, -2.0, -1.0, 0.0],
    "claim_min": [-3.0, -2.0, -1.0],
    "claim_margin": [4.0, 6.0, 8.0],
    "clarify_margin": [1.0, 2.0, 4.0],
    "max_units": [3, 4],
}


def evaluate(th: dict, questions: list[dict]) -> tuple[float, dict]:
    answer._TH_CACHE.clear()
    answer._TH_CACHE.update(th)
    rows = [score_response(q, answer.ask(q["question"], use_llm=False)) for q in questions]
    s = summarize(rows)
    objective = s["status_accuracy"] + 0.5 * (s["correctness"] or 0)
    return objective, s


def main():
    get_index()
    dev = load_questions("dev")
    print(f"Tuning on {len(dev)} dev questions ...")
    t0 = time.time()
    base = dict(DEFAULT_THRESHOLDS)
    best = None
    keys = list(GRID)
    for values in itertools.product(*(GRID[k] for k in keys)):
        th = dict(base, **dict(zip(keys, values)))
        if th["claim_min"] < th["sent_min"] - 1:
            continue
        obj, s = evaluate(th, dev)
        # tie-break: stay close to the defaults (smaller total change)
        dist = sum(abs(th[k] - DEFAULT_THRESHOLDS[k]) for k in keys)
        cand = (round(obj, 4), -dist)
        if best is None or cand > best[0]:
            best = (cand, th, s)
    (_, th, s) = best
    THRESHOLDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    THRESHOLDS_FILE.write_text(json.dumps({
        "tuned_on": "dev split (eval/questions.json)", "dev_summary": s, "thresholds": th,
        "grid": GRID, "seconds": round(time.time() - t0, 1)}, indent=2), encoding="utf-8")
    answer.thresholds(reload=True)
    print(json.dumps({"thresholds": th, "dev_summary": s}, indent=2))
    print(f"Saved to {THRESHOLDS_FILE} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
