# Enterprise Documentation Q&A Assistant

HCLTech Future Ready AI Engineer Hackathon, use case 2. A documentation assistant that ingests PDF, HTML,
Markdown and TXT documents, retrieves the relevant passages for a natural-language question, and answers
**only from that evidence**, with a citation on every claim. It shows conflicts between documents instead of
silently picking one, asks a clarifying question when the answer depends on the user's plan, and says
"not found" (with the closest topics) when the documents don't cover the question.

It runs **fully offline on a CPU** (local ONNX models, no API key needed). If an Anthropic API key is set,
Claude writes the answer wording, and every generated sentence is still checked against its cited passage.

## Getting started after downloading from GitHub

**You need:** Python **3.10 or newer** ([python.org](https://www.python.org/downloads/); on Windows tick
**"Add python.exe to PATH"** in the installer), about 1 GB of free disk space, and internet for the first run only.
Git is optional.

1. **Download the code:** on GitHub click **Code → Download ZIP** and unzip it, or
   `git clone https://github.com/miriyala1818/docqa-assistant.git`.
2. **Start it:**
   - **Windows:** double-click **`run.bat`** in the project folder.
   - **macOS / Linux:** in a terminal, `cd docqa-assistant`, then `bash run.sh`.
   - **Manually (any OS):**
     ```
     cd docqa-assistant/backend
     python -m pip install -r requirements.txt
     python -m uvicorn app.api.server:app --port 8000
     ```
3. **Open http://localhost:8000.** The first start installs packages, downloads two small models (~110 MB, into
   `backend/models/`) and indexes `knowledge_base/`. This takes 1-3 minutes; later starts take a few seconds.

**Optional:** to have Claude write the answer wording, copy `.env.example` to `.env`, or set
`ANTHROPIC_API_KEY` in your terminal before starting. Without it, everything works offline.

**Troubleshooting**

| Problem | Fix |
|---|---|
| `python` is not recognized | Install Python 3.10+ with "Add to PATH" ticked, then open a new terminal. On macOS/Linux use `python3`. |
| `run.ps1 cannot be loaded because running scripts is disabled` | Use `run.bat` instead, or `powershell -ExecutionPolicy Bypass -File run.ps1`. |
| `Address already in use` / port 8000 busy | `.\run.ps1 -Port 9000` or `PORT=9000 bash run.sh`, then open http://localhost:9000. |
| Model download fails (offline / firewall / proxy) | Connect to the internet once; the models are then cached in `backend/models/`. |
| `pip install` fails building a package | Use Python 3.10-3.12 (tested on 3.12) and upgrade pip: `python -m pip install --upgrade pip`. |

## Quick start (if you already have the folder)

```powershell
cd "D:\Microsoft VS Code\docqa-assistant"
python -m pip install -r backend\requirements.txt
.\run.ps1                       # or: run.bat, or: cd backend; python -m uvicorn app.api.server:app --port 8000
```

Open **http://localhost:8000**. Interactive API docs are at http://localhost:8000/docs.

Optional, to have Claude write the answers:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."     # model: claude-opus-5-5 (override with DOCQA_LLM_MODEL)
.\run.ps1
```

In VS Code, open the **Run and Debug** panel (Ctrl+Shift+D) for one-click configurations: run the server,
unit tests, API smoke test, evaluation and threshold tuning.

Other commands (run them from the `backend` folder):

| Command | What it does |
|---|---|
| `python -m eval.run_eval` | Full evaluation on the test split; writes `reports/EVALUATION_REPORT.md` |
| `python -m eval.tune` | Re-tune decision thresholds on the dev split; writes `data/thresholds.json` |
| `python tests/test_core.py` | Unit tests (parsers, chunking, quantities, verifier, query understanding) |
| `python tests/smoke_api.py http://127.0.0.1:8000` | End-to-end API test against a running server (upload, ask, delete) |
| `python scripts/build_pdfs.py` | Rebuild the two sample PDFs from `scripts/corpus_src/` |

