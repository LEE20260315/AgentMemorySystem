@echo off
rem recall_watch.bat - start the timed sync daemon (visible console).
rem Prefers dist\recall.exe (no Python needed); falls back to Python.
cd /d "%~dp0"
if exist "dist\recall.exe" goto :exe

set "PYEXE="
where py >nul 2>nul
if %errorlevel%==0 set "PYEXE=py -3"
if not defined PYEXE (
  where python >nul 2>nul
  if %errorlevel%==0 set "PYEXE=python"
)
if not defined PYEXE (
  echo [recall] ERROR: no dist\recall.exe and no Python 3.10+ found.
  pause
  exit /b 1
)
echo [recall] daemon started (close window to stop)
%PYEXE% -m recall watch
pause
exit /b 0

:exe
echo [recall] daemon started (close window to stop)
"dist\recall.exe" watch
pause