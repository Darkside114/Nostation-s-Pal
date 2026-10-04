#Requires -Version 5.1
<#
    Removes the NOSTATION hub clock sync: stops the watcher, deletes the
    scheduled task / Startup launcher and (unless -KeepFiles) the files.

    Run:  powershell -ExecutionPolicy Bypass -File uninstall.ps1 [-KeepFiles]
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'NostationSync'),
    [switch]$KeepFiles
)

$ErrorActionPreference = 'Continue'
$TaskName = 'Nostation hub clock sync'
$ShortcutName = 'Nostation hub clock sync.lnk'

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task: $TaskName" -ForegroundColor Green
} else {
    Write-Host 'No scheduled task to remove.'
}

$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup $ShortcutName
if (Test-Path $lnk) {
    Remove-Item $lnk -Force
    Write-Host "Removed Startup launcher: $lnk" -ForegroundColor Green
} else {
    Write-Host 'No Startup launcher to remove.'
}

Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*nostation_watcher.py*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("Stopped watcher pid {0}" -f $_.ProcessId)
    }

if (-not $KeepFiles) {
    if (Test-Path $InstallDir) {
        Remove-Item $InstallDir -Recurse -Force
        Write-Host "Deleted $InstallDir"
    }
} else {
    Write-Host "Kept $InstallDir (logs intact)"
}
