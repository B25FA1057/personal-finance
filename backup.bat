@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else (
    echo .venv not found. Run: python -m venv .venv
    exit /b 1
)
python scripts\backup.py
pause
