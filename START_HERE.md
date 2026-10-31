# Start here

## Open in VS Code
Double-click **`docqa-assistant.code-workspace`**, or in VS Code use **File → Open Folder…** and choose this folder.
If VS Code asks to install the recommended **Python** extension, click **Install**.

## Run it (one-time setup, then one click)
You need **Python 3.10+** installed ([python.org](https://www.python.org/downloads/); on Windows tick
"Add python.exe to PATH").

In VS Code, open **Terminal → Run Task…** and run:
1. **`1. Install requirements (first time only)`**
2. **`2. Start app (http://localhost:8000)`**, or just press **Ctrl+Shift+B**

Then open **http://localhost:8000** in your browser. The first start downloads ~110 MB of models (1-3 minutes);
later starts take a few seconds.

Without VS Code: double-click **`run.bat`** (Windows) or run `bash run.sh` (Mac/Linux).

## Where things are
| Folder | What's inside |
|---|---|
| `backend/app/api/` | REST API server (FastAPI) |
| `backend/app/ingestion/` | Reads PDF / HTML / Markdown / TXT and splits them into passages |
| `backend/app/retrieval/` | Search: BM25 + embeddings + reranker |
| `backend/app/qa/` | Answers, citations, conflicts, clarification, not-found |
| `frontend/` | The web page (HTML, CSS, JS) |
| `knowledge_base/` | The documents the assistant answers from (add your own here) |
| `backend/eval/` | Test questions and evaluation scripts |
| `reports/` | Evaluation report |

Full documentation: **README.md**.
