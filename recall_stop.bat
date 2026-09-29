@echo off
rem recall_stop.bat - pause the Recall autopilot and stop legacy daemons
echo [recall] creating autopilot stop flag...
set "RECALL_EXE=%LOCALAPPDATA%\AgentMemorySystem\Run\recall\recall.exe"
if exist "%RECALL_EXE%" "%RECALL_EXE%" autopilot --stop
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*recall*' } | ForEach-Object { Write-Host ('  kill PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"
echo [recall] autopilot paused; use recall.exe autopilot --resume to continue
timeout /t 3 >nul
