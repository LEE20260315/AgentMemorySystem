@echo off
REM ===========================================================
REM AgentMemorySync launcher v1.3.2
REM - 比较 OneDrive 分发包与本地副本时间戳，决定是否同步
REM - robocopy /MIR 同步本地副本
REM - 设置 AGENT_MEMORY_DATA_DIR，指向融合层
REM   优先级: <REPO_DIR>\AgentMemory\ \> <REPO_DIR>\data\  (兼容 v1.3.x 时代)
REM ===========================================================
setlocal
cd /d "%~dp0"

set "REPO_DIR=%~dp0"
if "%REPO_DIR:~-1%"=="\" set "REPO_DIR=%REPO_DIR:~0,-1%"

set "SOURCE_DIR=%REPO_DIR%\AgentMemorySync"
set "LOCAL_DIR=%TEMP%\AgentMemorySync_Run"
set "LOCAL_EXE=%LOCAL_DIR%\AgentMemorySync.exe"

REM v1.3.2 起，默认数据根改为 OneDrive/AgentMemory（跨设备共享真相源）
REM 若不存在则回退到项目内 data\（兼容旧 v1.3.x 时代部署）
if exist "%REPO_DIR%\AgentMemory" (
  set "AGENT_MEMORY_DATA_DIR=%REPO_DIR%\AgentMemory"
) else if exist "%REPO_DIR%\data" (
  set "AGENT_MEMORY_DATA_DIR=%REPO_DIR%\data"
) else (
  REM 兜底：直接指向 OneDrive 根的 AgentMemory（首次部署）
  set "AGENT_MEMORY_DATA_DIR=%REPO_DIR%\AgentMemory"
)

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
