#Requires -Version 5.1
<#
    Build companion_app.py into a single-file exe.

    Output:  <this folder>\dist\<product name>.exe   (Chinese name, see below)

    Notes:
      * --onefile   single file, can be copied anywhere
      * --windowed  no console window (also for the hidden --watch mode)
      * --icon      embedded icon (Explorer / taskbar)
      * --add-data  the same .ico also goes inside the exe, so the window icon
                    survives being moved to another folder or sent to someone
      * bundles tkinter + hidapi only; numpy/pandas/matplotlib are excluded

    THIS FILE MUST STAY ASCII-ONLY. Windows PowerShell 5.1 reads BOM-less .ps1
    files as ANSI(GBK), which corrupts non-ASCII literals -- and merely editing
    this file can drop its BOM. So the Chinese product name is NOT written here:
    it is read from generate_version_info.py at build time (-PrintName).

    Usage:  powershell -ExecutionPolicy Bypass -File build_exe.ps1
#>
[CmdletBinding()]
param(
    # Optional: path to python.exe. When omitted, resolve_python.ps1 probes for a
    # usable Python 3 (env var NOSTATION_PYTHON > PATH > py launcher > registry >
    # common install dirs). The old version of this script hard-coded the author's
    # local interpreter path, which leaked the Windows account name into a public
    # repo and made the script unusable on any other machine.
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---------------------------------------------------------------------------
# Guard: this file MUST stay ASCII-only.
# Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI and corrupts/non-parse any
# non-ASCII literal, so a Chinese comment here can break the whole build with a
# confusing parser error. Fail loudly instead.
# ---------------------------------------------------------------------------
$selfText = [System.IO.File]::ReadAllText($PSCommandPath)
$badChars = @($selfText.ToCharArray() | Where-Object { [int]$_ -gt 127 })
if ($badChars.Count -gt 0) {
    $sample = ($badChars | Select-Object -First 10) -join ' '
    throw ("build_exe.ps1 must stay ASCII-only, but found {0} non-ASCII chars: {1}" -f
        $badChars.Count, $sample)
}

if (-not $Python) {
    . (Join-Path $here 'resolve_python.ps1')
    # Fallback candidates: scan common layouts of "an interpreter bundled with a
    # development runtime". Why needed: the author's machine has no standalone
    # Python install, so the build uses the interpreter shipped with the dev
    # runtime. This globs for it instead of hard-coding one machine's path, so it
    # works on the author's box without leaking the account name publicly.
    $extra = @()
    foreach ($root in @(
            (Join-Path $env:LOCALAPPDATA '.dsh'),
            (Join-Path $env:USERPROFILE '.dsh'),
            (Join-Path $env:USERPROFILE 'scoop\apps'),
            (Join-Path $env:LOCALAPPDATA 'Programs'))) {
        if (Test-Path -LiteralPath $root) {
            $extra += @(Get-ChildItem -LiteralPath $root -Recurse -Filter 'python.exe' `
                    -ErrorAction SilentlyContinue -Depth 8 |
                    Select-Object -ExpandProperty FullName)
        }
    }
    $Python = Resolve-PythonInterpreter -ExtraCandidates $extra
    Write-Host "Python: $Python" -ForegroundColor Cyan
}
if (-not (Test-Path -LiteralPath $Python)) { throw "python not found: $Python" }
# PyInstaller mangles non-ASCII --name values (they get re-encoded to the ANSI
# code page), so build under an ASCII name and rename afterwards.
$BuildName = 'NostationAutoSyncCompanion'
$excludes = @(
    # NOTE: do NOT exclude PIL itself. companion_app needs Pillow at runtime to
    # draw the crisp window/taskbar icon from the icon's largest frame; excluding
    # it made the app silently fall back to the raw .ico (blurry 16px frame).
    # But we only need PNG/ICO decoding, and Pillow's AVIF decoder alone is
    # ~7.5 MB, so drop the codecs we never touch.
    'PIL._avif', 'PIL._webp', 'PIL._imagingcms', 'PIL.ImageQt', 'PIL.ImageShow',
    'numpy', 'pandas', 'matplotlib', 'scipy', 'lxml', 'openpyxl',
    'pptx', 'docx', 'XlsxWriter', 'setuptools', 'pip', 'pytest', 'unittest',
    'pydoc', 'doctest', 'sqlite3'
)

Write-Host "Source dir: $here" -ForegroundColor Cyan

# Product file name (with .exe) comes from the Python side as UTF-8, so this
# script never contains non-ASCII characters itself.
$gen = Join-Path $here 'generate_version_info.py'
$Name = (& $Python $gen --print-name)
if (-not $Name) { throw 'could not read the product name from generate_version_info.py' }
# strip any extension the Python side returned, we always append .exe below
$Name = [System.IO.Path]::GetFileNameWithoutExtension($Name)
Write-Host "Product name: $Name"

if (-not (Test-Path (Join-Path $here 'assets/companion.ico'))) {
    Write-Host 'Generating fallback icon...'
    & $Python (Join-Path $here 'make_icon.py')
}
$iconFile = Join-Path $here 'assets/companion.ico'
if (-not (Test-Path $iconFile)) { throw 'companion.ico missing' }

Write-Host 'Generating version resource (copyright / author metadata)...'
& $Python (Join-Path $here 'generate_version_info.py')
if ($LASTEXITCODE -ne 0) { throw 'generate_version_info.py failed' }
$versionFile = Join-Path $here 'version_info.txt'
if (-not (Test-Path $versionFile)) { throw 'version_info.txt was not generated' }

Write-Host 'Generating splash image...'
& $Python (Join-Path $here 'make_splash.py')
if ($LASTEXITCODE -ne 0) { throw 'make_splash.py failed' }
$splashFile = Join-Path $here 'assets/splash.png'
if (-not (Test-Path $splashFile)) { throw 'splash.png was not generated' }

foreach ($d in 'build', 'dist', '__pycache__') {
    $p = Join-Path $here $d
    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
}

$args = @(
    '-m', 'PyInstaller',
    '--noconfirm', '--clean', '--onefile', '--windowed',
    '--name', $BuildName,
    '--icon', (Join-Path $here 'assets/companion.ico'),
    # The icon also goes INSIDE the exe: move the exe to another folder or send
    # it to someone else and the window/taskbar icon still works (read at
    # runtime from the onefile extraction dir).
    #
    # MUST be ONE token, and use ':' (not ';'): Windows PowerShell 5.1 strips
    # the quotes it adds when handing arguments to a native exe, so a separate
    # 'SOURCE','DEST' pair arrives as two tokens and PyInstaller rejects it.
    ("--add-data={0}{1}." -f $iconFile, [char]58),
    # Splash screen: the onefile bootloader shows this immediately, so the user
    # never sees the blank white window while the app is being unpacked.
    '--splash', $splashFile,
    '--version-file', $versionFile,
    '--workpath', (Join-Path $here 'build'),
    '--distpath', (Join-Path $here 'dist'),
    '--specpath', $here,
    '--collect-submodules', 'hid',
    '--hidden-import', 'hid',
    '--hidden-import', 'tkinter',
    '--hidden-import', 'tkinter.ttk',
    '--hidden-import', 'tkinter.messagebox',
    '--hidden-import', 'tkinter.scrolledtext'
)
foreach ($ex in $excludes) { $args += @('--exclude-module', $ex) }
$args += (Join-Path $here 'companion_app.py')

Write-Host 'Building (about 1-2 minutes)...' -ForegroundColor Cyan
# PyInstaller logs to stderr; with ErrorActionPreference=Stop that would abort
# the script, so relax it for the native call and check the exit code instead.
$ErrorActionPreference = 'Continue'
& $Python @args
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($code -ne 0) { throw "PyInstaller failed, exit code $code" }

$built = Get-ChildItem (Join-Path $here 'dist\*.exe') | Select-Object -First 1
if (-not $built) { throw 'No exe produced in dist\' }

# Rename to the Chinese product name (PowerShell handles this correctly).
$exe = Join-Path $here ("dist\{0}.exe" -f $Name)
if ($built.FullName -ne $exe) {
    if (Test-Path $exe) { Remove-Item $exe -Force }
    Rename-Item $built.FullName (Split-Path $exe -Leaf) -Force
}
$exe = Get-Item $exe
$sizeMb = [Math]::Round($exe.Length / 1MB, 1)

Write-Host ''
Write-Host ("Build OK: {0}" -f $exe.FullName) -ForegroundColor Green
Write-Host ("Size    : {0} MB" -f $sizeMb)

# Self check: the exe is a GUI-subsystem binary, so it cannot print to this
# console; --status writes its report to a file instead.
$statusOut = Join-Path $here 'dist\selfcheck.txt'
# --no-heal: this runs the throwaway build in dist\, so it must NOT let the
# "location self-heal" feature re-point the user's autostart entry here.
& $exe.FullName --status --no-heal --out $statusOut | Out-Null
Write-Host ''
Write-Host 'Self check (dist\selfcheck.txt):'
if (Test-Path $statusOut) {
    Get-Content $statusOut -Encoding UTF8 | Write-Host
} else {
    Write-Host '  FAILED: status file not written' -ForegroundColor Red
}

Write-Host ''
Write-Host 'Embedded file properties (right-click exe -> Properties -> Details):'
$vi = (Get-Item $exe.FullName).VersionInfo
foreach ($f in 'FileDescription', 'ProductName', 'ProductVersion', 'FileVersion',
               'CompanyName', 'LegalCopyright', 'LegalTrademarks', 'Comments') {
    Write-Host ("  {0,-18}: {1}" -f $f, $vi.$f)
}
