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
. <git-env.ps1>
cd <repo>
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
