@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

:: 尝试用 pythonw（无黑窗口）启动，失败则用 python
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw memory_sync_app.py
) else (
    start "" python memory_sync_app.py
)