## Using your own documents

Drop files into `knowledge_base/` (the index rebuilds automatically when files change), or upload them in the
UI or via `POST /api/documents`. Version and date metadata are read from YAML front-matter (Markdown),
`<meta name="version">` / `<meta name="effective_date">` (HTML), and "Version 3.2" / "Effective 1 Jan 2026" /
"Last updated: ..." lines or a `_v3.2` filename (all formats). The plan names used for clarification
(`Basic`, `Pro`, `Enterprise`) live in `backend/app/utils/textutil.py::PLANS`; change them to match your product.
If you replace the sample collection, the questions in `backend/eval/questions.json` must be rewritten for it.

> **The included knowledge collection is synthetic.** "Orbitly" is a fictional SaaS product, written for this
> prototype because no document set was supplied. It contains deliberate traps: six cross-document conflicts
> and eight plan-dependent facts. See `backend/eval/README.md`.

## How it meets each requirement

| ID | Requirement | Implementation |
|---|---|---|
| FR-01 | Multi-format ingestion | `backend/app/ingestion/parsers.py`. PDF (PyMuPDF): font-size heading levels, page numbers, running header/footer removal, ligature and hyphenation repair. HTML (BeautifulSoup): h1-h6 hierarchy, anchor ids, nav/footer/script removed. Markdown: YAML front-matter, heading tree, tables, code blocks. TXT: ALL-CAPS / numbered / underlined headings. **All formats produce the same block list**, so everything downstream is format-independent. |
| FR-02 | Content preparation | `backend/app/ingestion/chunker.py`. Section-aware chunks (~320 tokens, 50-token overlap, never across sections); tiny sections merged; numbered procedures and tables kept whole; table rows linearised into self-describing lines (`Storage per workspace: Basic: 10 GB; Pro: 100 GB; ...`); a contextual header (`Doc > Section > Subsection:`) is embedded with every chunk; duplicate chunks removed by hash; version, date and doc type stored per chunk. |
| FR-03 | Natural-language retrieval | `backend/app/retrieval/index.py`. BM25 (exact terms, error codes) + dense `bge-small-en-v1.5` (meaning), fused by Reciprocal Rank Fusion; top 30 reranked by the `ms-marco-MiniLM-L-6` cross-encoder; glossary query expansion for terminology variation (`login credentials` → `password`); version filter. |
| FR-04 | Grounded answers | `backend/app/qa/answer.py`. An **evidence gate** runs before answering. Answers are built from the highest-scoring evidence sentences (extractive), or written by Claude from numbered passages with JSON output. **Post-generation verification** checks that every number in a sentence appears in its cited passage and that ≥60% of its content words are covered. Unsupported sentences are removed, and if none remain the status becomes NOT_FOUND. |
| FR-05 | Citations | Sentence-level citation chips. Each citation carries document title, section path, page (PDF) or anchor (HTML), version, effective date, the excerpt with the cited sentence highlighted, and a deep link to the source file (`#page=N` / `#anchor`). |
| FR-06 | Conflict handling | Numeric claims (value + unit, normalised: minutes, days, GB, req/min, attempts, $...) are extracted from relevant sentences. Two documents conflict when they give different values for the same dimension and plan, about the same thing (shared content words). Superseded values ("from 100 to 300", "previously 30 minutes") are ignored. Explicit version changes ("in addition to", "no longer") from changelogs are flagged too. The UI shows a "Sources disagree" panel with every side, its version and date, and which is newest. |
| FR-07 | Confidence and fallback | NOT_FOUND when the evidence is below a tuned threshold, or the question names something absent from the whole collection (e.g. "SAP"), shown with the 3 closest topics. NEEDS_CLARIFICATION when the strongest evidence gives different values per plan and the question doesn't name one; each option is shown with its citation. A confidence level is derived from the rerank score, margin and verified ratio. |
| FR-08 | Evaluation | `backend/eval/`: 60 labelled questions (direct, paraphrase, multi-passage, conflict, ambiguous, unanswerable), dev/test split, thresholds tuned on dev only, retrieval ablation, answer and behaviour metrics, latency, failure buckets with real examples. Report: `reports/EVALUATION_REPORT.md`. |

