@echo off
rem ⚠️ 切勿在此加入 `chcp 65001`：cmd.exe 在批处理执行中途切换代码页会按字节
rem 偏移重新定位本文件，遇到下面的中文字符即错位解析，把后续行拆成乱码命令
rem （实测特征：'not' is not recognized / '执行；OneDrive' is not recognized）。
rem 保持控制台原有代码页即可；中文提示可能显示为乱码，但脚本能正确执行。
rem ============================================================
rem  build_exe.bat — 打包成免装 Python 的可执行程序（onedir）
rem  产物：dist\recall\recall.exe + _internal\
rem  并自动部署到本地运行位 %LOCALAPPDATA%\AgentMemorySystem\Run\recall
rem  （计划任务 RecallMemorySync 从这里执行；OneDrive 上的 dist 目录在本机受限，
rem   以此处的本地拷贝为准）
rem  依赖：python -m pip install pyinstaller
rem ============================================================
cd /d "%~dp0"

set "PYEXE="
where py >nul 2>nul
if %errorlevel%==0 set "PYEXE=py -3"
if not defined PYEXE (
  where python >nul 2>nul
  if %errorlevel%==0 set "PYEXE=python"
)
if not defined PYEXE (
  echo [错误] 未找到 Python 3.10+
  pause
  exit /b 1
)

echo [recall] 检查 PyInstaller...
%PYEXE% -m PyInstaller --version >nul 2>nul
if not %errorlevel%==0 (
  echo [recall] 未安装，正在安装 PyInstaller...
  %PYEXE% -m pip install pyinstaller
)

echo [recall] 开始打包（onedir，按 recall.spec）...
rem OneDrive keeps short-lived handles on dist\, which makes PyInstaller fail with
rem WinError 5 while cleaning the output tree. Pre-clean with retries here.
if exist "dist\recall" (
  for /l %%i in (1,1,10) do (
    rmdir /s /q "dist\recall" 2>nul
    if not exist "dist\recall" goto :dist_cleaned
    ping -n 3 127.0.0.1 >nul
  )
  echo [warning] dist\recall still locked - continuing anyway
)
:dist_cleaned

%PYEXE% -m PyInstaller recall.spec --noconfirm

if not %errorlevel%==0 (
  echo [错误] 打包失败
  pause
  exit /b 1
)

rem 把可移植配置放到 exe 旁边（跨设备时编辑这个文件即可）
copy /y "recall\recall_config.json" "dist\recall\recall_config.json" >nul 2>nul

rem 部署到本地运行位（计划任务从这里执行）
set "RUNDIR=%LOCALAPPDATA%\AgentMemorySystem\Run\recall"
if not exist "%RUNDIR%" mkdir "%RUNDIR%"
xcopy /y /e /i "dist\recall\*" "%RUNDIR%\" >nul
if %errorlevel%==0 (
  echo [recall] 已部署到 %RUNDIR%
) else (
  echo [警告] 部署到本地运行位失败，请手工复制 dist\recall 到 %RUNDIR%
)

echo.
echo [recall] 打包完成：dist\recall\recall.exe
echo         用法：recall.exe autopilot   /   recall.exe recall "关键词"
pause
rem Exiting with an explicit 0: the trailing pause returns 1 when no console
rem is attached, which makes automation misread a successful build as failure.
exit /b 0
