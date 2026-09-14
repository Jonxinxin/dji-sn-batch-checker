@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Python build environment is missing. Run install-python.cmd first.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements-build.txt"
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\build_exe.py"
if errorlevel 1 goto failed
echo Release files are in the dist folder.
pause
exit /b 0
:failed
echo Build failed. Review the error above.
pause
exit /b 1
