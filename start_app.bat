@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python environment is missing. Create .venv and install requirements.txt first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -c "import flask, obsws_python, pystray, PIL, winrt.runtime, winrt.windows.media.control" >nul 2>nul
if errorlevel 1 (
    echo Project dependencies are missing. Run:
    echo .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
