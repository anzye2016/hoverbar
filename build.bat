@echo off
REM HoverBar - Build exe
cd /d "%~dp0"
.venv\Scripts\python.exe -m PyInstaller HoverBar.spec --distpath .
if %errorlevel% equ 0 (
    echo.
    echo Build success! exe at %cd%\HoverBar.exe
    echo Cleaning temp files...
    if exist build\ rmdir /s /q build
    if exist dist\ rmdir /s /q dist
    if exist __pycache__ rmdir /s /q __pycache__
) else (
    echo.
    echo Build failed, check error messages above.
)
