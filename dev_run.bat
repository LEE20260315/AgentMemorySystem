@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Find python first, derive pythonw from same directory
set PY=
set PYW=

for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY (
        set "PY=%%i"
        set "PYW=%%~dpipythonw.exe"
        if not exist "!PYW!" set "PYW="
    )
)

REM Fallback: try where pythonw directly
if "%PYW%"=="" (
    for /f "delims=" %%i in ('where pythonw 2^>nul') do (
        if not defined PYW set "PYW=%%i"
    )
)

REM If still no pythonw, use python
if "%PYW%"=="" set "PYW=%PY%"

REM If no python at all, error out
if "%PY%"=="" (
    echo Python not found. Please install Python 3.8+
    pause
    exit /b 1
)

REM Check and install dependencies using python (not pythonw, pip needs console)
%PY% -c "import pystray, PIL" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing dependencies...
    %PY% -m pip install pystray Pillow --quiet --disable-pip-version-check
)

REM Launch GUI with pythonw (no console window)
start "" %PYW% memory_sync_app.py
