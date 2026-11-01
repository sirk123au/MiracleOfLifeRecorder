@echo off
setlocal
cd /d "%~dp0"
set "PYINSTALLER_CONFIG_DIR=%CD%\build\pyinstaller-cache"
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if errorlevel 1 (echo Python 3 required. & goto failed)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 (echo Dependency setup failed. & goto failed)
python -m pip install -r requirements.txt "pyinstaller>=6,<7"
if errorlevel 1 (echo Dependency installation failed. & goto failed)
python -m unittest discover -s tests -v
if errorlevel 1 (echo Workflow tests failed. Build cancelled. & goto failed)
python packaging\prepare_version.py
if errorlevel 1 (echo Version resource failed. & goto failed)
python packaging\prepare_icon.py
if errorlevel 1 (echo Icon generation failed. & goto failed)
python -m PyInstaller --noconfirm --clean --noupx --onedir --windowed --name MiracleOfLifeRecorder --icon build\application.ico --version-file build\windows_version_info.txt --collect-all flask --collect-all obsws_python --collect-all pystray --collect-all boto3 --collect-all botocore --collect-submodules winrt app.py
if errorlevel 1 (echo Build failed. & goto failed)
python packaging\verify_bundle.py
if errorlevel 1 (echo Runtime bundle verification failed. & goto failed)
echo Built: dist\MiracleOfLifeRecorder\MiracleOfLifeRecorder.exe
echo Keep the entire folder together or distribute the Setup installer.
if /i not "%~1"=="--no-pause" pause
exit /b 0
:failed
if /i not "%~1"=="--no-pause" pause
exit /b 1
