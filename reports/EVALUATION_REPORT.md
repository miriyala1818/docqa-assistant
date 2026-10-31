# Evaluation Report: Enterprise Documentation Q&A Assistant

Generated 2026-10-31 12:04 by `python -m eval.run_eval --split test` on Windows 11, Python 3.12.0. Answer generator: **local extractive (no LLM)**. All numbers below are computed by the script; none are typed in by hand.

## 1. Test set

60 labelled questions over a 9-document sample collection (2 PDF, 2 HTML, 3 Markdown, 2 TXT), split into **20 dev** (used only to tune thresholds) and **40 test** (reported below). Construction and assumptions: `eval/README.md`.

| Category | Total | Expected status |
|---|---|---|
| direct | 18 | ANSWERED |
| paraphrase | 12 | ANSWERED |
| multi_passage | 6 | ANSWERED (2+ citations) |
| conflict | 8 | ANSWERED_WITH_CONFLICT |
| ambiguous | 8 | NEEDS_CLARIFICATION |
| unanswerable | 8 | NOT_FOUND |

## 2. Retrieval quality and ablation

Computed on the 34 answerable questions of the `test` split. A passage is relevant if its document and section match a labelled section (grade 2 = answers it, 1 = partial). Recall@k and MRR use grade-2 passages; nDCG@5 uses the graded labels. Latency = retrieval only, CPU.

| Retrieval setup | Recall@5 | Recall@10 | MRR | nDCG@5 | Latency p50 | Latency p95 |
|---|---|---|---|---|---|---|
| BM25 only | 0.912 | 0.971 | 0.817 | 0.838 | 0 ms | 1 ms |
| Dense only | 0.941 | 0.971 | 0.890 | 0.880 | 13 ms | 20 ms |
| Hybrid (RRF) | 0.941 | 0.941 | 0.892 | 0.906 | 14 ms | 20 ms |
| Hybrid + reranker | 0.971 | 0.971 | 0.956 | 0.950 | 1243 ms | 1629 ms |
| Hybrid + reranker + query expansion | 0.971 | 0.971 | 0.956 | 0.952 | 2332 ms | 3120 ms |

## 3. Answer quality and behaviour

| Area | Metric | Value | Meaning |
|---|---|---|---|
| Behaviour | Status accuracy | 95.0% | Response status matched the label (40 questions) |
| Answer | Correctness | 84.6% | Share of gold key points present in the answer (answerable questions) |
| Answer | Citation precision | 95.3% | Citations that point at a labelled-relevant passage |
| Answer | Groundedness (automatic) | 100.0% | Answer sentences that pass the number + term-coverage verifier against their cited passage |
| Behaviour | Abstention accuracy | 100.0% | Unanswerable questions returned as NOT_FOUND |
| Behaviour | False-refusal rate | 5.9% | Answerable questions wrongly returned as NOT_FOUND |
| Behaviour | Conflict recall | 100.0% | Conflict questions flagged ANSWERED_WITH_CONFLICT |
| Behaviour | False-conflict rate | 0.0% | Non-conflict questions wrongly flagged as conflicts |
| Behaviour | Clarify rate | 100.0% | Ambiguous questions that got a clarifying question |
| System | Latency p50 / p95 | 3247 / 4381 ms | End-to-end time per question, CPU only |

**About groundedness:** in extractive mode every answer sentence is copied from its cited passage, so this metric is high by construction. It becomes informative in LLM mode, where the same verifier removes unsupported generated sentences. Citation precision and correctness are the stronger signals here.

### Status accuracy by category

| Category | n | Correct | Accuracy | Mean correctness |
|---|---|---|---|---|
| direct | 12 | 12 | 100% | 94% |
| paraphrase | 8 | 6 | 75% | 50% |
| multi_passage | 4 | 4 | 100% | 88% |
| conflict | 5 | 5 | 100% | 100% |
| ambiguous | 5 | 5 | 100% | 100% |
| unanswerable | 6 | 6 | 100% | 100% |

### Confusion matrix (expected → got)

| expected \ got | ANSWERED | ANSWERED_WITH_CONFLICT | NEEDS_CLARIFICATION | NOT_FOUND |
|---|---|---|---|---|
| ANSWERED | 22 | 0 | 0 | 2 |
| ANSWERED_WITH_CONFLICT | 0 | 5 | 0 | 0 |
| NEEDS_CLARIFICATION | 0 | 0 | 5 | 0 |
| NOT_FOUND | 0 | 0 | 0 | 6 |

### Iteration history (test split)

| Metric | v1 (blind run) | v2 (this run) |
|---|---|---|
| Status accuracy | 90.0% | 95.0% |
| Correctness | 68.9% | 84.6% |
| Citation precision | 95.0% | 95.3% |
| Abstention accuracy | 100.0% | 100.0% |
| False-refusal rate | 11.8% | 5.9% |
| Conflict recall | 100.0% | 100.0% |
| False-conflict rate | 0.0% | 0.0% |
| Clarify rate | 80.0% | 100.0% |

