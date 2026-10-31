@echo off
REM Double-click to start the DocQA Assistant on http://localhost:8000
REM First time only: installs Python packages and downloads ~110 MB of models.
cd /d "%~dp0backend"
python --version >nul 2>&1 || (echo Python 3.10+ is required: https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) & pause & exit /b 1)
python -c "import fastapi, fastembed, pymupdf" >nul 2>&1 || (echo Installing requirements... & python -m pip install -r requirements.txt || (pause & exit /b 1))
echo.
echo Open http://localhost:8000 in your browser  (API docs: http://localhost:8000/docs)
echo Press Ctrl+C to stop.
python -m uvicorn app.api.server:app --host 127.0.0.1 --port 8000
pause
