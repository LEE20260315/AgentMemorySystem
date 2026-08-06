@echo off
REM AgentMemorySync 看门狗启动器（v2.1.0）
REM 用法：双击或加入启动项。负责监控主程序崩溃后自动重启。
cd /d "%~dp0"
setlocal

REM 找 pythonw
set PYW=
for /f "delims=" %%i in ('where pythonw 2^>nul') do (
    if not defined PYW set "PYW=%%i"
)
if not defined PYW (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        if not defined PYW (
            set "PYW=%%~dpipythonw.exe"
            if not exist "!PYW!" set "PYW="
        )
    )
)
if not defined PYW (
    echo [AgentMemorySync-Watchdog] pythonw not found, skip.
    exit /b 1
)

start "" "%PYW%" "%~dp0watchdog.py"
exit /b 0
