@echo off
REM HoverBar launcher (double-click me).
REM Uses the project's .venv when present, otherwise falls back to pythonw on PATH.
setlocal
cd /d "%~dp0"

set "PYW="
set "PY="

if exist ".venv\Scripts\pythonw.exe" (
    set "PYW=%CD%\.venv\Scripts\pythonw.exe"
    set "PY=%CD%\.venv\Scripts\python.exe"
)

if not defined PYW (
    for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%I"
    for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PY set "PY=%%I"
)

if not defined PYW (
    echo [HoverBar] pythonw.exe not found.
    echo            Install Python 3.10+ or run build.bat to produce HoverBar.exe.
    pause
    exit /b 1
)

REM dependency check (silently installs when something is missing)
"%PY%" -c "import PySide6, psutil, pynvml, win32pdh" >nul 2>&1
if errorlevel 1 (
    echo [HoverBar] Installing dependencies...
    "%PY%" -m pip install -r requirements.txt
)

start "" "%PYW%" "%CD%\hoverbar.pyw"
endlocal
