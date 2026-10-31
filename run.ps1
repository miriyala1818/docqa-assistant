# Start the DocQA Assistant on http://localhost:8000
# Usage:  .\run.ps1            (first run downloads ~110 MB of local models)
#         .\run.ps1 -Port 9000
param([int]$Port = 8000)
Set-Location (Join-Path $PSScriptRoot "backend")
Write-Host "Open http://localhost:$Port  (API docs: http://localhost:$Port/docs)"
python -m uvicorn app.api.server:app --host 127.0.0.1 --port $Port
