@echo off
rem recall_run.bat - run ONE sync then exit.
rem Prefers dist\recall.exe (no Python needed on this machine); falls back to Python.
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
echo [recall] running one sync (python)...
%PYEXE% -m recall watch --once
echo.
echo [recall] done. Index + logs at %%LOCALAPPDATA%%\recall-memory\
pause
exit /b 0

:exe
echo [recall] running one sync...
"dist\recall.exe" watch --once
echo.
echo [recall] done. Index + logs at %%LOCALAPPDATA%%\recall-memory\
pause