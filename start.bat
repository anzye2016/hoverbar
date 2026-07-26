@echo off
cd /d "%~dp0"

fltmc >nul 2>&1 || (
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

title HoverBar

if not exist .venv (
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt -q
)

.venv\Scripts\python -m pip install -r requirements.txt -q
start /min "" .venv\Scripts\pythonw.exe hoverbar.pyw
