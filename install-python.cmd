@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  python -m venv "%~dp0.venv"
  if errorlevel 1 goto failed
)
"%~dp0.venv\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto failed
echo Installation complete. Open the Python launcher to start.
pause
exit /b 0
:failed
echo Installation failed. Install Python 3.11 or newer, then retry.
pause
exit /b 1
