@echo off
rem install_task.bat - register the Windows Scheduled Task for the Recall autopilot.
rem 任务从本地运行位执行（%LOCALAPPDATA%\AgentMemorySystem\Run\recall\recall.exe）。
rem 已按 G-B9/T07 写入"电源安全"设置：插电池也启动、拔电池不停止、错过补跑、不唤醒。
rem 请先运行 build_exe.bat 完成打包与部署。
rem 注意：不使用 schtasks.exe，改用 PowerShell 的 Register-ScheduledTask。
setlocal
cd /d "%~dp0"
set "RUNDIR=%LOCALAPPDATA%\AgentMemorySystem\Run\recall"
set "TARGET=%RUNDIR%\recall.exe"
if not exist "%TARGET%" (
  echo [recall] ERROR: %TARGET% 不存在，请先运行 build_exe.bat
  pause
  exit /b 1
)
echo [recall] registering scheduled task RecallMemorySync
echo           target: %TARGET%
echo           every 3 hours (power-safe: runs on battery, backfills missed runs)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$t='%TARGET%';" ^
  "$a=New-ScheduledTaskAction -Execute $t -Argument 'autopilot --max-tasks 4';" ^
  "$tr=New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 3);" ^
  "$s=New-ScheduledTaskSettingsSet;" ^
  "$s.DisallowStartIfOnBatteries=$false;" ^
  "$s.StopIfGoingOnBatteries=$false;" ^
  "$s.StartWhenAvailable=$true;" ^
  "$s.WakeToRun=$false;" ^
  "Register-ScheduledTask -TaskName 'RecallMemorySync' -Action $a -Trigger $tr -Settings $s -Force | Out-Null;" ^
  "if ($?) { Start-ScheduledTask -TaskName 'RecallMemorySync'; Write-Host '[recall] registered + triggered' } else { Write-Host '[recall] ERROR: registration failed'; exit 1 }"
if %errorlevel%==0 (
  echo [recall] done. view log: "%TARGET%" logs
) else (
  echo [recall] ERROR: registration failed (may need admin)
)
pause
