# How the test set was built

`eval/questions.json` has **60 labelled questions** over the 9-document Orbitly sample collection in
`knowledge_base/`. This file documents how the questions and labels were constructed and what they assume,
as the brief requires.

## Who wrote it, and what that means

The sample collection and the questions were **drafted by an AI assistant (Claude)** in the same session that
built the system. That is a real limitation: the author of the questions knows how the system works. To reduce
the bias:

- the collection was written **before** the questions, with deliberate traps (conflicts, plan-dependent values);
- paraphrase questions deliberately avoid the documents' wording ("pricier plan", "hook up to our team chat");
- thresholds were tuned on **dev only**, and the first test run (v1) was blind and is archived unchanged
  (`reports/EVALUATION_REPORT_v1_blind_test.md`).

**Before quoting the numbers, the team should:**

1. Read every question and label (about 30 minutes) and fix anything you disagree with.
2. Have a second member label the 10 questions in `second_labeller_template.csv` **without looking at
   `questions.json`**. Then run `python -m eval.agreement eval/second_labeller_template.csv`, report the
   agreement, and write any disagreements down as assumptions.
3. Add a few questions of your own, written without looking at the system.

## Categories (following the plan's recipe)

| Category | Count (dev/test) | What it tests | Expected status |
|---|---|---|---|
| direct | 18 (6/12) | Fact stated in one passage, asked with similar wording | ANSWERED |
| paraphrase | 12 (4/8) | Same facts in everyday words, avoiding document vocabulary | ANSWERED |
| multi_passage | 6 (2/4) | Answer needs 2+ passages, sometimes from 2 documents | ANSWERED |
| conflict | 8 (3/5) | Documents disagree, or a newer version changes the answer | ANSWERED_WITH_CONFLICT |
| ambiguous | 8 (3/5) | Answer differs by plan and the question does not say which | NEEDS_CLARIFICATION |
| unanswerable | 8 (2/6) | Plausible for the domain but absent from the collection | NOT_FOUND |

The split is fixed in the file (`"split": "dev" | "test"`) and stratified by category.

## Planted conflicts (ground truth for FR-06)

| Topic | Older statement | Newer statement |
|---|---|---|
| Account lockout | FAQ (2025-11-10): locked 30 min after 3 attempts | User Manual v3.1 (2026-01-15): 15 min after 5 attempts |
| Annual refund window | FAQ (2025-11-10): 14 days | Billing Policy v2.0 (2026-03-01): 30 days |
| API rate limit | User Manual v3.1: 100 req/min | Release Notes v3.2 (2026-06-01): 300 req/min |
| Web session timeout | User Manual v3.1: 30 min | Release Notes v3.2: 60 min |
| Pro audit-log retention | Admin Handbook v1.4 (2026-02-01): 90 days | Release Notes v3.2: 180 days |
| Export formats | KB-1042 (2026-02-10): CSV and JSON | Release Notes v3.2: adds Parquet |

Plan-dependent facts (for NEEDS_CLARIFICATION) are price, storage, upload size, API calls per day, users,
projects, support response time and audit-log retention.

The unanswerable questions (SAP, on-premises, HIPAA, cryptocurrency, dark mode, Linux app, white-labelling,
offline mobile) were checked to be absent from every document by text search.

## Labels

Each question stores:

- `relevant`: a list of `{doc, section, grade}`. Relevance is labelled at **section level**: a passage is
  relevant if its document id matches and its section path contains the `section` string. Grade 2 means it
  answers the question; grade 1 means partially or related. Section-level labels survive re-chunking, so
  chunk-size experiments do not need relabelling.
- `key_points`: 1-4 short strings that a correct answer must contain (case-insensitive substring match). For
  ambiguous questions they are the plan names the clarification should offer; for conflict questions, both
  conflicting values.
- `expected_status`: one of the four response statuses.

## Label changes (all made before the blind test run)

- q41 (dev): added `billing_policy › Mid-cycle upgrades` as grade 1, because that passage also states the 30-day
  annual window. Found during dev review.
- q27 (test): section name changed from "Introduction" to the guide's own heading after the chunker began
  naming introductory sections after the document heading. The labelled passage is the same; only the name changed.

## Metrics computed (`python -m eval.run_eval`)

Retrieval: Recall@5/10, MRR, nDCG@5, for each retrieval configuration (ablation). Answers: status accuracy,
key-point correctness, citation precision, automatic groundedness. Behaviour: abstention accuracy,
false-refusal rate, conflict recall, false-conflict rate, clarify rate. System: latency p50/p95. Failure
buckets: retrieval miss, chunk split, missed/false conflict, over-refusal, under-abstention, missed/unneeded
clarification, citation mismatch, incomplete answer.