**v1 → v2.** v1 was tuned on dev and then run once on test (the only blind run; archived as
`reports/EVALUATION_REPORT_v1_blind_test.md`). Its failure analysis showed four design weaknesses, which were
fixed with general rules (no question-specific glossary entries or labels were added for test questions):

1. **Sentence window credit.** When a sentence pair scores high together, both sentences now get the credit
   (v1 credited only the second one, so "Invitations expire after 7 days" lost to the sentence after it).
2. **Evidence gate.** The gate now passes if the best passage *or* the best single sentence is strong enough.
   A long table chunk can score low as a whole while one row answers exactly ("How many projects can I create?").
3. **Clarification options.** Once the strongest evidence triggers a clarification, options are collected from
   all relevant evidence for that quantity (v1 dropped the Enterprise support row).
4. **Glossary matching.** Multi-word glossary phrases also match when all their content words appear in the
   question, not only as an exact phrase.

Thresholds were then re-tuned on dev only (`max_units` was added to the grid). **Because the test split was
inspected between v1 and v2, the v2 test numbers are no longer blind and are optimistic;** the v1 numbers are
the unbiased estimate.

## 4. Failure analysis

Every question with a wrong status, missing key points or an off-target citation is put in one bucket (first matching rule in `eval/run_eval.py::bucket`).

| Bucket | Count |
|---|---|
| incomplete answer | 2 |
| over-refusal | 2 |
| citation mismatch | 1 |
| retrieval miss | 1 |
| chunk split the answer | 1 |

**citation mismatch** — q10: *Which identity providers are supported for SSO?* (expected `ANSWERED`, got `ANSWERED`)

**Q (direct):** Which identity providers are supported for SSO?  
**Status:** `ANSWERED` · confidence high  
> Single sign-on (SSO) lets members sign in to Orbitly with their company identity provider instead of a separate password. [1]  
> SSO and SCIM provisioning are available on the Enterprise plan only. [1]  
> Tested configurations are available for Okta, Microsoft Entra ID (formerly Azure AD), Google Workspace and OneLogin. [2]  
> Orbitly supports any SAML 2.0 identity provider. [2]  

- [1] SSO Configuration Guide (v1.2, 2026-04-12) › Configuring SAML Single Sign-On, #sso-guide — `sso_configuration_guide.html`
- [2] SSO Configuration Guide (v1.2, 2026-04-12) › Supported identity providers, #supported-idps — `sso_configuration_guide.html`

**incomplete answer** — q17: *What roles are available in Orbitly?* (expected `ANSWERED`, got `ANSWERED`; missing key points: Admin, Member, Guest)

**Q (direct):** What roles are available in Orbitly?  
**Status:** `ANSWERED` · confidence high  
> Orbitly has four roles. [1]  
> The Owner has full control, including billing and deleting the workspace; each workspace has exactly one Owner, and ownership can be transferred. [1]  

- [1] Orbitly Administrator Handbook (v1.4, 2026-02-01) › 1. Roles and Permissions, p. 1 — `orbitly_admin_handbook.pdf`

**over-refusal** — q23: *Where can I get a copy of an old bill?* (expected `ANSWERED`, got `NOT_FOUND`; missing key points: Settings > Billing > Invoices)

**Q (paraphrase):** Where can I get a copy of an old bill?  
**Status:** `NOT_FOUND`  
> I couldn't find this in the documentation.  
> Closest topics: Orbitly Billing and Plans Policy > Invoices; Orbitly Billing and Plans Policy > Billing and Plans Policy; Orbitly Frequently Asked Questions > Billing > Can I get a refund?

**retrieval miss** — q26: *How do I get all of our data out of Orbitly?* (expected `ANSWERED`, got `ANSWERED`; missing key points: Settings > Workspace > Export)

**Q (paraphrase):** How do I get all of our data out of Orbitly?  
**Status:** `ANSWERED` · confidence low  
> To create a workspace, sign up at the Orbitly sign-up page, choose a workspace name and select a data region. [1]  
> You can rename the workspace at any time from Settings > Workspace > General. [1]  

- [1] Orbitly User Manual (v3.1, 2026-01-15) › 1. Getting Started > 1.1 Creating your workspace, p. 1 — `orbitly_user_manual_v3.1.pdf`

**chunk split the answer** — q27: *Can our staff log in with their company Okta accounts?* (expected `ANSWERED`, got `ANSWERED`; missing key points: Okta)

**Q (paraphrase):** Can our staff log in with their company Okta accounts?  
**Status:** `ANSWERED` · confidence medium  
> Single sign-on (SSO) lets members sign in to Orbitly with their company identity provider instead of a separate password. [1]  
> SSO and SCIM provisioning are available on the Enterprise plan only. [1]  

- [1] SSO Configuration Guide (v1.2, 2026-04-12) › Configuring SAML Single Sign-On, #sso-guide — `sso_configuration_guide.html`

### What we would fix next

