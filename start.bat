@echo off
rem Starts Jarvis: the backend, the voice loop and (on demand) the dashboard.
rem Say "Hey Jarvis", or press Ctrl+J for the dashboard. Ctrl+C stops it.
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not exist "backend\.env" (
  echo Jarvis isn't set up yet - running setup first.
  call setup.bat
  if not exist "backend\.env" exit /b 1
)
"%PY%" backend\run.py
if errorlevel 1 pause
