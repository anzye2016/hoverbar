@echo off
cd /d "%~dp0"

if not exist .venv (
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt -q
)

.venv\Scripts\python -m pip install -r requirements.txt -q
start /min "" .venv\Scripts\pythonw.exe hoverbar.pyw
