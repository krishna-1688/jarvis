@echo off
rem Jarvis - first-time setup. Double-click this, or run it from a terminal.
rem Makes a private Python environment in .venv so Jarvis's packages never
rem clash with anything else on this PC, then runs the setup wizard.
cd /d "%~dp0"
setlocal

if exist ".venv\Scripts\python.exe" goto wizard

rem Python 3.11-3.12: some of Jarvis's packages (numpy, PyAudio, webrtcvad, winsdk)
rem have no ready-made build for newer Pythons yet, and building them from
rem source needs a C compiler most laptops don't have.
set "OKVER=import sys; sys.exit(not ((3, 11) <= sys.version_info[:2] <= (3, 12)))"
set "PY="
for %%V in (3.11 3.12) do (
  if not defined PY where py >nul 2>nul && py -%%V -c "" >nul 2>nul && set "PY=py -%%V"
)
if not defined PY where python >nul 2>nul && python -c "%OKVER%" >nul 2>nul && set "PY=python"
if not defined PY (
  echo.
  echo  Jarvis needs Python 3.11 or 3.12.
  echo  Newer versions ^(3.13+^) don't work yet - some packages aren't built for them.
  echo.
  echo  Install Python 3.12 from https://www.python.org/downloads/release/python-31210/
  echo  ^(scroll down to "Windows installer (64-bit)", and tick "Add python.exe to PATH"^),
  echo  then double-click setup.bat again. It can sit alongside any other Python you have.
  echo.
  pause
  exit /b 1
)
echo Creating a private Python environment in .venv using %PY% ...
%PY% -m venv .venv || (echo Could not create .venv & pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q

:wizard
".venv\Scripts\python.exe" setup.py %*
echo.
pause
