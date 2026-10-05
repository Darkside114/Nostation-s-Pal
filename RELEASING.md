# 发版流程

给自己看的备忘，避免每次发版都要重新回忆。发版前请通读一遍。

## 一、改代码 + 升版本号

版本号在 [`companion_app.py`](companion_app.py) 顶部，三处都要改：

```python
APP_VERSION = "1.8.2"      # 语义化版本，自动更新靠它比较
APP_BUILD = 182            # 与版本对应（1.8.2 -> 182）
CHANGELOG = [
    ("1.8.2", "本次改了什么"),   # 必须加一行，会显示在「版本历史」里
    ...
]
```

> `CHANGELOG` 会显示在软件内的「帮助 → 版本历史」窗口里，所以要写给人看，
> 不要写 commit 信息那种口气。

## 一之二、如果改动了许可条款

许可条款有**独立的版本号** LICENSE_VERSION（在 companion_app.py 顶部），
它决定"已同意的老用户是否会被要求重新确认"。

| 情况 | 要不要改 LICENSE_VERSION |
| --- | --- |
| 改动了 LICENSE_TERMS 的文字 | **要** —— 改成当天日期，如 "2026-10-05" |
| 只改软件功能、条款一字未动 | **不要** —— 否则用户每升一个小版本都要点一次同意 |

为什么用日期：早期版本误把软件版本号（如 1.8.1）写进了配置的
license_version，若再用点分编号会出现"1.1.0 与 1.8.1 谁更新"的歧义。
日期制不可能混淆。

改完之后建议手动验证两种身份：

1. 删掉 %LOCALAPPDATA%\NostationSync\config.json → 应弹出**首次**条款（无"已更新"字样）
2. 把配置里的 license_version 改成旧值 → 应弹出**「许可条款（已更新，请重新确认）」**

另外确认：license_ok() 只被 un_gui() 调用，后台进程（--watch）不检查许可，
所以条款更新后**即使还没点同意，开机校时也不会中断**。改这块时务必保持这个性质。

## 一之三、PowerShell 脚本的两条硬性规则

这个仓库反复被同一类问题坑过，改 .ps1 时请守住两条：

### 规则 1：含中文的 .ps1 **必须**有 UTF-8 BOM

Windows PowerShell 5.1 会把**没有 BOM** 的 .ps1 当成 ANSI(GBK) 读，
文件里的中文会变乱码，**并直接导致语法错误（报错信息还是乱码，极难排查）**。
用 PowerShell 写文件时要显式指定：

```powershell
[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($true)))
```

另外注意：**某些编辑器/工具保存时会丢掉 BOM**，改完请复查。

### 规则 2：uild_exe.ps1 必须是**纯ASCII**

它只含英文，所以不依赖 BOM。历史上唯一一次中文注释就是因此报错的。
现在脚本开头会**自检**：发现非 ASCII 字符就直接抛错并列出字符，
不会再让你对着乱码报错猜半天。

### 自查命令（提交前跑一遍）

```powershell
Get-ChildItem -Recurse -Filter *.ps1 | Where-Object { $_.FullName -notlike '*\.git\*' } | ForEach-Object {
    $b = [System.IO.File]::ReadAllBytes($_.FullName)
    $bom = ($b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)
    $t = [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)
    $n = ($t.ToCharArray() | Where-Object { [int]$_ -gt 127 }).Count
    '{0,-28} 非ASCII={1,-4} BOM={2}' -f $_.Name, $n, $bom
}
```

## 一之四、不要写死本机绝对路径

历史教训：早期脚本把作者本机的 Python 路径
（`C:\Users\<账户名>\...`）写死了，造成两个问题——
**泄露 Windows 账户名到公开仓库**，以及**别人克隆后无法构建**。

现在统一做法：

- Python 解释器由 [esolve_python.ps1](resolve_python.ps1) 自动探测
  （`NOSTATION_PYTHON` 环境变量 > PATH > `py` 启动器 > 注册表 > 常见安装目录），
  并会跳过 Microsoft Store 的 `python.exe` 占位存根
- 需要指定时用环境变量，而不是改脚本：
  ```powershell
  $env:NOSTATION_PYTHON = "D:\Python312\python.exe"
  ```
- 提交前自查有没有残留：
  ```powershell
  git grep -n -I -E 'C:\\\\Users\\\\[A-Za-z0-9_.-]+'
  ```

## 二、本地验证

```powershell
python -m py_compile companion_app.py
python tools\verify_icon.py                 # 图标没动就不用跑
```

## 三、打包

```powershell
.\build_exe.ps1
```

脚本会自动：生成版本资源 → 生成启动画面 → 打包单文件 exe → 自检（`--status --no-heal`）。
产物在 `dist\Nostation自动同步伴侣.exe`。

## 四、发布到 GitHub

在 `Releases` 里新建，**tag 用 `v` + 版本号**（如 `v1.8.2`），附件名用 `Nostation.exe`。

### Release 说明的写法

**只写「版本号 + 更新内容」就够了。** 不要加校验值表格、不要加 `Get-FileHash` 命令、
不要写安装步骤（README 里已有）。

好的示例：

```markdown
## 本次更新：修复 xxx

- 具体改了什么，为什么改
- 用户能感知到的变化

### 升级方式

打开程序即可自动收到更新提示；也可直接下载覆盖旧文件。
```

### 为什么说明里不需要写 SHA256

自动更新的完整性校验**不依赖 Release 说明里的哈希**，而是直接读 GitHub API 给
附件的 `digest` 字段（形如 `sha256:9d7d47...`）。所以：

- 说明可以保持干净，只讲更新内容
- 校验依然有效：`expected_sha()` 优先用 `asset_digest`，
  取不到才退回"从说明正文里找 64 位十六进制"（兼容早期版本）

> 踩过的坑：曾经为了把哈希写进说明，用了转义的多重反引号包代码块，
> 结果在 GitHub 上**渲染成裸文本**（表格里出现一长串字符加乱掉的反引号），
> 很丑。改用 API digest 后这个问题就不存在了。

### 附件名的限制

GitHub 的 Release 附件名**只支持 ASCII**（试过直接编码和双重编码两种写法，
后者更糟，会变成一串点）。所以附件固定叫 `Nostation.exe`，
在说明里提一句"下载后可改名为 `Nostation自动同步伴侣.exe`"即可。

## 五、提交代码

```powershell
# 先用你的方式让 git 可用（例如安装 Git for Windows，或确保 git 在 PATH 里）
cd <你克隆仓库的目录>
git add -A
git commit -m "feat(v1.8.2): ..."
git push
```

推送时会要求输入用户名（`Darkside114`）和一个具 `repo` 权限的 Personal Access Token。

## 六、确认自动更新认得新版本

发完 Release 后验证一下（应输出线上 tag 与新版本号一致）：

```powershell
python -c "import sys; sys.path.insert(0,'.'); import companion_app as ca; r=ca.fetch_latest_release(); print(r['tag'], ca.expected_sha(r))"
```

## 注意事项

- **不要提交构建产物**：`dist/`、`build/`、`*.spec`、`version_info.txt` 已在 `.gitignore` 里
- **不要提交 exe**：exe 走 Releases，不进 git 仓库
- `assets/` 下的图标、启动画面、界面截图是源文件，要提交
- 界面截图与 `CHANGELOG` 里的版本号会一起显示给用户，改了界面记得重新生成截图：
  `python tools\make_ui_screenshot.py assets\ui-preview.png`
- `splash.png` 由 `make_splash.py` 在打包时自动生成（会读 `APP_VERSION`），
  所以升版本后它会自动更新
