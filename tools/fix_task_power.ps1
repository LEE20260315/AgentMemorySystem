<#
.SYNOPSIS
    修正 RecallMemorySync 计划任务的电源门控（G-B9 / T07）。

.DESCRIPTION
    旧任务的 XML 带 <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    与 <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>，导致电池供电 / 休眠时
    任务被压制而漏跑；同时因为 StartWhenAvailable=true，"未按时启动"不算 missed，
    所以 NumberOfMissedRuns 仍报 0 —— 从任务状态完全看不出漏跑。

    本脚本用 Set-ScheduledTask 就地改写四项设置（不删不重建任务）：
        DisallowStartIfOnBatteries = $false   # 插电池也启动
        StopIfGoingOnBatteries     = $false   # 拔电池不停止
        StartWhenAvailable         = $true    # 错过的时间点到来后补跑
        WakeToRun                  = $false    # 不唤醒睡眠中的机器
    脚本幂等、可反复执行；改前先把当前 XML 备份到 %TEMP%\recall-task-backup\，
    改后读回 XML 并打印关键设置做自证。

.NOTES
    不使用 schtasks.exe（本环境将其列入黑名单）；仅用 ScheduledTasks 模块。
    若权限不足，脚本会明确报错并以非零码退出，不会留下半改状态。
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'RecallMemorySync'
)

$ErrorActionPreference = 'Stop'

function Get-KeySettings {
    param([Parameter(Mandatory = $true)] $Task)
    $s = $Task.Settings
    [pscustomobject]@{
        DisallowStartIfOnBatteries = [bool]$s.DisallowStartIfOnBatteries
        StopIfGoingOnBatteries     = [bool]$s.StopIfGoingOnBatteries
        StartWhenAvailable         = [bool]$s.StartWhenAvailable
        WakeToRun                  = [bool]$s.WakeToRun
    }
}

function Show-Settings {
    param([string]$Label, [Parameter(Mandatory = $true)] $Task)
    $k = Get-KeySettings -Task $Task
    Write-Host ("[{0}] DisallowStartIfOnBatteries={1}  StopIfGoingOnBatteries={2}  StartWhenAvailable={3}  WakeToRun={4}" -f `
            $Label, $k.DisallowStartIfOnBatteries, $k.StopIfGoingOnBatteries, $k.StartWhenAvailable, $k.WakeToRun)
}

# --- 0) 定位任务 ---
try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
}
catch {
    Write-Error "[recall] 找不到计划任务 '$TaskName'：$($_.Exception.Message)"
    exit 1
}

# --- 1) 改前备份当前 XML（回滚用）---
$backupDir = Join-Path $env:TEMP 'recall-task-backup'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupXml = Join-Path $backupDir ("{0}-{1}.xml" -f $TaskName, $stamp)
(Export-ScheduledTask -TaskName $TaskName) -split "`r?`n" | Set-Content -Path $backupXml -Encoding UTF8
Write-Host "[recall] 已备份当前任务 XML -> $backupXml"

Show-Settings -Label '改动前' -Task $task

# --- 2) 就地改写四项设置 ---
$settings = $task.Settings
$settings.DisallowStartIfOnBatteries = $false
$settings.StopIfGoingOnBatteries     = $false
$settings.StartWhenAvailable         = $true
$settings.WakeToRun                  = $false

try {
    Set-ScheduledTask -TaskName $TaskName -Settings $settings | Out-Null
}
catch {
    Write-Error "[recall] Set-ScheduledTask 失败（可能需管理员权限）：$($_.Exception.Message)；备份在 $backupXml"
    exit 2
}

# --- 3) 读回并自证 ---
$after = Get-ScheduledTask -TaskName $TaskName
Show-Settings -Label '改动后' -Task $after

Write-Host ""
Write-Host "[recall] 读回 XML 复核（关键行）："
(Export-ScheduledTask -TaskName $TaskName) -split "`r?`n" |
    Select-String -Pattern 'Batteries|StartWhenAvailable|WakeToRun' |
    ForEach-Object { Write-Host ('   ' + $_.Line.Trim()) }

$k = Get-KeySettings -Task $after
$ok = (-not $k.DisallowStartIfOnBatteries) -and
      (-not $k.StopIfGoingOnBatteries) -and
      ($k.StartWhenAvailable) -and
      (-not $k.WakeToRun)

Write-Host ""
if ($ok) {
    Write-Host "[recall] OK：电源门控已修正（插电池也启动、拔电池不停止、错过补跑、不唤醒）。"
    Write-Host "[recall] 回滚命令：Register-ScheduledTask -Xml (Get-Content '$backupXml' -Raw) -TaskName '$TaskName' -Force"
    exit 0
}
else {
    Write-Error "[recall] 校验失败：设置未按预期生效。备份在 $backupXml"
    exit 3
}
