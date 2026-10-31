"""FastAPI app: REST API + serves the frontend (../../frontend).

Run from the backend folder:
    python -m uvicorn app.api.server:app --port 8000
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import FRONTEND_DIR, KB_DIR, LLM_MODEL
from ..ingestion.parsers import SUPPORTED, parse_file
from ..qa import answer, llm
from ..retrieval.index import get_index

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI(title="Enterprise Documentation Q&A Assistant", version="1.0",
              description="Grounded answers with citations from PDF, HTML, Markdown and TXT documents.")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=1000)
    plan: str | None = Field(None, description="Basic | Pro | Enterprise (optional context)")
    version: str | None = Field(None, description="Restrict to documents with this version, e.g. 3.2")
    use_llm: bool | None = Field(None, description="None = automatic, False = force extractive")


@app.on_event("startup")
def _startup():
    get_index()  # loads (or builds) the index and warms the models on first use


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def home():
    return (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    idx = get_index()
    return {"status": "ok", **idx.stats(), "llm": {"available": llm.available(), "model": LLM_MODEL},
            "thresholds": answer.thresholds(), "supported_formats": sorted(set(SUPPORTED))}


@app.post("/api/ask")
def ask(req: AskRequest):
    plan = req.plan if req.plan in ("Basic", "Pro", "Enterprise") else None
    version = req.version.strip().lstrip("vV") if req.version and req.version.strip() else None
    return answer.ask(req.question.strip(), version=version, plan=plan, use_llm=req.use_llm)


@app.get("/api/documents")
def documents():
    idx = get_index()
    return {"documents": sorted(idx.docs.values(), key=lambda d: d["title"].lower()), "stats": idx.stats()}


def _safe_name(name: str) -> str:
    name = Path(name or "").name
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(" .")
    if not name or Path(name).suffix.lower() not in SUPPORTED:
        raise HTTPException(400, f"Unsupported file '{name}'. Supported: PDF, HTML, Markdown, TXT.")
    return name


@app.post("/api/documents")
async def upload(files: list[UploadFile] = File(...)):
    idx = get_index()
    saved = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        for f in files:
            name = _safe_name(f.filename)
            data = await f.read()
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"{name} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
            if not data.strip():
                raise HTTPException(400, f"{name} is empty")
            p = Path(tmp) / name
            p.write_bytes(data)
            # parse before accepting, so a broken file never enters the collection
            try:
                parse_file(p)
            except Exception as e:
                raise HTTPException(422, f"Could not parse {name}: {e}") from e
            saved.append(p)
        KB_DIR.mkdir(parents=True, exist_ok=True)
        for p in saved:
            (KB_DIR / p.name).write_bytes(p.read_bytes())
    report = idx.build()
    return {"uploaded": [p.name for p in saved], "index": report}


@app.delete("/api/documents/{doc_id}")
def delete(doc_id: str):
    if not get_index().remove_doc(doc_id):
        raise HTTPException(404, "Unknown document")
    return {"deleted": doc_id}


@app.post("/api/reindex")
def reindex():
    return get_index().build()


@app.get("/api/documents/{doc_id}/chunks")
def chunks(doc_id: str):
    idx = get_index()
    if doc_id not in idx.docs:
        raise HTTPException(404, "Unknown document")
    return {"document": idx.docs[doc_id], "chunks": [c.to_dict() for c in idx.chunks if c.doc_id == doc_id]}


@app.get("/api/documents/{doc_id}/file")
def source_file(doc_id: str):
    idx = get_index()
    doc = idx.docs.get(doc_id)
    if not doc:
        raise HTTPException(404, "Unknown document")
    path = (KB_DIR / doc["source"]).resolve()
    if KB_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(404, "File not found")
    media = {"pdf": "application/pdf", "html": "text/html; charset=utf-8"}.get(doc["format"], "text/plain; charset=utf-8")
    # Uploaded HTML is shown in a sandbox (no scripts, separate origin) so it cannot attack the app.
    headers = {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff",
               "Content-Disposition": f'inline; filename="{doc["source"]}"'}
    return FileResponse(path, media_type=media, headers=headers)


@app.exception_handler(Exception)
async def _errors(_, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})
