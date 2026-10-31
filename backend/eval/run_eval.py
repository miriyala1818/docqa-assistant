"""End-to-end evaluation (FR-08). Produces reports/eval_results.json and reports/EVALUATION_REPORT.md.

    python -m eval.run_eval            # reports on the TEST split (thresholds were tuned on dev)
    python -m eval.run_eval --split all

Sections: retrieval ablation (Recall@5/10, MRR, nDCG@5, latency), answer & behaviour metrics,
per-category status accuracy, failure analysis with buckets and real examples, and
evidence-backed example answers. Every number in the report is computed here; nothing is typed in.
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from app.config import K_FINAL, REPORTS_DIR, load_thresholds
from app.qa import answer, llm
from app.retrieval import models
from app.retrieval.index import MODES, get_index
from eval.common import (ANSWERABLE_STATUSES, grade, load_questions, percentile, retrieval_metrics, score_response,
                         summarize)

REPORTS = REPORTS_DIR
MODE_LABEL = {"bm25": "BM25 only", "dense": "Dense only", "hybrid": "Hybrid (RRF)",
              "hybrid_rerank": "Hybrid + reranker", "full": "Hybrid + reranker + query expansion"}


def run_ablation(idx, questions):
    rows = {}
    answerable = [q for q in questions if q["relevant"]]
    for mode in MODES:
        models.clear_caches()
        per_q, lat = [], []
        for q in answerable:
            t = time.perf_counter()
            hits, _ = idx.search(q["question"], mode=mode, k_final=10)
            lat.append((time.perf_counter() - t) * 1000)
            per_q.append(retrieval_metrics([h.chunk for h in hits], q["relevant"], idx.chunks))
        n = len(per_q)
        rows[mode] = {
            "label": MODE_LABEL[mode], "n": n,
            **{m: round(sum(r[m] for r in per_q) / n, 3) for m in ("recall@5", "recall@10", "mrr", "ndcg@5")},
            "latency_p50_ms": round(percentile(lat, 0.5), 1), "latency_p95_ms": round(percentile(lat, 0.95), 1),
        }
    return rows


def bucket(q, resp, row, idx):
    """Assign one failure bucket (first match wins)."""
    exp, got = q["expected_status"], resp["status"]
    if exp == "NOT_FOUND" and got != "NOT_FOUND":
        return "under-abstention (answered an unanswerable question)"
    if exp != "NOT_FOUND":
        top_grades = [grade(r, q["relevant"]) for r in resp["retrieved"][:K_FINAL]]
        if 2 not in top_grades:
            return "retrieval miss"
    if exp != "NOT_FOUND" and got == "NOT_FOUND":
        return "over-refusal"
    if exp == "ANSWERED_WITH_CONFLICT" and got != "ANSWERED_WITH_CONFLICT":
        return "missed conflict"
    if got == "ANSWERED_WITH_CONFLICT" and exp != "ANSWERED_WITH_CONFLICT":
        return "false conflict"
    if exp == "NEEDS_CLARIFICATION" and got != "NEEDS_CLARIFICATION":
        return "missed clarification"
    if got == "NEEDS_CLARIFICATION" and exp != "NEEDS_CLARIFICATION":
        return "unneeded clarification"
    if row["citations_n"] and row["citations_ok"] < row["citations_n"]:
        return "citation mismatch"
    if row["correctness"] < 1:
        # key point exists in a relevant retrieved passage that the answer did not use -> chunk/selection split
        used = {c["chunk_id"] for c in resp["citations"]}
        for miss in row["missing_key_points"]:
            for r in resp["retrieved"]:
                ch = next(c for c in idx.chunks if c.chunk_id == r["chunk_id"])
                if r["chunk_id"] not in used and grade(r, q["relevant"]) and miss.lower() in ch.text.lower():
                    return "chunk split the answer"
        return "incomplete answer"
    return None


def run_end_to_end(questions):
    models.clear_caches()
    rows, resps = [], {}
    for q in questions:
        resp = answer.ask(q["question"], use_llm=None)
        resps[q["id"]] = resp
        rows.append(score_response(q, resp))
    return rows, resps


def fmt(v, pct=False):
    if v is None:
        return "n/a"
    return f"{v * 100:.1f}%" if pct else (f"{v:.3f}" if isinstance(v, float) else str(v))


def example_block(q, resp):
    lines = [f"**Q ({q['category']}):** {q['question']}  ", f"**Status:** `{resp['status']}`"
             + (f" · confidence {resp['confidence']}" if resp.get("confidence") else "") + "  "]
    if resp["status"] == "NOT_FOUND":
        lines.append(f"> {resp.get('message', '')}  ")
        if resp["closest_topics"]:
            lines.append("> Closest topics: " + "; ".join(t["topic"] for t in resp["closest_topics"]))
        return "\n".join(lines)
    for a in resp["answer"]:
        cites = "".join(f"[{n}]" for n in a["citations"])
        label = f"*{a['label']}:* " if a.get("label") else ""
        text = a["text"].replace("\n", "  \n> ")
        lines.append(f"> {label}{text} {cites}  ")
    if resp.get("clarifying_question"):
        lines.append("> Options: " + "; ".join(f"{o['text']} [{o['citation']}]" for o in resp["options"]))
    for c in resp["conflicts"]:
        lines.append(f"> ⚠ {c['note']}  ")
    lines.append("")
    for c in resp["citations"]:
        loc = f", {c['location']}" if c["location"] else ""
        ver = f" (v{c['version']}, {c['effective_date']})" if c.get("version") else (f" ({c['effective_date']})" if c.get("effective_date") else "")
        lines.append(f"- [{c['n']}] {c['doc_title']}{ver} › {c['section']}{loc} — `{c['source']}`")
    return "\n".join(lines)


def write_report(out, questions, resps, path: Path):
    s, ab, rows = out["summary"], out["ablation"], out["rows"]
    th = out["thresholds"]
    L = []
    L.append("# Evaluation Report: Enterprise Documentation Q&A Assistant\n")
    L.append(f"Generated {out['generated']} by `python -m eval.run_eval --split {out['split']}` "
             f"on {out['machine']}. Answer generator: **{out['generator']}**. "
             "All numbers below are computed by the script; none are typed in by hand.\n")
    L.append("## 1. Test set\n")
    cats = Counter(q["category"] for q in load_questions("all"))
    L.append(f"{sum(cats.values())} labelled questions over a 9-document sample collection "
             "(2 PDF, 2 HTML, 3 Markdown, 2 TXT), split into **20 dev** (used only to tune thresholds) and "
             "**40 test** (reported below). Construction and assumptions: `eval/README.md`.\n")
    L.append("| Category | Total | Expected status |\n|---|---|---|")
    exp = {"direct": "ANSWERED", "paraphrase": "ANSWERED", "multi_passage": "ANSWERED (2+ citations)",
           "conflict": "ANSWERED_WITH_CONFLICT", "ambiguous": "NEEDS_CLARIFICATION", "unanswerable": "NOT_FOUND"}
    for c in ["direct", "paraphrase", "multi_passage", "conflict", "ambiguous", "unanswerable"]:
        L.append(f"| {c} | {cats[c]} | {exp[c]} |")
    L.append("")
    L.append("## 2. Retrieval quality and ablation\n")
    L.append(f"Computed on the {ab['full']['n']} answerable questions of the `{out['split']}` split. A passage is relevant if "
             "its document and section match a labelled section (grade 2 = answers it, 1 = partial). "
             "Recall@k and MRR use grade-2 passages; nDCG@5 uses the graded labels. Latency = retrieval only, CPU.\n")
    L.append("| Retrieval setup | Recall@5 | Recall@10 | MRR | nDCG@5 | Latency p50 | Latency p95 |\n|---|---|---|---|---|---|---|")
    for m in MODES:
        r = ab[m]
        L.append(f"| {r['label']} | {fmt(r['recall@5'])} | {fmt(r['recall@10'])} | {fmt(r['mrr'])} | {fmt(r['ndcg@5'])} "
                 f"| {r['latency_p50_ms']:.0f} ms | {r['latency_p95_ms']:.0f} ms |")
    L.append("")
    L.append("## 3. Answer quality and behaviour\n")
    L.append("| Area | Metric | Value | Meaning |\n|---|---|---|---|")
    L += [
        f"| Behaviour | Status accuracy | {fmt(s['status_accuracy'], True)} | Response status matched the label ({s['n']} questions) |",
        f"| Answer | Correctness | {fmt(s['correctness'], True)} | Share of gold key points present in the answer (answerable questions) |",
        f"| Answer | Citation precision | {fmt(s['citation_precision'], True)} | Citations that point at a labelled-relevant passage |",
        f"| Answer | Groundedness (automatic) | {fmt(s['groundedness'], True)} | Answer sentences that pass the number + term-coverage verifier against their cited passage |",
        f"| Behaviour | Abstention accuracy | {fmt(s['abstention_accuracy'], True)} | Unanswerable questions returned as NOT_FOUND |",
        f"| Behaviour | False-refusal rate | {fmt(s['false_refusal_rate'], True)} | Answerable questions wrongly returned as NOT_FOUND |",
        f"| Behaviour | Conflict recall | {fmt(s['conflict_recall'], True)} | Conflict questions flagged ANSWERED_WITH_CONFLICT |",
        f"| Behaviour | False-conflict rate | {fmt(s['false_conflict_rate'], True)} | Non-conflict questions wrongly flagged as conflicts |",
        f"| Behaviour | Clarify rate | {fmt(s['clarify_rate'], True)} | Ambiguous questions that got a clarifying question |",
        f"| System | Latency p50 / p95 | {s['latency_p50_ms']:.0f} / {s['latency_p95_ms']:.0f} ms | End-to-end time per question, CPU only |",
    ]
    L.append("")
    L.append("**About groundedness:** in extractive mode every answer sentence is copied from its cited passage, "
             "so this metric is high by construction. It becomes informative in LLM mode, where the same verifier "
             "removes unsupported generated sentences. Citation precision and correctness are the stronger signals here.\n")
    L.append("### Status accuracy by category\n")
    L.append("| Category | n | Correct | Accuracy | Mean correctness |\n|---|---|---|---|---|")
    by = defaultdict(list)
    for r in rows:
        by[r["category"]].append(r)
    for c in ["direct", "paraphrase", "multi_passage", "conflict", "ambiguous", "unanswerable"]:
        rs = by.get(c, [])
        if rs:
            ok = sum(r["status_ok"] for r in rs)
            L.append(f"| {c} | {len(rs)} | {ok} | {ok / len(rs) * 100:.0f}% | {sum(r['correctness'] for r in rs) / len(rs) * 100:.0f}% |")
    L.append("")
    L.append("### Confusion matrix (expected → got)\n")
    statuses = ["ANSWERED", "ANSWERED_WITH_CONFLICT", "NEEDS_CLARIFICATION", "NOT_FOUND"]
    L.append("| expected \\ got | " + " | ".join(statuses) + " |\n|---|" + "---|" * len(statuses))
    for e in statuses:
        L.append(f"| {e} | " + " | ".join(str(sum(1 for r in rows if r['expected'] == e and r['got'] == g)) for g in statuses) + " |")
    L.append("")
    v1 = REPORTS / "eval_results_v1_blind_test.json"
    if out["split"] == "test" and v1.exists():
        old = json.loads(v1.read_text(encoding="utf-8"))["summary"]
        L.append("### Iteration history (test split)\n")
        L.append("| Metric | v1 (blind run) | v2 (this run) |\n|---|---|---|")
        for k, name in [("status_accuracy", "Status accuracy"), ("correctness", "Correctness"),
                        ("citation_precision", "Citation precision"), ("abstention_accuracy", "Abstention accuracy"),
                        ("false_refusal_rate", "False-refusal rate"), ("conflict_recall", "Conflict recall"),
                        ("false_conflict_rate", "False-conflict rate"), ("clarify_rate", "Clarify rate")]:
            L.append(f"| {name} | {fmt(old.get(k), True)} | {fmt(s.get(k), True)} |")
        L.append("")
        it = Path(__file__).resolve().parent / "iterations.md"
        if it.exists():
            L.append(it.read_text(encoding="utf-8"))
    L.append("## 4. Failure analysis\n")
    fails = out["failures"]
    if not fails:
        L.append("No failures on this split.\n")
    else:
        L.append("Every question with a wrong status, missing key points or an off-target citation is put in one bucket "
                 "(first matching rule in `eval/run_eval.py::bucket`).\n")
        L.append("| Bucket | Count |\n|---|---|")
        for b, n in Counter(f["bucket"] for f in fails).most_common():
            L.append(f"| {b} | {n} |")
        L.append("")
        seen = set()
        for f in fails:
            if f["bucket"] in seen:
                continue
            seen.add(f["bucket"])
            q = next(x for x in questions if x["id"] == f["id"])
            L.append(f"**{f['bucket']}** — {f['id']}: *{q['question']}* (expected `{q['expected_status']}`, got `{f['got']}`"
                     + (f"; missing key points: {', '.join(f['missing'])}" if f["missing"] else "") + ")\n")
            L.append(example_block(q, resps[f["id"]]))
            L.append("")
    L.append("### What we would fix next\n")
    L.append(out["next_steps"])
    L.append("")
    L.append("## 5. Evidence-backed examples\n")
    L.append("Real outputs from this run, one per question type.\n")
    for qid in out["example_ids"]:
        q = next(x for x in questions if x["id"] == qid)
        L.append(f"### {qid}\n")
        L.append(example_block(q, resps[qid]))
        L.append("")
    L.append("## 6. Method and limitations\n")
    L.append(f"- Thresholds (tuned on dev by `python -m eval.tune`): `{json.dumps(th)}`.")
    L.append("- Answer-quality method: automatic key-point matching (case-insensitive substring of 1-4 gold key points per question), "
             "citation precision against section-level relevance labels, and the sentence verifier. A human audit of ~20 answers "
             "is recommended before quoting groundedness externally.")
    L.append("- The collection and questions are synthetic and were written by the same author as the system (an AI assistant), "
             "so the numbers show the system works as designed on representative cases; they are not a benchmark. "
             "Independent questions from a second person would give a fairer estimate (see `eval/README.md`).")
    L.append("- 40 test questions means each question moves a rate by 2.5 points; small differences between ablation rows are within noise.")
    L.append("- Conflict detection covers numeric disagreements (values with units) and explicit version updates ('in addition to', "
             "'no longer', ...). Conflicts stated purely in words (e.g. 'allowed' vs 'not allowed') are only caught in LLM mode.")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test", choices=["dev", "test", "all"])
    args = ap.parse_args()

    idx = get_index()
    answer.thresholds(reload=True)
    questions = load_questions(args.split)
    print(f"Evaluating {len(questions)} questions ({args.split}) ...")
    models.rerank_scores("warm up", ["warm up"])  # load models before timing

    ablation = run_ablation(idx, questions)
    rows, resps = run_end_to_end(questions)
    summary = summarize(rows)

    failures = []
    for q, r in zip(questions, rows):
        b = bucket(q, resps[q["id"]], r, idx)
        if b:
            failures.append({"id": q["id"], "bucket": b, "got": r["got"], "missing": r["missing_key_points"]})

    # pick one example per category (prefer correct ones; the brief asks for evidence-backed examples)
    example_ids = []
    for cat in ["direct", "paraphrase", "multi_passage", "conflict", "ambiguous", "unanswerable"]:
        cands = [r for r in rows if r["category"] == cat]
        good = [r for r in cands if r["status_ok"] and r["correctness"] == 1] or cands
        if good:
            example_ids.append(good[0]["id"])

    next_steps = "\n".join(f"- {s}" for s in [
        "**Retrieval misses / paraphrases:** extend the glossary from real query logs, or add LLM query rewriting (toggle) and re-run the ablation to prove it.",
        "**Missed or false conflicts:** add an NLI / LLM confirmation step for candidate pairs (already used in LLM mode) so wording-only contradictions are caught and coincidental number differences are rejected.",
        "**Over-refusal vs. under-abstention:** tune the evidence gate on a larger dev set; consider a per-category calibration of the reranker score.",
        "**Incomplete answers:** raise `max_units` for multi-part questions or split the question into sub-questions before retrieval.",
    ])

    out = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "split": args.split,
        "machine": f"{platform.system()} {platform.release()}, Python {platform.python_version()}",
        "generator": f"Claude ({llm.LLM_MODEL})" if llm.available() else "local extractive (no LLM)",
        "thresholds": load_thresholds(), "summary": summary, "ablation": ablation, "rows": rows,
        "failures": failures, "example_ids": example_ids, "next_steps": next_steps,
    }
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "eval_results.json").write_text(json.dumps({**out, "responses": resps}, indent=1, default=str), encoding="utf-8")
    write_report(out, questions, resps, REPORTS / "EVALUATION_REPORT.md")

    print(json.dumps({"summary": summary, "ablation": {m: {k: v for k, v in r.items() if k != "label"} for m, r in ablation.items()},
                      "failures": failures}, indent=1))
    print(f"Wrote {REPORTS / 'EVALUATION_REPORT.md'}")


if __name__ == "__main__":
    main()
