#!/usr/bin/env bash
# Start the DocQA Assistant on http://localhost:8000 (macOS / Linux)
# First time only: installs Python packages and downloads ~110 MB of models.
set -e
cd "$(dirname "$0")/backend"
PY=${PYTHON:-python3}
$PY -c "import fastapi, fastembed, pymupdf" 2>/dev/null || $PY -m pip install -r requirements.txt
echo "Open http://localhost:8000  (API docs: http://localhost:8000/docs)"
exec $PY -m uvicorn app.api.server:app --host 127.0.0.1 --port "${PORT:-8000}"
