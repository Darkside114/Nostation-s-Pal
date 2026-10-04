﻿#Requires -Version 5.1
<#
    Make the app a true single-file portable tool:

      * copies the freshly built exe to a stable folder
      * registers autostart via HKCU\...\Run  (no shortcut file, no install)
      * removes the old Startup-folder shortcut
      * tries to remove the leftover elevated scheduled task (may need UAC)
      * starts the background watcher and prints a status report

    Result: one exe file. No install, no uninstaller, no service.
#>
[CmdletBinding()]
param(
    [string]$SourceExe = '<repo>\dist\Nostation自动同步伴侣.exe',
    [string]$TargetDir = (Join-Path $env:LOCALAPPDATA 'Programs\NostationAutoSync')
)

$ErrorActionPreference = 'Stop'
$exeName = 'Nostation自动同步伴侣.exe'
$taskName = 'Nostation hub clock sync'
$shortcutName = 'Nostation hub clock sync.lnk'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runValue = 'NostationAutoSync'

if (-not (Test-Path $SourceExe)) { throw "source exe not found: $SourceExe" }
New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

Write-Host '1) stopping running instances...' -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name like 'Nostation%'" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

Write-Host '2) copying exe...' -ForegroundColor Cyan
$targetExe = Join-Path $TargetDir $exeName
Copy-Item $SourceExe $targetExe -Force
Write-Host ("   {0}  ({1:N1} MB)" -f $targetExe, ((Get-Item $targetExe).Length / 1MB)) -ForegroundColor Green

Write-Host '3) registering autostart in HKCU Run (no extra files)...' -ForegroundColor Cyan
$cmd = '"{0}" --watch' -f $targetExe
New-ItemProperty -Path $runKey -Name $runValue -Value $cmd -PropertyType String -Force | Out-Null
$check = (Get-ItemProperty -Path $runKey -Name $runValue).$runValue
if ($check -ne $cmd) { throw "registry write failed (got: $check)" }
Write-Host "   $runValue = $check" -ForegroundColor Green

Write-Host '4) removing old Startup-folder shortcut...' -ForegroundColor Cyan
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) $shortcutName
if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host '   removed' -ForegroundColor Green }
else { Write-Host '   (none)' }

Write-Host '5) trying to remove leftover scheduled task...' -ForegroundColor Cyan
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    try {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
        Write-Host '   removed' -ForegroundColor Green
    } catch {
        Write-Host '   cannot remove without admin (open the app, click 停止同步, approve UAC)' -ForegroundColor Yellow
    }
} else { Write-Host '   (none)' }

Write-Host '6) starting background watcher...' -ForegroundColor Cyan
Start-Process -FilePath $targetExe -ArgumentList '--watch' -WindowStyle Hidden
Start-Sleep -Seconds 6

Write-Host ''
Write-Host 'status:' -ForegroundColor Cyan
$statusOut = Join-Path $TargetDir 'selfcheck.txt'
& $targetExe --status --out $statusOut | Out-Null
if (Test-Path $statusOut) { Get-Content $statusOut -Encoding UTF8 | Write-Host }
Remove-Item $statusOut -ErrorAction SilentlyContinue

Write-Host ''
Write-Host 'files that make up this tool:' -ForegroundColor Cyan
Write-Host "   $targetExe"
Write-Host ''
Write-Host 'done. Double-click the exe any time to open the UI.' -ForegroundColor Green
