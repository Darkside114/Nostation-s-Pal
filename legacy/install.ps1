#Requires -Version 5.1
<#
    NOSTATION hub clock sync - installer

    Does three things:
      1. copies a minimal Python runtime + the hidapi module into
         %LOCALAPPDATA%\NostationSync  (no system-wide Python required);
      2. copies the sync scripts and the silent launcher;
      3. makes it start by itself:
           - tries a Task Scheduler task (needs an elevated shell), and
           - always adds a Startup-folder launcher as well, which works without
             administrator rights and covers the normal "I just logged in" case.

    Run:  powershell -ExecutionPolicy Bypass -File install.ps1
          (run it from an elevated shell if you also want the pre-logon task)
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'NostationSync'),
    # 留空则自动探测（见仓库根目录 resolve_python.ps1），也可显式传入
    [string]$PythonSource = '',
    [switch]$NoAutostart
)

$ErrorActionPreference = 'Stop'

$TaskName = 'Nostation hub clock sync'
$ShortcutName = 'Nostation hub clock sync.lnk'
$SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say([string]$msg, [string]$color = 'Gray') { Write-Host $msg -ForegroundColor $color }

Say "Installing to $InstallDir" 'Cyan'
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# ---------------------------------------------------------------- 1. runtime --
# If -PythonSource is not given, auto-detect a usable Python 3 (reusing the
# repository-level resolve_python.ps1). NOTE: this install.ps1 belongs to the
# legacy v1.1.x script-based installer; since v1.2.0 the app is a single exe and
# does not need it. Kept for reference only.
if (-not $PythonSource) {
    $resolver = Join-Path (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)) 'resolve_python.ps1'
    if (Test-Path -LiteralPath $resolver) {
        . $resolver
        $pyExe = Resolve-PythonInterpreter
        $PythonSource = Split-Path -Parent $pyExe
        Say "  detected Python: $PythonSource"
    }
}
if (-not $PythonSource -or -not (Test-Path (Join-Path $PythonSource 'python.exe'))) {
    throw "Python runtime not found. Pass -PythonSource <dir> (the dir must contain python.exe)."
}
$pyDest = Join-Path $InstallDir 'python'
if (-not (Test-Path (Join-Path $pyDest 'python.exe'))) {
    Say '  copying Python runtime...'
    New-Item -ItemType Directory -Force -Path $pyDest, (Join-Path $pyDest 'Lib') | Out-Null
    foreach ($f in 'python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll', 'vcruntime140.dll', 'vcruntime140_1.dll') {
        $src = Join-Path $PythonSource $f
        if (Test-Path $src) { Copy-Item $src (Join-Path $pyDest $f) -Force }
    }
    foreach ($d in 'DLLs') {
        $src = Join-Path $PythonSource $d
        if (Test-Path $src) { Copy-Item $src (Join-Path $pyDest $d) -Recurse -Force }
    }
    Get-ChildItem (Join-Path $PythonSource 'Lib') -Force | Where-Object { $_.Name -ne 'site-packages' } |
        ForEach-Object { Copy-Item $_.FullName (Join-Path $pyDest 'Lib') -Recurse -Force }
    $pySite = Join-Path $pyDest 'Lib\site-packages'
    New-Item -ItemType Directory -Force -Path $pySite | Out-Null
    Get-ChildItem (Join-Path $PythonSource 'Lib\site-packages') -Force |
        Where-Object { $_.Name -match '^(hid|hidapi)' } |
        ForEach-Object { Copy-Item $_.FullName $pySite -Recurse -Force }
    Say '  runtime copied'
} else {
    Say '  Python runtime already present, skipping copy'
}
$pythonw = Join-Path $pyDest 'pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "pythonw.exe missing in $pyDest" }

# ---------------------------------------------------------------- 2. scripts --
Say '  copying scripts'
foreach ($f in 'nostation_sync.py', 'nostation_watcher.py', 'start-watcher.vbs', 'install.ps1', 'uninstall.ps1',
               'amk_probe.py', 'vial_definition.py') {
    $src = Join-Path $SourceDir $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $InstallDir $f) -Force }
}
Remove-Item (Join-Path $InstallDir 'sync.log'), (Join-Path $InstallDir 'watcher.log') -ErrorAction SilentlyContinue

Say '  smoke test:'
& $pythonw (Join-Path $InstallDir 'nostation_sync.py') --verbose
$smoke = $LASTEXITCODE
Say ("  exit code {0} (0 = hub synced, 3 = hub not connected)" -f $smoke) `
    $(if ($smoke -eq 0) { 'Green' } else { 'Yellow' })

# --------------------------------------------------------------- 3. autostart -
$vbs = Join-Path $InstallDir 'start-watcher.vbs'
if ($NoAutostart) {
    Say 'Skipping autostart (-NoAutostart).'
} else {
    $taskOk = $false
    try {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        $action = New-ScheduledTaskAction -Execute $pythonw `
            -Argument ('"{0}" --interval 3' -f (Join-Path $InstallDir 'nostation_watcher.py')) `
            -WorkingDirectory $InstallDir
        $triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
        $triggerLogon.Delay = 'PT10S'
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType Interactive -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -Hidden
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggerLogon `
            -Principal $principal -Settings $settings -ErrorAction Stop 2>$null `
            -Description 'Syncs the Matrix Lab NOSTATION hub clock at logon and whenever it is reconnected.' | Out-Null
        $taskOk = $true
        Say '  scheduled task registered' 'Green'
    } catch {
        Say '  scheduled task not registered (needs an elevated shell) - using the Startup folder instead' 'Yellow'
    }

    # Startup folder entry: works with normal user rights, and is the fallback
    # (and belt-and-braces) path. The watcher has a single-instance lock.
    try {
        $startup = [Environment]::GetFolderPath('Startup')
        New-Item -ItemType Directory -Force -Path $startup | Out-Null
        $lnk = Join-Path $startup $ShortcutName
        if (Test-Path $lnk) { Remove-Item $lnk -Force }
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($lnk)
        $sc.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
        $sc.Arguments = '"' + $vbs + '"'
        $sc.WorkingDirectory = $InstallDir
        $sc.WindowStyle = 7
        $sc.Description = 'Syncs the Matrix Lab NOSTATION hub clock on logon and reconnect'
        $sc.Save()
        Say "  Startup launcher created: $lnk" 'Green'
    } catch {
        Say "  could not create the Startup launcher: $_" 'Red'
    }

    if (-not $taskOk) {
        Say ''
        Say '  NOTE: for syncing before you log in, re-run this installer from an'
        Say '        elevated PowerShell (Run as administrator).'
    }
}

# --------------------------------------------------------------- 4. verify ---
Say ''
Say 'Verification:' 'Cyan'
$tasks = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($tasks) {
    $tasks | Select-Object TaskName, State | Format-Table -AutoSize | Out-String | Write-Host
} else {
    Write-Host 'No scheduled task (Startup folder method in use).'
}
Write-Host ('Current uptime: {0:N1} hours' -f ((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalHours)
Write-Host 'Log file:      ' -NoNewline
Say (Join-Path $InstallDir 'logs\nostation-sync.log') 'White'
Write-Host 'Start now:     ' -NoNewline
Say ('wscript.exe "{0}"' -f $vbs) 'White'
Write-Host 'Remove:        ' -NoNewline
Say ('powershell -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $InstallDir 'uninstall.ps1')) 'White'
Say ''
Say 'Done. The hub clock is set at logon and every time the hub is reconnected.' 'Green'
