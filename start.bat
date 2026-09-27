@echo off
cd /d "%~dp0"

if not exist .venv (
    python -m venv .venv
)

.venv\Scripts\python -c "import psutil, PySide6, pynvml, win32pdh" >nul 2>&1
if errorlevel 1 (
    .venv\Scripts\python -m pip install -r requirements.txt -q
)

start /min "" .venv\Scripts\pythonw.exe hoverbar.pyw
