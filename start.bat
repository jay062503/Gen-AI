@echo off
rem Media Studio launcher for Windows.  start.bat [--ml|--no-ml|--reinstall] [--port N] [--host H]
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "SETUP_ARGS="
set "RUN_ARGS=--open"
:parse
if "%~1"=="" goto parsed
if "%~1"=="--ml" (set "SETUP_ARGS=!SETUP_ARGS! --ml") else if "%~1"=="--no-ml" (set "SETUP_ARGS=!SETUP_ARGS! --no-ml") else if "%~1"=="--reinstall" (set "SETUP_ARGS=!SETUP_ARGS! --reinstall") else if "%~1"=="--no-open" (set "RUN_ARGS=!RUN_ARGS:--open=!") else (set "RUN_ARGS=!RUN_ARGS! %~1")
shift
goto parse
:parsed

if exist ".venv\Scripts\python.exe" goto havevenv
set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12" && goto foundpy
py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13" && goto foundpy
py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11" && goto foundpy
py -3.10 -c "import sys" >nul 2>&1 && set "PY=py -3.10" && goto foundpy
python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1 && set "PY=python" && goto foundpy
echo Python 3.10 - 3.13 was not found.
echo Install Python 3.12 from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again.
pause
exit /b 1
:foundpy
echo Creating virtual environment...
%PY% -m venv .venv || (echo Could not create the virtual environment. & pause & exit /b 1)
:havevenv

".venv\Scripts\python.exe" scripts\setup.py %SETUP_ARGS% || (echo Setup failed. & pause & exit /b 1)
".venv\Scripts\python.exe" run.py %RUN_ARGS%
if errorlevel 1 pause