- **Retrieval misses / paraphrases:** extend the glossary from real query logs, or add LLM query rewriting (toggle) and re-run the ablation to prove it.
- **Missed or false conflicts:** add an NLI / LLM confirmation step for candidate pairs (already used in LLM mode) so wording-only contradictions are caught and coincidental number differences are rejected.
- **Over-refusal vs. under-abstention:** tune the evidence gate on a larger dev set; consider a per-category calibration of the reranker score.
- **Incomplete answers:** raise `max_units` for multi-part questions or split the question into sub-questions before retrieval.

## 5. Evidence-backed examples

Real outputs from this run, one per question type.

### q07

**Q (direct):** When are invoices sent?  
**Status:** `ANSWERED` · confidence medium  
> Invoices are emailed to the billing contact on the first day of each billing cycle. [1]  
> Past invoices can be downloaded as PDF from Settings > Billing > Invoices. [1]  

- [1] Orbitly Billing and Plans Policy (v2.0, 2026-03-01) › Invoices — `billing_policy.md`

### q25

**Q (paraphrase):** The verification email never showed up. What can I do?  
**Status:** `ANSWERED` · confidence medium  
> Ask your IT team to allow emails from no-reply@orbitly.example. [1]  
> You can request a new verification email after 5 minutes from the sign-in page. [1]  
> Corporate email filters sometimes delay messages by up to 15 minutes. [1]  
> Check your spam or junk folder first. [1]  

- [1] Orbitly Troubleshooting Guide (v3.1, 2026-05-20) › VERIFICATION EMAIL NOT RECEIVED — `troubleshooting_guide.txt`

### q33

**Q (multi_passage):** Why can't I upload a file, and how can I fix it?  
**Status:** `ANSWERED` · confidence medium  
> Uploads fail when the file is larger than the maximum upload size for your plan or when the workspace storage is full. [1]  
> Check the usage limits for your plan, delete unused files, or buy a storage add-on. [1]  

- [1] Orbitly Troubleshooting Guide (v3.1, 2026-05-20) › CANNOT UPLOAD FILES — `troubleshooting_guide.txt`

### q38

**Q (conflict):** How many API requests per minute can I make?  
**Status:** `ANSWERED_WITH_CONFLICT` · confidence medium  
> *Most recent (Orbitly 3.2 Release Notes v3.2, 2026-06-01):* - The default API rate limit has been increased from 100 to 300 requests per minute per API key. [1]  
> *Older (Orbitly User Manual v3.1, 2026-01-15):* The Orbitly REST API allows 100 requests per minute per API key. [2]  
> Requests above the limit receive HTTP status 429 with error code ORB-429 and a Retry-After header that tells you how many seconds to wait. [2]  
> - A new /v2/exports endpoint lets you start and monitor workspace exports programmatically. [1]  
> ⚠ Sources disagree on the rate limit. The most recent source is Orbitly 3.2 Release Notes (version 3.2, effective 2026-06-01). Check which version applies to you; the older statement is shown for transparency.  

- [1] Orbitly 3.2 Release Notes (v3.2, 2026-06-01) › API changes — `release_notes_v3.2.md`
- [2] Orbitly User Manual (v3.1, 2026-01-15) › 5. API Access > 5.2 Rate limits, p. 4 — `orbitly_user_manual_v3.1.pdf`

### q48

**Q (ambiguous):** How many API calls can I make per day?  
**Status:** `NEEDS_CLARIFICATION` · confidence medium  
> The answer depends on your plan. Which plan are you on: Basic, Pro or Enterprise?   
> Options: API calls per day: Basic: 10,000 calls [1]; API calls per day: Pro: 100,000 calls [1]; API calls per day: Enterprise: 1,000,000 calls [1]

- [1] Orbitly Usage Limits by Plan (v3.2, 2026-06-01) › Usage Limits by Plan — `usage_limits.md`

### q55

**Q (unanswerable):** Is Orbitly HIPAA compliant?  
**Status:** `NOT_FOUND`  
> I couldn't find this in the documentation. The documents never mention HIPAA.  
> Closest topics: Orbitly User Manual > 2. Account Access > 2.4 Two-factor authentication (2FA); Orbitly Administrator Handbook > 5. Security Settings; SSO Configuration Guide > Supported identity providers

## 6. Method and limitations

- Thresholds (tuned on dev by `python -m eval.tune`): `{"nf_top": -1.0, "sent_min": -1.0, "sent_margin": 3.0, "claim_min": -1.0, "claim_margin": 4.0, "clarify_margin": 1.0, "max_units": 4}`.
- Answer-quality method: automatic key-point matching (case-insensitive substring of 1-4 gold key points per question), citation precision against section-level relevance labels, and the sentence verifier. A human audit of ~20 answers is recommended before quoting groundedness externally.
- The collection and questions are synthetic and were written by the same author as the system (an AI assistant), so the numbers show the system works as designed on representative cases; they are not a benchmark. Independent questions from a second person would give a fairer estimate (see `eval/README.md`).
- 40 test questions means each question moves a rate by 2.5 points; small differences between ablation rows are within noise.
- Conflict detection covers numeric disagreements (values with units) and explicit version updates ('in addition to', 'no longer', ...). Conflicts stated purely in words (e.g. 'allowed' vs 'not allowed') are only caught in LLM mode.
