@echo off
setlocal
cd /d "%~dp0"
set "REPO_DIR=%~dp0"
if "%REPO_DIR:~-1%"=="\" set "REPO_DIR=%REPO_DIR:~0,-1%"
set "SOURCE_DIR=%REPO_DIR%\AgentMemorySync"
set "LOCAL_DIR=%TEMP%\AgentMemorySync_Run"
set "LOCAL_EXE=%LOCAL_DIR%\AgentMemorySync.exe"
set "AGENT_MEMORY_DATA_DIR=%REPO_DIR%\data"

if not exist "%SOURCE_DIR%\AgentMemorySync.exe" (
  echo [AgentMemorySync] OneDrive package not found: %SOURCE_DIR%
  echo Please run "python build.py" once on any device to generate the package.
  pause
  exit /b 1
)

set "NEED_COPY=0"
if not exist "%LOCAL_EXE%" set "NEED_COPY=1"
if exist "%SOURCE_DIR%\AgentMemorySync.exe" if exist "%LOCAL_EXE%" (
  for %%I in ("%SOURCE_DIR%\AgentMemorySync.exe") do set "SRC_TIME=%%~tI"
  for %%I in ("%LOCAL_EXE%") do set "LOCAL_TIME=%%~tI"
  if /I not "%SRC_TIME%"=="%LOCAL_TIME%" set "NEED_COPY=1"
)

if "%NEED_COPY%"=="1" (
  echo [AgentMemorySync] Synchronizing local runtime copy...
  if exist "%LOCAL_DIR%" rmdir /s /q "%LOCAL_DIR%"
  robocopy "%SOURCE_DIR%" "%LOCAL_DIR%" /MIR >nul
  if errorlevel 8 (
    echo [AgentMemorySync] Failed to copy. Please check directory permissions.
    pause
    exit /b 1
  )
)

echo [AgentMemorySync] data=%AGENT_MEMORY_DATA_DIR%
echo [AgentMemorySync] exe=%LOCAL_EXE%
start "" /D "%LOCAL_DIR%" "%LOCAL_EXE%"
