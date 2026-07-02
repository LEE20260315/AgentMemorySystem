@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "REPO_DIR=%~dp0"
if "%REPO_DIR:~-1%"=="\" set "REPO_DIR=%REPO_DIR:~0,-1%"
set "CURRENT_FILE=%REPO_DIR%\AgentMemorySync.current.txt"
set "SOURCE_NAME=AgentMemorySync"
if exist "%CURRENT_FILE%" set /p SOURCE_NAME=<"%CURRENT_FILE%"
if not defined SOURCE_NAME set "SOURCE_NAME=AgentMemorySync"
set "SOURCE_DIR=%REPO_DIR%\%SOURCE_NAME%"
set "LOCAL_BASE=%TEMP%\AgentMemorySync_Run"
set "LOCAL_DIR=%LOCAL_BASE"
set "LOCAL_EXE=%LOCAL_DIR%\AgentMemorySync.exe"

if exist "%REPO_DIR%\AgentMemory" (
  set "AGENT_MEMORY_DATA_DIR=%REPO_DIR%\AgentMemory"
) else if exist "%REPO_DIR%\data" (
  set "AGENT_MEMORY_DATA_DIR=%REPO_DIR%\data"
) else (
  set "AGENT_MEMORY_DATA_DIR=%REPO_DIR%\AgentMemory"
)

if not exist "%SOURCE_DIR%\AgentMemorySync.exe" (
  echo [AgentMemorySync] OneDrive package not found: %SOURCE_DIR%
  echo Please run "python build.py" once on any device to generate the package.
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
  set "TARGET_DIR=%LOCAL_BASE%"
  set "COPY_RC=0"
  if exist "!TARGET_DIR!" rmdir /s /q "!TARGET_DIR!" >nul 2>nul
  if exist "!TARGET_DIR!" (
    set "TARGET_DIR=%TEMP%\AgentMemorySync_Run_%RANDOM%_%RANDOM%"
    echo [AgentMemorySync] Primary runtime dir is busy, using fallback: !TARGET_DIR!
  )
  robocopy "%SOURCE_DIR%" "!TARGET_DIR!" /MIR >nul
  set "COPY_RC=!ERRORLEVEL!"
  if not "!COPY_RC!"=="0" if not "!COPY_RC!"=="1" if not "!COPY_RC!"=="2" if not "!COPY_RC!"=="3" if not "!COPY_RC!"=="4" if not "!COPY_RC!"=="5" if not "!COPY_RC!"=="6" if not "!COPY_RC!"=="7" (
    set "TARGET_DIR=%TEMP%\AgentMemorySync_Run_%RANDOM%_%RANDOM%"
    echo [AgentMemorySync] Primary copy failed, retrying with fallback: !TARGET_DIR!
    robocopy "%SOURCE_DIR%" "!TARGET_DIR!" /MIR >nul
    set "COPY_RC=!ERRORLEVEL!"
  )
  if not "!COPY_RC!"=="0" if not "!COPY_RC!"=="1" if not "!COPY_RC!"=="2" if not "!COPY_RC!"=="3" if not "!COPY_RC!"=="4" if not "!COPY_RC!"=="5" if not "!COPY_RC!"=="6" if not "!COPY_RC!"=="7" (
    echo [AgentMemorySync] Failed to copy. Please check directory permissions.
    exit /b 1
  )
  set "LOCAL_DIR=!TARGET_DIR!"
  set "LOCAL_EXE=!LOCAL_DIR!\AgentMemorySync.exe"
)

echo [AgentMemorySync] data=%AGENT_MEMORY_DATA_DIR%
echo [AgentMemorySync] source=%SOURCE_DIR%
echo [AgentMemorySync] exe=%LOCAL_EXE%
start "" /D "%LOCAL_DIR%" "%LOCAL_EXE%"
exit /b 0
