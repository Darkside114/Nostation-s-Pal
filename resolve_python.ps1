#Requires -Version 5.1
<#
    找出一个可用的 Python 3 解释器，供本目录下的脚本使用。

    为什么要探测而不用写死的路径：早期版本把作者本机的 Python 绝对路径
    写死在脚本里，导致两个问题
      1) 暴露了作者的 Windows 账户名（C:\Users\<账户名>\...）到公开仓库；
      2) 别人克隆下来根本没法构建。

    返回解释器的完整路径（字符串）。调用方式：

        . (Join-Path $PSScriptRoot 'resolve_python.ps1')
        $Python = Resolve-PythonInterpreter
        & $Python -c "import hid, PIL, PyInstaller"

    查找顺序（找到第一个真正可运行的 Python 3 即返回）：
      1. 环境变量 NOSTATION_PYTHON（自己指定，最高优先级）
      2. PATH 里的 python / python3（会跳过 Microsoft Store 的占位存根）
      3. py -3 启动器
      4. 注册表里登记的 Python 安装
      5. 常见安装位置（%LOCALAPPDATA%\Programs\Python 等）
      6. 运行时自带的 Python（例如某些开发/打包环境）

    可用环境变量 NOSTATION_PYTHON 覆盖；设置后仅尝试该路径。
#>

function Test-PythonInterpreter {
    <# 真的跑一下 --version，确认它是个能用的 Python 3 #>
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $false }

    # Microsoft Store 的 python.exe 是个占位存根（0 字节或转发到应用商店），
    # 直接排除，否则会误判。
    if ($Path -like '*\WindowsApps\python*.exe') { return $false }

    try {
        $out = & $Path --version 2>&1
    } catch {
        return $false
    }
    if ($LASTEXITCODE -ne 0) { return $false }
    return ($out -join ' ') -match 'Python\s+3\.'
}

function Resolve-PythonInterpreter {
    [CmdletBinding()]
    param(
        # 额外候选（例如"运行时自带"的解释器），按顺序排在最后尝试
        [string[]]$ExtraCandidates = @()
    )

    $tried = New-Object System.Collections.Generic.List[string]

    function Try-One([string]$p) {
        if (-not $p) { return $null }
        $tried.Add($p)
        if (Test-PythonInterpreter -Path $p) { return $p }
        return $null
    }

    # 1) 显式指定
    if ($env:NOSTATION_PYTHON) {
        $hit = Try-One $env:NOSTATION_PYTHON
        if ($hit) { return $hit }
        throw ("NOSTATION_PYTHON 指向的不是可用的 Python 3: {0}" -f $env:NOSTATION_PYTHON)
    }

    # 2) PATH
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue |
               Where-Object { $_.Source } | Select-Object -First 1
        if ($cmd) {
            $hit = Try-One $cmd.Source
            if ($hit) { return $hit }
        }
    }

    # 3) py 启动器：先问它默认的 python 路径
    $py = Get-Command py -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($py) {
        try {
            $p = (& $py.Source -3 -c "import sys;print(sys.executable)" 2>$null | Select-Object -First 1)
            $hit = Try-One ($p -as [string])
            if ($hit) { return $hit }
        } catch { }
    }

    # 4) 注册表登记
    $regRoots = @(
        'HKCU:\SOFTWARE\Python\PythonCore',
        'HKLM:\SOFTWARE\Python\PythonCore',
        'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore'
    )
    foreach ($root in $regRoots) {
        if (-not (Test-Path $root)) { continue }
        foreach ($ver in (Get-ChildItem $root -ErrorAction SilentlyContinue)) {
            $ip = Join-Path $ver.PSPath 'InstallPath'
            if (-not (Test-Path $ip)) { continue }
            $dir = (Get-ItemProperty -Path $ip -ErrorAction SilentlyContinue).'(default)'
            if ($dir) {
                $hit = Try-One (Join-Path $dir 'python.exe')
                if ($hit) { return $hit }
            }
        }
    }

    # 5) 常见安装位置
    $bases = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)},
        'C:\'
    ) | Where-Object { $_ }
    foreach ($base in $bases) {
        if (-not (Test-Path $base)) { continue }
        $dirs = Get-ChildItem $base -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like 'Python3*' } |
                Sort-Object Name -Descending
        foreach ($d in $dirs) {
            $hit = Try-One (Join-Path $d.FullName 'python.exe')
            if ($hit) { return $hit }
        }
    }

    # 6) 额外候选
    foreach ($c in $ExtraCandidates) {
        $hit = Try-One $c
        if ($hit) { return $hit }
    }

    $msg = @(
        '找不到可用的 Python 3 解释器。',
        '',
        '请任选一种方式解决：',
        '  1) 安装 Python 3.10+：https://www.python.org/downloads/',
        '     （安装时勾选 "Add python.exe to PATH"）',
        '  2) 或者设置环境变量指向已有的 Python，然后重试：',
        '       $env:NOSTATION_PYTHON = "D:\Python312\python.exe"',
        '',
        '已尝试过的位置：'
    ) + ($tried | ForEach-Object { '  ' + $_ })
    throw ($msg -join [Environment]::NewLine)
}
