#Requires -Version 5.1
<#
    Put a copy of the app on the Desktop and re-point autostart at it.

      * stops running instances (so the old file is not locked)
      * copies the exe to %USERPROFILE%\Desktop
      * re-points the HKCU Run autostart value to the Desktop copy
      * starts the background watcher from the Desktop copy
      * verifies
#>
[CmdletBinding()]
param(
    [string]$SourceExe = (Join-Path $env:LOCALAPPDATA 'Programs\NostationAutoSync\Nostation自动同步伴侣.exe'),
    [string]$Desktop = [Environment]::GetFolderPath('Desktop')
)

$ErrorActionPreference = 'Stop'
$exeName = 'Nostation自动同步伴侣.exe'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runValue = 'NostationAutoSync'

if (-not (Test-Path $SourceExe)) { throw "source not found: $SourceExe" }
$targetExe = Join-Path $Desktop $exeName

Write-Host '1) stopping running instances...' -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name like 'Nostation%'" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

Write-Host '2) copying to Desktop...' -ForegroundColor Cyan
Copy-Item $SourceExe $targetExe -Force
Write-Host ("   {0}  ({1:N1} MB)" -f $targetExe, ((Get-Item $targetExe).Length / 1MB)) -ForegroundColor Green

Write-Host '3) re-pointing autostart to the Desktop copy...' -ForegroundColor Cyan
$cmd = '"{0}" --watch' -f $targetExe
New-ItemProperty -Path $runKey -Name $runValue -Value $cmd -PropertyType String -Force | Out-Null
$check = (Get-ItemProperty -Path $runKey -Name $runValue).$runValue
if ($check -ne $cmd) { throw "registry update failed (got: $check)" }
Write-Host "   $check" -ForegroundColor Green

Write-Host '4) starting background watcher from Desktop copy...' -ForegroundColor Cyan
Start-Process -FilePath $targetExe -ArgumentList '--watch' -WindowStyle Hidden
Start-Sleep -Seconds 6

Write-Host ''
Write-Host 'status:' -ForegroundColor Cyan
$statusOut = Join-Path $env:TEMP 'nostation_desktop_status.txt'
& $targetExe --status --out $statusOut | Out-Null
if (Test-Path $statusOut) { Get-Content $statusOut -Encoding UTF8 | Write-Host; Remove-Item $statusOut -Force }

Write-Host ''
Write-Host 'done. The app is on your Desktop:' -ForegroundColor Green
Write-Host "   $targetExe"
