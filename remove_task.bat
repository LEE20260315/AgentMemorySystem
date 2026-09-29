@echo off
rem remove_task.bat - delete the auto-sync scheduled task
schtasks /delete /tn "RecallMemorySync" /f
if %errorlevel%==0 (
  echo [recall] scheduled task deleted
) else (
  echo [recall] task not found, or delete failed
)
timeout /t 3 >nul