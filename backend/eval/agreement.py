"""Inter-annotator agreement for the expected-status labels.

A second team member labels a sample of questions independently (without looking at
eval/questions.json) in a CSV with columns:  id,expected_status
Then:   python -m eval.agreement eval/second_labeller.csv

Prints raw agreement and Cohen's kappa, and lists disagreements (which become documented assumptions).
"""
from __future__ import annotations

import csv
import sys
from collections import Counter

from eval.common import load_questions


def cohen_kappa(a: list[str], b: list[str]) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(a) | set(b)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def main(path: str) -> None:
    gold = {q["id"]: q for q in load_questions("all")}
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    pairs = [(gold[r["id"]]["expected_status"], r["expected_status"].strip().upper(), r["id"]) for r in rows if r["id"] in gold]
    if not pairs:
        sys.exit("No matching question ids in the CSV.")
    a, b = [p[0] for p in pairs], [p[1] for p in pairs]
    print(f"Questions compared: {len(pairs)}")
    print(f"Raw agreement:      {sum(x == y for x, y in zip(a, b)) / len(pairs):.1%}")
    print(f"Cohen's kappa:      {cohen_kappa(a, b):.3f}")
    for g, s, qid in pairs:
        if g != s:
            print(f"  {qid}: labeller 1 = {g}, labeller 2 = {s} | {gold[qid]['question']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "eval/second_labeller.csv")
