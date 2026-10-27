@echo off
setlocal
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 (echo Python 3 required. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed --name MiracleOfLifeRecorder --collect-all flask --collect-all obsws_python --collect-all pystray app.py
if errorlevel 1 (echo Build failed. & pause & exit /b 1)
echo Built: dist\MiracleOfLifeRecorder.exe
pause