## Architecture

```
OFFLINE: indexing (on upload / file change)
  files ──► parse (per format) ──► blocks + metadata ──► section-aware chunks ──► BM25 index
                                                                               └► dense vectors (bge-small)
ONLINE: per question
  question ─► query understanding ─► hybrid retrieve ─► cross-encoder ─► evidence gate ─┐
             (glossary, plan,        (BM25 30 + dense    rerank → top 8   (score, unknown │
              version)                30, RRF)                             terms)          │
  ┌───────────────────────────────────────────────────────────────────────────────────────┘
  └► sentence scoring ─► answer (extractive | Claude JSON) ─► verify each sentence ─► conflict + clarification
                                                                                     checks
                         ─► response: ANSWERED | ANSWERED_WITH_CONFLICT | NEEDS_CLARIFICATION | NOT_FOUND
```

## Evaluation results

These are the real numbers from `python -m eval.run_eval` (40 held-out test questions, CPU, extractive mode).
Full details, failure analysis and examples: [`reports/EVALUATION_REPORT.md`](reports/EVALUATION_REPORT.md).

| Metric | v1: blind test run | v2: after failure analysis* |
|---|---|---|
| Status accuracy | 90.0% | 95.0% |
| Key-point correctness | 68.9% | 84.6% |
| Citation precision | 95.0% | 95.3% |
| Abstention accuracy (unanswerable → NOT_FOUND) | 100% | 100% |
| False-refusal rate | 11.8% | 5.9% |
| Conflict recall / false-conflict rate | 100% / 0% | 100% / 0% |
| Clarify rate (ambiguous) | 80.0% | 100% |

\*v2 fixed four design weaknesses found in v1's failure analysis (general rules, no test-specific tweaks). Because
the test split was inspected between v1 and v2, **v1 is the unbiased estimate**.

| Retrieval setup (test) | Recall@5 | MRR | nDCG@5 | Latency p50 |
|---|---|---|---|---|
| BM25 only | 0.912 | 0.817 | 0.838 | <1 ms |
| Dense only | 0.941 | 0.890 | 0.880 | 13 ms |
| Hybrid (RRF) | 0.941 | 0.892 | 0.906 | 14 ms |
| Hybrid + reranker | 0.971 | 0.956 | 0.950 | 1.2 s |
| Hybrid + reranker + query expansion | 0.971 | 0.956 | 0.952 | 2.3 s |

The reranker gives the largest quality jump (MRR 0.89 → 0.96). On this test set, query expansion adds almost
nothing to retrieval ranking. It helps the evidence gate instead (fewer false refusals on paraphrases),
at the cost of a second reranker pass.

## REST API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/ask` | `{"question": "...", "plan": "Pro" \| null, "version": "3.2" \| null, "use_llm": null}` → status, answer sentences with citations, conflicts, clarifying question and options, closest topics, signals, retrieved passages, timings |
| `GET` | `/api/documents` | Indexed documents with metadata |
| `POST` | `/api/documents` | Upload one or more files (multipart field `files`); validated and parsed before acceptance |
| `DELETE` | `/api/documents/{doc_id}` | Remove a document and re-index |
| `GET` | `/api/documents/{doc_id}/chunks` | The passages a document was split into |
| `GET` | `/api/documents/{doc_id}/file` | The original file (citation deep links); HTML is served sandboxed |
| `POST` | `/api/reindex` | Rebuild the index |
| `GET` | `/api/health` | Counts, LLM availability, active thresholds |

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/ask -ContentType application/json `
  -Body '{"question": "What is the API rate limit?"}' | ConvertTo-Json -Depth 6
```

