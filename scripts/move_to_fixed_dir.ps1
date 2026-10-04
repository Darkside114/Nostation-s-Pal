#Requires -Version 5.1
<#
    把 Nostation自动同步伴侣.exe 迁移到固定目录，并把开机自启指向新位置。

    - 目标目录: %LOCALAPPDATA%\Programs\NostationAutoSync\
    - 同时更新「启动」文件夹快捷方式与计划任务
    - 顺带清理早期"脚本版"安装残留（python 运行时等）
#>
[CmdletBinding()]
param(
    [string]$SourceExe = 'C:\Users\Darkside\Documents\deepseek-harness\default-workspace\nostation-sync\dist\Nostation自动同步伴侣.exe',
    [string]$TargetDir = (Join-Path $env:LOCALAPPDATA 'Programs\NostationAutoSync'),
    [switch]$KeepLegacy
)

$ErrorActionPreference = 'Stop'
$exeName = 'Nostation自动同步伴侣.exe'
$taskName = 'Nostation hub clock sync'
$shortcutName = 'Nostation hub clock sync.lnk'
$legacyDir = Join-Path $env:LOCALAPPDATA 'NostationSync'

if (-not (Test-Path $SourceExe)) { throw "找不到源文件: $SourceExe" }

Write-Host "目标目录: $TargetDir" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

# 1) 停掉所有在跑的实例（否则文件被占用）
Write-Host '停止正在运行的后台同步...'
Get-CimInstance Win32_Process -Filter "Name like 'Nostation%'" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("  停止 pid {0}" -f $_.ProcessId)
    }
Start-Sleep -Seconds 2

# 2) 拷贝 exe
$targetExe = Join-Path $TargetDir $exeName
Copy-Item $SourceExe $targetExe -Force
Write-Host ("已拷贝: {0}  ({1:N1} MB)" -f $targetExe, ((Get-Item $targetExe).Length / 1MB)) -ForegroundColor Green

# 3) 更新「启动」文件夹快捷方式
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup $shortcutName
if (Test-Path $lnk) { Remove-Item $lnk -Force }
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $targetExe
$sc.Arguments = '--watch'
$sc.WorkingDirectory = $TargetDir
$sc.WindowStyle = 7
$sc.Description = 'Nostation hub 开机自动校时（By Darkside）'
$sc.Save()
Write-Host ("启动项已更新: {0}" -f $lnk) -ForegroundColor Green

# 4) 更新计划任务（导出 XML -> 改路径 -> 重新导入）
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    $xmlPath = Join-Path $env:TEMP 'nostation_task_new.xml'
    $oldXmlPath = Join-Path $env:TEMP 'nostation_task_old.xml'
    try {
        Export-ScheduledTask -TaskName $taskName | Set-Content -Path $oldXmlPath -Encoding Unicode
        $xml = Get-Content $oldXmlPath -Raw -Encoding Unicode
        $oldExe = ($task.Actions | Select-Object -First 1).Execute
        $oldCwd = ($task.Actions | Select-Object -First 1).WorkingDirectory
        $xml = $xml.Replace($oldExe, $targetExe)
        if ($oldCwd) { $xml = $xml.Replace($oldCwd, $TargetDir) }
        # 有些机器上 WorkingDirectory 是 exe 所在目录
        $xml = $xml.Replace([System.IO.Path]::GetDirectoryName($oldExe), $TargetDir)
        Set-Content -Path $xmlPath -Value $xml -Encoding Unicode
        schtasks /Create /TN $taskName /XML $xmlPath /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "schtasks 返回 $LASTEXITCODE" }
        $now = (Get-ScheduledTask -TaskName $taskName).Actions | Select-Object -First 1
        Write-Host ("计划任务已更新: {0} {1}" -f $now.Execute, $now.Arguments) -ForegroundColor Green
    } catch {
        Write-Host "计划任务更新失败（可在新版界面里重新点一次「开启」）: $_" -ForegroundColor Yellow
    }
} else {
    Write-Host '没有计划任务，仅使用登录启动项。'
}

# 5) 启动后台同步
Write-Host '启动后台同步...'
Start-Process -FilePath $targetExe -ArgumentList '--watch' -WindowStyle Hidden
Start-Sleep -Seconds 6

# 6) 自检
$statusOut = Join-Path $TargetDir 'selfcheck.txt'
& $targetExe --status --out $statusOut | Out-Null
Write-Host ''
Write-Host '自检:'
if (Test-Path $statusOut) { Get-Content $statusOut -Encoding UTF8 | Write-Host }

# 7) 清理早期脚本版残留（保留日志与配置）
if (-not $KeepLegacy -and (Test-Path $legacyDir)) {
    Write-Host ''
    Write-Host '清理早期脚本版残留...' -ForegroundColor Cyan
    foreach ($f in 'python', '__pycache__', 'amk_probe.py', 'install.ps1',
                   'nostation_sync.py', 'nostation_watcher.py', 'uninstall.ps1',
                   'vial_definition.py', 'start-watcher.vbs') {
        $p = Join-Path $legacyDir $f
        if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue; Write-Host "  已删除 $f" }
    }
    Write-Host ("  保留: config.json / nostation-sync.log / logs（历史记录）")
}

Write-Host ''
Write-Host '完成。双击下面的 exe 即可打开界面：' -ForegroundColor Green
Write-Host "  $targetExe"