The UI also supports shareable links: `http://localhost:8000/?q=How+much+storage+do+I+get%3F&plan=Pro`.

## Design decisions and trade-offs

- **Extractive by default, LLM optional.** Verbatim sentences cannot hallucinate, run offline and cost nothing,
  but the answers read less smoothly. Claude produces better-written answers. Its sentences pass the same verifier,
  and the local conflict and clarification checks still run on top of its output. *The Claude path is
  implemented but was not exercised in this build (no API key was available); any API error falls back to
  extractive mode with a warning in the response.*
- **Rule-based conflict detection.** It is precise and explainable (0 false conflicts on test) and needs no
  model, but it only sees numeric disagreements and explicit change wording. Contradictions stated purely in
  words need the LLM / NLI confirmation step (listed as next work).
- **Section-level relevance labels** instead of chunk ids, so chunk-size changes don't invalidate the test set.
- **Thresholds tuned, not guessed**: a grid search on 20 dev questions. That set is small, so the thresholds
  should be re-tuned on real questions before production.
- **Latency.** The cross-encoder dominates (~25 ms per passage on CPU; ~2-3 s per question end to end).
  Options: rerank 20 instead of 30, disable query expansion (halves rerank cost), or use a GPU.
- **Unknown-term check.** Capitalised words or acronyms in the question that never occur in the collection
  (SAP, HIPAA, Linux) trigger NOT_FOUND. This is cheap and precise, but a new product name not yet in the
  documents will also be treated as unknown.
- **Single-process, file-based index** (JSON + NumPy). It is simple and fast for hundreds of documents; a vector DB
  would be the next step for large collections.

## Assumptions

- A newer effective date means a newer document. The assistant says which statement is newest, but never hides
  the older one, because the user may still be on the older version.
- Plans are the main reason one question has several valid answers, so clarification is keyed to plan names.
- The knowledge collection is trusted internal content. Uploaded HTML is still served sandboxed, and uploads are
  size-limited, type-checked and parsed before they are accepted.
- Out of scope, per the brief: editing source documents or acting on enterprise systems.

## Project structure

```
docqa-assistant/
├── backend/                         Python (FastAPI) service
│   ├── app/
│   │   ├── config.py                paths, model names, thresholds
│   │   ├── api/
│   │   │   └── server.py            REST API + serves the frontend
│   │   ├── ingestion/               FR-01, FR-02
│   │   │   ├── parsers.py           PDF / HTML / Markdown / TXT → common blocks + metadata
│   │   │   └── chunker.py           section-aware chunking, contextual headers, dedupe
│   │   ├── retrieval/               FR-03
│   │   │   ├── index.py             BM25 + dense + RRF + cross-encoder, persistence
│   │   │   └── models.py            local ONNX embedding + reranker models (fastembed)
│   │   ├── qa/                      FR-04 … FR-07
│   │   │   ├── answer.py            evidence gate, answer, verifier, conflicts, clarification, fallback
│   │   │   └── llm.py               optional Claude generation (structured JSON)
│   │   └── utils/
│   │       └── textutil.py          tokenising, glossary, quantity/unit/plan extraction
│   ├── eval/                        FR-08: questions.json, run_eval.py, tune.py, agreement.py, README.md
│   ├── tests/                       test_core.py (unit), smoke_api.py (end-to-end API)
│   ├── scripts/                     build_pdfs.py (+ corpus_src/), bench_reranker.py
│   ├── data/                        thresholds.json (tuned on dev), index/ (generated)
│   └── requirements.txt
├── frontend/                        single-page UI (no build step, no external assets)
│   ├── index.html
│   ├── css/styles.css
│   └── js/app.js
├── knowledge_base/                  the document collection (9 sample documents)
├── reports/                         EVALUATION_REPORT.md (+ archived v1 blind run)
├── .vscode/                         Run & Debug configurations, editor settings
├── run.ps1                          start the app
└── README.md
```
