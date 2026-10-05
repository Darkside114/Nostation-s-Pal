#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nostation's Pal —— 带界面的单文件程序。

同一个 exe 有几种工作模式（由参数决定）:

    NostationsPal.exe               打开操作界面
    NostationsPal.exe --watch       后台监视进程（开机自启调用，无窗口）
    NostationsPal.exe --sync-now    立刻校时一次（无窗口）

界面上的两个按钮:
    「开启 Nostation 开机同步」 -> 写登录启动项 + 启动后台监视进程 + 立刻校时一次
                                   （可选：再注册计划任务，实现"开机未登录"也校时）
    「停止同步」                -> 移除自启动项、注销计划任务、结束后台监视进程

底层协议与 https://config.matrix-lab.com/ 网页版 Sync 按钮完全一致:
    32 字节 raw HID 报文  FD 37 <年高> <年低> 月 日 星期 时 分 秒
    接口 usage_page 0xFF60 / usage 0x61，设备回 FD 37 AA 表示写入成功。
"""

import argparse
import base64
import ctypes
import datetime
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback

# ---------------------------------------------------------------------------
# 软件标识 / 版权
# ---------------------------------------------------------------------------

APP_TITLE = "Nostation's Pal"
APP_NAME_EN = "Nostation's Pal"
# 产物文件名与内部标识**一律用 ASCII**。
#
# 为什么必须这样：程序名里一旦有非 ASCII 字符（比如中文），
# PowerShell 5.1 会按系统 ANSI 代码页（简体中文下是 GBK）解析命令行参数，
# 于是传给 PowerShell 的 exe 路径变成乱码
# （"Nostation自动同步伴侣.exe" -> "Nostation鑷姩鍚屾浠.exe"），
# 结果计划任务里存下乱码路径、自检永远报 stale、进程探测也匹配不上。
# 用纯 ASCII 名字可以从根上杜绝这一类问题。
APP_EXE_NAME = "NostationsPal"          # 不带扩展名，产物为 NostationsPal.exe
APP_MUTEX_NAME = "NostationPal"
APP_DATA_DIR = "NostationPal"           # %LOCALAPPDATA%\NostationPal
APP_VERSION = "2.0.0"
APP_BUILD = 200
# 版本历史（每次迭代都要改 APP_VERSION / APP_BUILD 并在这里记一行）
CHANGELOG = [
    ("2.0.0", "正式更名 Nostation's Pal：程序名、exe 文件名与内部标识全部改为 ASCII，从根上杜绝中文名导致的乱码（计划任务里出现乱码路径、自检误报失效）；新增按地址抓取真实温湿度并显示到 NOSTATION 屏幕"),
    ("1.12.0", "根治「Failed to remove temporary directory」警告框：后台同步进程现在会接收系统关机通知并优雅退出，不再被 Windows 强杀，临时目录因此能正常清理；启动时也会回收历史残留"),
    ("1.11.0", "处理「Failed to remove temporary directory」警告框：单文件程序"
               "非正常结束时会在系统临时目录留下约 30 MB 残留，现在启动时会自动"
               "回收（只清理确属本程序的），退出前也会先释放临时目录里的 Tcl/Tk "
               "数据，让 bootloader 更容易删干净、不再弹那个框"),
    ("1.10.1", "构建脚本改为自动探测 Python，别人克隆后可直接打包；"
               "exe 体积从 12.8 MB 精简到 11.1 MB：窗口图标改为构建时预生成"
               "（运行时不再需要 Pillow），并去掉 Tcl/Tk 里用不到的编码表与示例资源"),
    ("1.10.0", "许可条款改动后会重新弹出确认：条款有了独立的版本号，"
               "只要条款文字有变，已同意的老用户下次打开界面会读到新条款并再次确认"
               "（只升软件版本、不改条款时不会打扰）。后台同步不受影响，"
               "即使还没点同意，开机校时也照常进行"),
    ("1.9.0", "许可条款新增「第三方工具声明」：明确本软件是独立第三方工具，"
              "与 Matrix Lab 无任何隶属、合作、赞助、授权或背书关系，"
              "并说明相关商标归属；条款结尾加入「欢迎去项目主页点 Star」的引导"),
    ("1.8.2", "更新校验改用 GitHub 提供的附件摘要，不再依赖 Release 说明里的哈希"
              "（说明只保留版本号与更新内容，更好读）；新增 RELEASING.md 发版备忘"),
    ("1.8.1", "帮助页新增「自动更新」与「版本历史」说明，章节顺序与编号重排；"
              "修正帮助页里「不会访问网络」这句已过时的说法"),
    ("1.8.0", "新增自动更新：启动时自动向 GitHub 查询新版本，有更新会弹窗告知"
              "并可直接下载；下载后校验 SHA256，再自动替换旧 exe 并重新打开。"
              "菜单新增「检查更新」可手动触发；版本历史从「关于」独立成单独窗口"),
    ("1.7.0", "新增「怎么用」帮助页，逐个说明四个按钮、指示灯、状态区、日志含义与"
              "常见问题；「重新检查」会写明本次检查结果，不再点了没反应；"
              "按钮下方加引导提示，界面文案去掉对普通用户无意义的设备 ID 与术语"),
    ("1.6.0", "改为 MIT 许可证开源发布；许可条款与「关于」同步改写，"
              "exe 属性里的版权信息也改为 MIT License"),
    ("1.5.5", "「关于」与许可条款里写明：本软件免费但【不是开源软件】，"
              "并显示项目主页；修正图标尺寸预览图的排版错位"),
    ("1.5.4", "修复桌面上一直挂着一个 420×200 启动画面：PyInstaller 的闪屏在"
              "无界面模式（--watch 后台同步等）里也会创建，而之前只在界面模式关闭它。"
              "后台同步进程是开机自启、常驻的，所以那个闪屏会永久留在桌面"),
    ("1.5.3", "闪屏加保险机制：万一没被正常关闭，12 秒后强制关闭并记日志"),
    ("1.5.2", "修复启动时闪一下默认尺寸小窗口：界面先全部布局好、图标设好，再整体显示；"
              "并把 App 构造里同步执行的设备枚举挪到窗口显示之后"
              "（App(root) 构建 972ms → 16ms；启动到窗口可见 1.77s → 1.29s）"),
    ("1.5.1", "去掉重复的窗口图标设置；启动时状态栏先显示「正在检查设备与同步状态…」"),
    ("1.5.0", "修复启动慢与白框：新增启动闪屏（onefile 解压期间不再看到白框）；"
              "位置自愈等慢检查改为窗口显示后再做；hid.enumerate 结果加 2.5 秒缓存"
              "（本机单次要 1.1~1.5 秒）"),
    ("1.4.0", "打开界面时无条件清空日志（上一版在后台同步运行时不清，"
              "而它几乎一直在跑，等于没清）；新增「会话标记」防止另一进程把旧内容写回"),
    ("1.3.3", "修复：exe 里漏打包 Pillow，导致「用大帧画清晰窗口图标」这条路"
              "在打包版中静默失败、退回模糊图标；现在已把 Pillow 打进 exe"),
    ("1.3.2", "窗口图标改用 Win32 WM_SETICON 设置，并按系统 DPI 取所需尺寸"
              "（175% 下 56px）：此前 Tk 的 iconbitmap 只让 Windows 用最小的 16px 帧，"
              "标题栏图标被放大得发糊"),
    ("1.3.1", "尝试修正窗口图标（改用图标里最大的一帧绘画）"),
    ("1.3.0", "更换全新应用图标；图标同时写进 exe 资源并打包进内部，"
              "窗口与任务栏图标随 exe 移动/转发都不丢"),
    ("1.2.0", "每次打开程序清空日志（不再延续上一次内容），开头写一行本次会话信息；"
              "被拒绝的设备每次运行只记一条日志（跨进程去重，界面与后台不会各记一遍）"),
    ("1.1.1", "修复：许可条款「免责声明」等段落的手工折行宽度超出对话框，"
              "导致二次折行与孤儿行；全部条款按 66 列以内重排"),
    ("1.1.0", "新增：位置自愈（随意挪动路径，运行一次即自动修正自启）；"
              "「停止同步」改为全量清理并逐项核验，确保无残留；"
              "修复：受保护进程被误判为已退出，导致后台同步可能重复启动"),
    ("1.0.0", "首个版本：设备准入校验（仅服务 NOSTATION）、未连接时置灰操作按钮、"
              "注册表自启、免安装单文件、版权署名与许可条款"),
]

# ---------------------------------------------------------------------------
# 版本号一致性自检
#
# 版本要同时写在三处（APP_VERSION / APP_BUILD / CHANGELOG 首条），手工维护
# 很容易漏（本项目真的漏过一次 CHANGELOG、也写坏过一次字符串）。这里在导入时
# 就检查，不一致直接抛出，免得带着错误的版本号发出去 —— 自动更新靠版本号比较，
# 版本号错了会导致用户收不到更新或反复收到更新。
# ---------------------------------------------------------------------------
def _check_version_consistency():
    if CHANGELOG and CHANGELOG[0][0] != APP_VERSION:
        raise RuntimeError(
            "版本号不一致：APP_VERSION={!r}，但 CHANGELOG 首条是 {!r}。"
            "发版前请把新版本记进 CHANGELOG 第一行。".format(
                APP_VERSION, CHANGELOG[0][0]))
    if APP_VERSION.count(".") == 2:
        expected_build = int(APP_VERSION.replace(".", ""))
        if APP_BUILD != expected_build:
            raise RuntimeError(
                "APP_BUILD 与 APP_VERSION 不匹配：{} 应对应 {}，实际是 {}。".format(
                    APP_VERSION, expected_build, APP_BUILD))
    ver_in_log = [v for v, _ in CHANGELOG if v == APP_VERSION]
    if len(ver_in_log) != 1:
        raise RuntimeError(
            "CHANGELOG 里 {} 出现了 {} 次（应为 1 次）。".format(
                APP_VERSION, len(ver_in_log)))


_check_version_consistency()

AUTHOR = "Darkside"
COMPANY = "Darkside"
COPYRIGHT_YEAR = "2026"
COPYRIGHT = "Copyright (C) {} {}  (MIT License)".format(COPYRIGHT_YEAR, AUTHOR)
COPYRIGHT_CN = "版权所有 © {} {}，依 MIT 许可证发布".format(COPYRIGHT_YEAR, AUTHOR)
HOMEPAGE = "https://github.com/Darkside114/nostation-hub-sync"
CONTACT = ""            # 反馈邮箱/QQ 等，可留空
EDITION = "完整版"
# 许可条款的版本号，用**条款最后修改日期**，与软件版本号无关。
#
# 规则：只要改动了下面的 LICENSE_TERMS 文字，就把这里改成当天日期
#      （例如 "2026-10-05"）。已同意的用户下次打开界面会重新读到并再次确认。
#      只升软件版本、不动条款时**不要**改这里 —— 否则用户每升一个小版本
#      都要点一次同意，很烦，而且会让"条款变更"这个信号失去意义。
#
# 为什么用日期而不是 1.1.0 这类编号：早期版本误把软件版本号
# （如 1.8.1）存进了配置的 license_version 字段，若再用相似的点分编号
# 会出现"1.1.0 和 1.8.1 谁更新"的歧义。日期制不可能混淆。
LICENSE_VERSION = "2026-10-05"
# 许可条款正文（MIT 开源许可证）。
# 注意：这里的换行是"排版换行"，必须控制在约 70 个显示列以内（中文按 2 列计），
# 否则在对话框里会被二次折行、留下"灯光设置…"这种孤儿行。改文字时请保持同样宽度。
LICENSE_TERMS = """一、开源许可证（MIT）
    本软件是开源软件，以 MIT 许可证发布。
    Copyright (C) 2026 %s

    特此免费授予任何人获得本软件及相关文档文件（"软件"）副本的
    权利，可以不受限制地处理本软件，包括但不限于使用、复制、
    修改、合并、发布、分发、再许可及／或销售本软件副本的权利，
    但须满足以下条件：

    上述版权声明与本许可声明应包含在本软件的所有副本或
    主要部分中。

二、第三方工具声明（与 Matrix Lab 无关）
    本软件是【独立的第三方工具】，由 %s 个人开发与维护。
    作者与 Matrix Lab 及其关联公司没有任何隶属、合作、
    赞助、授权或背书关系。
    "Matrix Lab"、"NOSTATION" 等名称与商标归各自权利人所有，
    本软件仅为说明兼容性而提及。本软件不是 Matrix Lab 的官方
    软件，也未经其审核或认可；遇到问题请勿向 Matrix Lab 寻求
    支持，可在本软件项目主页反馈。

    本软件通过公开的 HID 协议与本机设备通信，不修改设备固件，
    也不代表 Matrix Lab 提供任何功能或服务。

三、署名与来源（MIT 附加的合理要求）
    1. 分发本软件或其修改版时，请保留版权声明与作者署名；
    2. 不得移除或篡改本软件中的版权声明、作者署名及权利标识；
    3. 不得以本软件作者的名义发布、背书或作出任何承诺；
    4. 修改版请注明已作修改，避免与原版混淆。

四、免责声明
    本软件按"现状"提供，不附带任何形式的明示或默示担保，
    包括但不限于对适销性、特定用途适用性及非侵权的担保。
    在任何情况下，作者或版权持有人均不对因本软件或本软件的
    使用或其他交易而产生的任何索赔、损害或其他责任负责，
    无论是合同诉讼、侵权行为还是其他方式。
    本软件只修改设备时钟，不会改动按键映射、灯光设置或屏幕内容，
    也不会联网上传任何数据。

五、关于"开源"
    开放源代码促进会（OSI）对开源的定义要求：允许任何人自由使用、
    修改与再分发。本软件以 MIT 许可证发布，符合该定义，是开源
    软件。任何人可以自由 fork、修改、提交改进，欢迎在项目主页
    提出 Issue 或 Pull Request。

六、权利归属
    本软件著作权归作者 %s 所有，并依 MIT 许可证授予使用
    权利。项目名称与作者署名受商标及署名权保护，MIT 许可证
    不授予使用作者名义进行背书的权利。""" % (AUTHOR, AUTHOR, AUTHOR)

MSG_LICENSE_DECLINED = (
    "你未同意许可条款，软件不会继续运行。\n\n"
    "如果只是觉得条款太长没看完，可以重新打开本软件并选择「是」。")

LICENSE_TERMS_SHORT = LICENSE_TERMS


# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------

MSG_LEN = 32
VIA_RAW_USAGE_PAGE = 0xFF60
VIA_RAW_USAGE = 0x61
AMK_PREFIX = 0xFD
AMK_OK = 0xAA
AMK_SET_DATETIME = 0x37
AMK_GET_AUX_MODE = 0x3A          # 只读，用于确认设备会说 AMK 协议
VIA_PREFIX = 0xFE
VIA_CMD_GET_SIZE = 0x01          # 只读，取设备定义长度
VIA_CMD_GET_DEFINITION = 0x02    # 只读，取设备定义 JSON（含键盘名）

MATRIX_LAB_VID = 0x4D58
NOSTATION_PID = 0x5748
KNOWN_NAMES = {
    0x5748: "NOSTATION",
    0x0103: "100NG Edition",
    0x1510: "Matrix Lab 1510",
    0x1587: "Matrix Lab 1587",
    0x3858: "Matrix Lab 3858",
    0x4643: "Matrix Lab 4643",
    0x4D54: "Matrix Lab 4D54",
    0x4E80: "Matrix Lab 4E80",
    0xB17F: "Matrix Lab B17F",
}

WEEKDAY_NAMES = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]

TASK_NAME = "Nostation Pal hub clock sync"
SHORTCUT_NAME = "Nostation Pal hub clock sync.lnk"

WATCHER_LOCK_PORTS = range(47843, 47853)   # 后台监视进程单实例锁
GUI_LOCK_PORTS = range(47853, 47863)       # 界面单实例（防止重复打开）

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
LOG_MAX_BYTES = 256 * 1024

# ---------------------------------------------------------------------------
# 路径 / 配置
# ---------------------------------------------------------------------------


def script_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def exe_path():
    """当前可执行文件/脚本的完整路径，用于写进自启动项。"""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(__file__)


def _migrate_legacy_data_dir(new_dir):
    """把旧版（NostationSync）目录里的用户数据搬到新目录（NostationPal）。

    2.0 改了程序名，数据目录也跟着换了。已同意过的许可、地址设置等
    不该因为改名而丢失，所以做一次性的搬运：
      * 只搬配置文件与设备拒绝记录，不搬日志（日志本来就每次清空）
      * 新目录已有同名文件时不覆盖（以新目录为准）
      * 任何失败都不影响启动，最坏就是回到"重新同意一次条款"
    """
    try:
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        old_dir = os.path.join(local, "NostationSync")
        if not os.path.isdir(old_dir):
            return
        if os.path.normcase(os.path.abspath(old_dir)) == \
                os.path.normcase(os.path.abspath(new_dir)):
            return
        for name in ("config.json", "rejected-devices.json"):
            src = os.path.join(old_dir, name)
            dst = os.path.join(new_dir, name)
            if os.path.exists(src) and not os.path.exists(dst):
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass
    except Exception:
        pass


def data_dir():
    d = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP_DATA_DIR)
    try:
        os.makedirs(d, exist_ok=True)
        _migrate_legacy_data_dir(d)
    except Exception:
        d = script_dir()
    return d


def log_path():
    return os.path.join(data_dir(), "nostation-sync.log")


def config_path():
    return os.path.join(data_dir(), "config.json")


ICON_FILENAME = "companion.ico"
# 构建时预生成、随 exe 一起打包的窗口图标（只含 16/24/32/48/64 这几档）
WINDOW_ICON_FILENAME = "window-icon.ico"


def _bundled_data_dirs():
    """查找打包进 exe 的数据文件所在目录（PyInstaller 解压到 _MEIPASS）。"""
    out = []
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
            os.path.abspath(sys.executable))
        out.append(base)
        out.append(os.path.dirname(os.path.abspath(sys.executable)))
    out.append(script_dir())
    return out


def sweep_leftover_tempdirs():
    """清理历史遗留在系统临时目录里的 _MEIxxxx，回收磁盘空间。

    单文件程序被强制结束时（关机、任务管理器结束任务、自动更新时替换 exe），
    bootloader 来不及删除临时目录，就留下一份完整依赖副本（约 25~30 MB）。
    累积起来会占用可观空间。

    只删"确凿是本程序留下的"目录，避免误删别的程序：
      * 目录名形如 _MEIxxxxx
      * 目录里含本程序特有的 companion.ico
      * **跳过当前进程自己正在用的那个目录**（见下面的注释，这里犯过错）
      * 删不掉（仍被占用）就跳过
    """
    removed = 0
    freed = 0
    try:
        import tempfile as _tf
        root = _tf.gettempdir()
    except Exception:
        return 0, 0

    # 绝不能删当前进程自己的解压目录 —— 那正是程序运行所需文件所在之处。
    # 之前漏了这一步，结果 sweep 把"自己"当成了"历史残留"删掉：
    # 已加载进内存的 DLL 还能用，但 Tcl 的数据文件（init.tcl 等）在磁盘上
    # 被删了，程序随即报 "Can't find a usable init.tcl" 而启动失败。
    own = os.path.abspath(getattr(sys, "_MEIPASS", "") or "")
    own_real = os.path.realpath(own) if own else ""

    for path in glob.glob(os.path.join(root, "_MEI*")):
        if not os.path.isdir(path):
            continue
        # 跳过自己
        if own:
            try:
                if os.path.abspath(path) == own or os.path.realpath(path) == own_real:
                    continue
            except Exception:
                pass
        try:
            if ICON_FILENAME not in set(os.listdir(path)):
                continue
        except Exception:
            continue
        size = 0
        try:
            for r, _d, fs in os.walk(path):
                for f in fs:
                    try:
                        size += os.path.getsize(os.path.join(r, f))
                    except Exception:
                        pass
            shutil.rmtree(path, ignore_errors=False)
            removed += 1
            freed += size
        except Exception:
            continue
    if removed:
        log("已清理历史临时目录残留 {} 个（回收 {:.1f} MB）".format(
            removed, freed / 1048576.0))
    return removed, freed


def window_icon_asset_path():
    """找构建时预生成的 window-icon.ico；找不到返回空串。

    两种布局都要找：
      * 打包后：--add-data 把它放在 exe 解压目录的**根**下
      * 源码方式运行：它在仓库的 assets/ 子目录里
    """
    for d in _bundled_data_dirs():
        for rel in (WINDOW_ICON_FILENAME, os.path.join("assets", WINDOW_ICON_FILENAME)):
            p = os.path.join(d, rel)
            try:
                if os.path.exists(p):
                    return p
            except Exception:
                continue
    return ""


def icon_path():
    """图标可能有两种存在方式，按优先级找：

    1. 打包进 exe 的数据文件（PyInstaller --add-data 解压到临时目录）
       —— 换电脑、换路径、单独把 exe 发给他人都不会丢；
    2. 与 exe/脚本同目录的 companion.ico（开发时或用户自行放的情形）。
    """
    candidates = []
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(base, ICON_FILENAME))
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                                       ICON_FILENAME))
    candidates.append(os.path.join(script_dir(), ICON_FILENAME))
    for p in candidates:
        try:
            if p and os.path.exists(p):
                return p
        except Exception:
            continue
    return ""


def prepare_window_icon():
    """返回供窗口使用的「画好的小图标」路径。

    为什么需要单独一份：`iconbitmap(原始 ico)` 会让 Windows 去取 ICO 里
    **最小**的那帧（16px），而窗口标题栏在 175% 缩放下要显示 56px，
    于是被硬放大、发糊，大尺寸那套精细设计完全用不上。
    所以要用一份只含合适尺寸（16/24/32/48/64）的图标。

    这份图标由 **构建时** 的 make_window_icon.py 生成并打包进 exe
    （见 assets/window-icon.ico）—— 这样运行时不需要 Pillow，
    exe 里可以把它整个排除掉，体积少 2.6 MB。
    如果那份文件不存在（例如源码方式直接跑、还没构建过），
    才退回用 Pillow 现场生成，保证开发时也能正常显示图标。
    """
    prebuilt = window_icon_asset_path()
    if prebuilt:
        return prebuilt

    src = icon_path()
    if not src:
        return ""
    dst = os.path.join(data_dir(), "window-icon.ico")
    try:
        from PIL import Image
        with Image.open(src) as im:
            best = max(im.info.get("sizes") or [im.size], key=lambda s: s[0])
            frame = im.ico.getimage(best).convert("RGBA")
        sizes = [(s, s) for s in (16, 24, 32, 48, 64)]
        base = frame.resize((64, 64), Image.LANCZOS)
        base.save(dst, format="ICO", sizes=sizes)
        return dst
    except Exception as exc:
        log("准备窗口图标失败（{}），将直接用原始 ico".format(exc))
        return src


def apply_window_icon(window):
    """给窗口/任务栏设置图标（用内嵌资源，拷走 exe 也不会丢）。

    走过的弯路记在这里，避免以后重复踩：

    1. `iconbitmap(原始 ico)` → Windows 取 ICO 里**最小**那帧（16px）当类图标，
       而标题栏在 175% 缩放下要 56px，于是被硬放大、发糊，精细设计全用不上；
    2. `iconphoto(原始像素, format="RGBA")` → Tk 直接报
       `image format "RGBA" is not supported`；
    3. `iconphoto(PNG 数据)` → 能调用但实测窗口图标**一点没变**
       （Tk 设置的是窗口类图标，会被同进程其它 Tk 窗口/类状态影响）；
    4. `iconbitmap(预先画好的 32/48 多尺寸 ico)` → 同样没改变窗口图标。

    所以最后直接用 Win32 `WM_SETICON` 把"从大帧缩下来的清晰图标"设到窗口上，
    这是唯一实测真正改变了窗口图标的做法。
    """
    path = prepare_window_icon()
    if not path:
        return False

    # 方案一：Win32 WM_SETICON（实测有效）
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040
        WM_SETICON = 0x0080
        ICON_SMALL, ICON_BIG = 0, 1

        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                      ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_uint]
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_void_p, ctypes.c_void_p]

        window.update_idletasks()
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        # 标题栏图标尺寸 = SM_CXICON/SM_CYICON 随 DPI 放大（175% 缩放下是 56px）。
        # 这里按系统给的尺寸各加载一份，避免 Windows 把小图硬放大。
        SM_CXICON, SM_CYICON = 11, 12
        SM_CXSMICON, SM_CYSMICON = 49, 50
        big_w = user32.GetSystemMetrics(SM_CXICON) or 32
        big_h = user32.GetSystemMetrics(SM_CYICON) or 32
        small_w = user32.GetSystemMetrics(SM_CXSMICON) or 16
        small_h = user32.GetSystemMetrics(SM_CYSMICON) or 16
        ok = False
        for which, w, h in ((ICON_BIG, big_w, big_h), (ICON_SMALL, small_w, small_h)):
            hicon = user32.LoadImageW(None, path, IMAGE_ICON, w, h, LR_LOADFROMFILE)
            if hicon:
                user32.SendMessageW(hwnd, WM_SETICON, which, hicon)
                ok = True
        if ok:
            log("窗口图标已用 WM_SETICON 设置（{}，大图 {}x{}）".format(
                os.path.basename(path), big_w, big_h))
            return True
    except Exception as exc:
        log("WM_SETICON 设置窗口图标失败: {}".format(exc))

    # 方案二：退回 Tk 自带方式
    try:
        window.iconbitmap(default=path)
        return True
    except Exception:
        try:
            window.iconbitmap(path)
            return True
        except Exception as exc:
            log("设置窗口图标失败: {}".format(exc))
            return False


_LOG_SESSION = None      # 当前日志文件所属的"会话"（用文件创建时间当标记）


def _log_session():
    """日志文件的会话标记 = 文件的创建时间。

    谁重新写了日志（清空重写），这个值就变；其它进程发现变了，
    就知道"日志被重置过"，于是重新读取共享状态、而不是把旧内容再写回去。
    """
    try:
        return os.stat(log_path()).st_ctime_ns
    except Exception:
        return None


def _sync_log_session():
    """发现日志被别人重置过时，重建本进程的去重状态。

    返回 True 表示本次确实检测到重置。
    """
    global _LOG_SESSION
    now = _log_session()
    if now is None:
        return False
    if _LOG_SESSION is None:
        _LOG_SESSION = now
        return False
    if now != _LOG_SESSION:
        _LOG_SESSION = now
        _REJECT_LOGGED.clear()
        _load_reject_state()
        return True
    return False


def log(msg):
    line = "{}  {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        _sync_log_session()
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if os.path.getsize(log_path()) > LOG_MAX_BYTES:
            with open(log_path(), "r", encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-500:]
            with open(log_path(), "w", encoding="utf-8") as fh:
                fh.writelines(tail)
    except Exception:
        pass


def reset_log(why=""):
    """把日志清空，只留一行本次会话的开头。

    **每次打开程序（以及每次后台进程启动）都清空**，不延续上一次内容——
    即使后台同步进程正在运行也照清。开头那行记录版本、时间与模式。
    同时清掉"已记录过的拒绝设备"状态，让本次会话的拒绝记录重新出现一次。

    多个进程同时写这一个日志，所以清空后要更新会话标记：
    另一个进程下次写日志时会检测到并重建自己的状态，不会把旧内容写回来。
    """
    global _LOG_SESSION
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head = "===== {}  启动于 {}   v{}   {} =====".format(
        APP_TITLE, stamp, APP_VERSION, why).rstrip()
    try:
        with open(log_path(), "w", encoding="utf-8") as fh:
            fh.write(head + "\n")
    except Exception:
        pass
    _LOG_SESSION = _log_session()
    _REJECT_LOGGED.clear()
    try:
        os.remove(_reject_state_path())
    except Exception:
        pass


def read_tail(path, lines=200):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:])
    except Exception:
        return ""


def load_config():
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
            if isinstance(cfg, dict):
                return cfg
    except Exception:
        pass
    return {}


def save_config(cfg):
    try:
        with open(config_path(), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 许可条款 / 版权
# ---------------------------------------------------------------------------


def license_message(is_update=False):
    """许可对话框正文（条款只在正文里出现一次）。

    is_update=True 表示用户以前同意过、这次是因为**条款更新**重新确认，
    开头会说明原因，免得用户以为程序坏了。
    """
    head = [
        "感谢使用 {}".format(APP_TITLE),
        "{}   v{}    {}".format(APP_NAME_EN, APP_VERSION, EDITION),
        COPYRIGHT_CN,
        "",
    ]
    if is_update:
        head += [
            "【许可条款已更新】",
            "本软件的许可条款有新版本（条款版本 {}）。".format(LICENSE_VERSION),
            "你以前已同意过旧版本，这次需要重新阅读并确认。",
            "主要变化见下方条款；软件功能与你的设置都不受影响。",
            "",
        ]
    else:
        head += [
            "开始使用前，请阅读并同意以下许可条款：",
            "",
        ]
    tail = [
        "",
        "────────────────────────────────────────",
        "如果这个软件帮到了你，欢迎到项目主页点个 Star（星标）：",
        HOMEPAGE,
        "开源项目靠这个被人看到，也是作者继续维护的动力。",
        "有建议或遇到问题，同样可以在上面提 Issue。",
        "",
        "是否同意以上条款并继续使用？",
    ]
    return "\n".join(head + [LICENSE_TERMS] + tail)


def help_text():
    """使用说明：每个按钮/指示灯是干什么的（面向第一次用的人）。"""
    return """\
这个软件只做一件事：让你的 NOSTATION hub 每次开机自动校准时间，
不用再手动打开 config.matrix-lab.com 网页点同步。

──────────────────────────────────────────
一、四个按钮是干什么的
──────────────────────────────────────────
【开启 Nostation 开机同步】——最常用的按钮，点一次就好
    作用：把开机自启装到系统里（写入当前用户的启动项），
          并立刻启动后台同步进程。
    结果：以后每次开机自动校时；现在也会马上同步一次。
    提示：点一次就够，不需要每次开机都点。重复点不会出问题。

【停止同步】——想彻底关掉时点它
    作用：清理全部自启项（注册表项、启动文件夹、计划任务），
          并结束后台同步进程。
    结果：程序不再自动校时；清理完会逐项核验，确认没有残留。
    提示：设备没插着也能点（故意设计成这样，否则拔了设备就关不掉）。

【立即同步一次】——手动校时
    作用：马上把本机时间写入设备一次。
    结果：运行记录里会多一行「校时 …… 成功」。
    提示：适合刚开机还没触发自动同步、或想确认设备通不通的时候。

【重新检查】——刷新界面显示的状态
    作用：重新核对五件事并显示出来：① 设备插没插 ② 开机自启在不在
          ③ 启动文件夹里有没有 ④ 有没有开机前校时任务 ⑤ 后台同步在不在跑。
    结果：运行记录里会写一行「手动重新检查：✓…✗…」，一眼能看出哪项不对。
    提示：界面每 3 秒会自动检查「设备插拔」，但另外几项不会自动重查；
          你手动改过自启、或觉得显示和实际不符时，点它。

──────────────────────────────────────────
二、上面那个状态区怎么看
──────────────────────────────────────────
左边的小圆点（指示灯）
    绿色 = 后台同步进程在运行      红色 = 后台同步没在运行
    —— 它反映的是「同步程序在不在跑」，不是「设备插没插」。

设备：
    已连接（NOSTATION）            设备正常，可以校时
    未检测到 NOSTATION（请检查 USB 连接）
                                  没找到设备；此时下面两个需要设备的
                                  按钮会变灰，点不动

开机自动同步：
    已开启（开机自启）· 后台同步中  一切正常
    未开启                          点「开启…」按钮即可
    已开启（开机自启(路径已失效…)）  程序被挪过位置，运行一次会自动修正

──────────────────────────────────────────
三、运行记录里的那几行是什么意思
──────────────────────────────────────────
校时 2026-10-05 周一 09:14:03 -> 1/1 成功 (… -> 已同步)
    成功把本机时间写进设备。1/1 表示找到 1 台 NOSTATION、成功 1 台。

拒绝设备 4d58:0103 100NG Edition：产品 ID 不是 NOSTATION (5748)
    这是正常现象，不用担心。你机器上还有别的 Matrix Lab 设备时，
    程序会检查它、发现不是 NOSTATION 就拒绝操作，并记录一行作为证明：
    「本程序只动 NOSTATION 这一台」。同一台设备每次运行只记一条。

未检测到连接（设备已断开），操作按钮已置灰
    设备被拔掉了，程序自动暂停校时，不会报错。

手动重新检查（09:20:11）：✓ 设备已连接 · ✓ 开机自启 · …
    你点了「重新检查」的结果，✓ 正常、✗ 有问题、— 表示该项本来就没设置。

每次打开程序都会清空旧日志，只留本次运行的内容。

──────────────────────────────────────────
四、自动更新（检查更新 / 版本历史）
──────────────────────────────────────────
程序启动约 3 秒后会自动去 GitHub 看看有没有新版本。
没有更新时它完全安静，不会有任何提示 —— 你不需要管它。

【有更新时会怎样】
    弹一个窗口，写着新版本号、下载大小和更新内容，问你：
      「是」= 下载并自动替换成新版本
      「否」= 这次不更新，并且以后不再为这个版本反复提示你

【点「是」之后会怎样】
    1. 出现进度条，下载新版本（约 13 MB）
    2. 自动校验文件 SHA256，确认下载完整没被篡改
    3. 提示你「点确定后程序会关闭并自动替换」
    4. 程序关闭 → 新版本替换旧 exe → 自动重新打开（约 3~10 秒）
    你的开机自启设置、日志、配置都不受影响。

【菜单里的三项】
    帮助(H) → 检查更新
        立刻手动检查一次。有更新就弹上面那个窗口；
        已经是最新会告诉你是最新的。
    帮助(H) → 版本历史
        每个版本改了什么，都在这里（不再堆在「关于」里）。
    帮助(H) → 启动时自动检查更新（可勾选）
        不想要自动检查就取消勾选。取消后仍然可以手动「检查更新」。

【关于联网】
    只有「检查更新」会访问 GitHub 获取版本信息，再无其它任何联网行为，
    而且可以关掉。校时功能始终只与本机 USB 设备通信。

──────────────────────────────────────────
五、为什么有的按钮是灰的
──────────────────────────────────────────
没检测到 NOSTATION 时，「开启开机同步」和「立即同步一次」会变灰不能点，
这是故意的 —— 防止误操作。插好设备后 3 秒内会自动恢复，也可以点
「重新检查」立即恢复。

「停止同步」任何时候都能点：设备拔了、收起来了，你仍然要能关掉开机自启。

──────────────────────────────────────────
六、常见问题
──────────────────────────────────────────
问：点了「开启开机同步」之后，我还需要留着这个窗口吗？
答：不用。关掉窗口不影响同步，后台进程会继续工作。

问：怎么知道它真的在自动同步？
答：看指示灯是不是绿色；或者过一会儿点「重新检查」，
    运行记录里会有新的「校时 …… 成功」。（默认每 30 分钟校一次）

问：提示「未检测到连接」怎么办？
答：① 确认 USB 线插好、hub 已开机 ② 换一个 USB 口试试
    ③ 点「重新检查」 ④ 还是不行就看「帮助 → 未检测到连接时怎么办」。

问：我把程序挪到别的文件夹 / 改了名字，需要重新设置吗？
答：不需要。运行一次就会自动把开机自启改到新位置。

问：怎么彻底卸载？
答：先点「停止同步」，再删掉这个 exe 就行。程序不写系统目录，
    数据只在 %LOCALAPPDATA%\\NostationPal（可以直接删）。

问：它会不会偷偷联网、上传数据？
答：不会上传任何数据。只有「检查更新」会访问 GitHub 查版本号，
    而且可以在帮助菜单里关掉（见上面第四节）。

问：运行记录里老出现「拒绝设备」，是不是有问题？
答：不是。见上面第三节。程序只操作 NOSTATION，其它设备一律拒绝并留记录。
"""


def show_text_dialog(parent, title, body, width=78, height=32):
    """用一个可滚动、可复制的对话框显示长文本（比 MessageBox 好排版）。"""
    import tkinter as tk
    from tkinter import ttk

    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    win.title(title)
    try:
        win.transient(parent)
    except Exception:
        pass
    try:
        win.geometry("{}x{}".format(int(width * 9.6), int(height * 20)))
    except Exception:
        pass

    frame = ttk.Frame(win, padding=10)
    frame.pack(fill="both", expand=True)
    txt = tk.Text(frame, wrap="word", font=("Microsoft YaHei UI", 10),
                  background="#fbfbfb", relief="flat", padx=8, pady=6)
    sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    txt.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    txt.insert("1.0", body)
    txt.configure(state="disabled")

    bar = ttk.Frame(win, padding=(10, 0, 10, 10))
    bar.pack(fill="x")
    ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")
    try:
        win.bind("<Escape>", lambda e: win.destroy())
        win.focus_force()
    except Exception:
        pass
    if parent is None:
        win.mainloop()
    return win


def changelog_text():
    """版本历史（独立对话框用；不再塞进「关于」里）。"""
    lines = ["{}   {}   v{}".format(APP_TITLE, APP_NAME_EN, APP_VERSION),
             COPYRIGHT_CN, "", "版本历史（新的在上）：", ""]
    for ver, note in CHANGELOG:
        lines.append("  v{}".format(ver))
        lines.append("      {}".format(note))
        lines.append("")
    lines.append("每次更新都会在这里记一条。程序内可点菜单「帮助 → 检查更新」获取新版。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 检查更新（走 GitHub Releases 公开 API）
# ---------------------------------------------------------------------------

UPDATE_API = "https://api.github.com/repos/Darkside114/nostation-hub-sync/releases/latest"
UPDATE_PAGE = HOMEPAGE + "/releases/latest"
UPDATE_ASSET_NAME = "Nostation.exe"
UPDATE_TIMEOUT = 20


def version_tuple(text):
    """把 "v1.10.2" 之类解析成可比较的元组。"""
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(text or ""))
    if not m:
        return None
    return tuple(int(g) if g else 0 for g in m.groups())


def fetch_latest_release():
    """查询最新 Release。返回 dict，网络失败返回 None。

    只用标准库 urllib，不引入新依赖；带 20 秒超时，失败静默返回 None
    —— 检查更新失败不该影响软件正常使用。
    """
    import urllib.request
    req = urllib.request.Request(
        UPDATE_API,
        headers={"User-Agent": "NostationsPal/{}".format(APP_VERSION),
                 "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=UPDATE_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        log("检查更新失败（网络或接口）: {}".format(exc))
        return None
    tag = data.get("tag_name") or ""
    asset_url, asset_size, asset_digest = "", 0, ""
    for a in data.get("assets") or []:
        if a.get("name") == UPDATE_ASSET_NAME:
            asset_url = a.get("browser_download_url") or ""
            asset_size = int(a.get("size") or 0)
            asset_digest = a.get("digest") or ""
            break
    return {
        "tag": tag,
        "version": version_tuple(tag),
        "name": data.get("name") or tag,
        "body": data.get("body") or "",
        "html_url": data.get("html_url") or UPDATE_PAGE,
        "published": data.get("published_at") or "",
        "asset_url": asset_url,
        "asset_size": asset_size,
        # GitHub 直接给附件的 SHA256 摘要（形如 "sha256:abc..."），
        # 用它校验完整性，就不用把哈希写进 Release 说明里了
        "asset_digest": asset_digest,
    }


def has_update(rel):
    if not rel or not rel.get("version"):
        return False
    cur = version_tuple(APP_VERSION)
    return bool(cur) and rel["version"] > cur


def sha256_of(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def expected_sha_from_body(body):
    """从 Release 说明正文里找 64 位十六进制哈希（兼容早期把哈希写在说明里的版本）。"""
    m = re.search(r"\b([0-9A-Fa-f]{64})\b", body or "")
    return m.group(1).upper() if m else ""


def expected_sha(rel):
    """期望的 SHA256（大写十六进制）。

    优先用 GitHub 给附件的 digest 字段（形如 "sha256:abc..."）——
    这样 Release 说明里不必再堆一段校验信息，说明可以只写版本号和更新内容；
    没有 digest 时退回从说明正文里找 64 位十六进制（兼容旧 Release）。
    """
    digest = (rel or {}).get("asset_digest") or ""
    m = re.search(r"([0-9A-Fa-f]{64})", digest)
    if m:
        return m.group(1).upper()
    return expected_sha_from_body((rel or {}).get("body"))


def download_update(rel, dest, progress=None):
    """下载新 exe 到 dest。progress(已下载, 总大小) 可选。返回 (ok, 说明)。"""
    import urllib.request
    url = rel.get("asset_url") or ""
    if not url:
        return False, "这个版本没有可下载的文件"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "NostationsPal/{}".format(APP_VERSION)})
        tmp = dest + ".part"
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or rel.get("asset_size") or 0)
            done = 0
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    try:
                        progress(done, total)
                    except Exception:
                        pass
        # 完整性校验：拿到期望哈希就必须匹配
        want = expected_sha(rel)
        got = sha256_of(tmp).upper()
        if want and want != got:
            os.remove(tmp)
            return False, "下载文件校验失败（SHA256 不匹配）\n期望 {}\n实际 {}".format(want, got)
        os.replace(tmp, dest)
        return True, ("SHA256 校验通过" if want
                      else "已下载（该版本未提供校验值，跳过校验）")
    except Exception as exc:
        log("下载更新失败: {}".format(traceback.format_exc()))
        return False, "下载失败：{}".format(exc)


def spawn_self_replace(new_exe, log_file=None, no_restart=False):
    """派一个独立脚本：等本进程退出 → 覆盖旧 exe → 重新启动。

    为什么必须交给外部进程：Windows 不允许程序覆盖自己正在运行的 exe
    （但允许**改名**它）。所以这里的做法是反复尝试替换 —— 进程一退出、
    文件锁一释放，替换就成功。

    踩过的两个坑（都实测过）：
    1. 不能用 `tasklist ... | find "PID"` 判活 —— 给 find 加引号会变成
       按字面搜索，永远匹配不到；
    2. 更不能用 `tasklist` 的退出码判活 —— 它无论找没找到**都返回 0**，
       只看 stdout 里有没有 "No tasks"。所以干脆不判活，直接重试替换。

    返回 (ok, 说明)。
    """
    if not os.path.exists(new_exe):
        return False, "下载文件不存在: {}".format(new_exe)
    target = exe_path()
    pid = os.getpid()
    helper = os.path.join(data_dir(), "self-update.ps1")
    if log_file is None:
        log_file = os.path.join(data_dir(), "self-update.log")

    # 脚本内容固定，路径全部通过参数传入 —— 避免中文路径写进文件后
    # 因编码问题损坏（.ps1 里的非 ASCII 就是之前踩过的 BOM 坑）。
    script = "\n".join([
        "param([Parameter(Mandatory=$true)][string]$Target,",
        "      [Parameter(Mandatory=$true)][string]$New,",
        "      [int]$OldPid = 0,",
        "      [string]$Log = '',",
        "      [switch]$NoRestart)",
        "$ErrorActionPreference = 'SilentlyContinue'",
        "function Say($m) {",
        "  $line = (Get-Date).ToString('HH:mm:ss') + '  ' + $m",
        "  if ($Log) { Add-Content -LiteralPath $Log -Value $line -Encoding UTF8 }",
        "}",
        "Say ('等待旧进程退出 (pid ' + $OldPid + ')')",
        "$deadline = (Get-Date).AddSeconds(40)",
        "while ((Get-Date) -lt $deadline) {",
        "  if (-not (Get-Process -Id $OldPid -ErrorAction SilentlyContinue)) { break }",
        "  Start-Sleep -Milliseconds 400",
        "}",
        "Say '旧进程已退出，开始替换'",
        "$ok = $false",
        "$deadline = (Get-Date).AddSeconds(25)",
        "while ((Get-Date) -lt $deadline) {",
        "  if (-not (Test-Path -LiteralPath $New)) { $ok = $true; break }",
        "  try {",
        "    Move-Item -LiteralPath $New -Destination $Target -Force -ErrorAction Stop",
        "    $ok = $true",
        "    Say '替换成功'",
        "    break",
        "  } catch {",
        "    Start-Sleep -Milliseconds 400",
        "  }",
        "}",
        "if ($ok) {",
        "  Say ('新版本已就位: ' + $Target)",
        "  if (-not $NoRestart) {",
        "    Say '重新启动程序'",
        "    Start-Process -FilePath $Target",
        "  }",
        "} else {",
        "  Say '替换失败（文件仍被占用）'; exit 1",
        "}",
        "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force",
        "",
    ])
    try:
        with open(helper, "w", encoding="utf-8-sig", newline="\r\n") as fh:
            fh.write(script)
    except Exception as exc:
        return False, "无法写入替换脚本: {}".format(exc)

    CREATE_NO_WINDOW = 0x08000000
    exe = "powershell.exe"
    args = [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", helper,
            "-Target", target, "-New", new_exe, "-OldPid", str(pid), "-Log", log_file]
    if no_restart:
        args.append("-NoRestart")
    try:
        subprocess.Popen(args, creationflags=CREATE_NO_WINDOW, close_fds=True,
                         cwd=data_dir())
    except Exception as exc:
        return False, "无法启动替换脚本: {}".format(exc)
    log("已派发自替换脚本: {} -> {}（等 pid {} 退出）".format(new_exe, target, pid))
    return True, helper


def show_license(is_update=False):
    """显示 UAC 无关的许可对话框。返回 MessageBox 结果码，失败返回 None。

    is_update=True 用于「条款更新后重新确认」，标题会写明，避免用户以为程序异常。
    """
    try:
        MB_YESNO = 0x04
        MB_ICONINFORMATION = 0x40
        MB_TOPMOST = 0x40000
        IDYES = 6
        title = "{} v{}  ·  许可条款{}".format(
            APP_TITLE, APP_VERSION, "（已更新，请重新确认）" if is_update else "")
        # MessageBox 文本上限约 64KB，这里远低于该值
        rc = ctypes.windll.user32.MessageBoxW(
            0, license_message(is_update=is_update), title,
            MB_YESNO | MB_ICONINFORMATION | MB_TOPMOST)
        return rc
    except Exception:
        log("许可对话框显示失败: {}".format(traceback.format_exc()))
        return None


def license_ok():
    """检查许可条款是否已同意（按**条款版本**判断，不是软件版本）。

    规则：
    - 全新安装（配置里没有条款版本）→ 弹出来读一遍并同意
    - 条款版本变了（新增/修改了条款）→ **重新弹一次**，让老用户也读到新条款
    - 条款版本没变 → 直接放行，不打扰用户

    注意：只有图形界面（run_gui）会调这个函数；后台同步进程（--watch）
    不检查许可，所以即使条款更新了、用户还没点同意，开机校时也不会中断。
    """
    cfg = load_config()
    accepted = cfg.get("license_version")
    if cfg.get("license_accepted") and accepted == LICENSE_VERSION:
        return True

    is_update = bool(cfg.get("license_accepted"))
    rc = show_license(is_update=is_update)
    if rc is None:
        # 无法弹窗（例如极端环境），不阻塞用户
        return True
    if rc == 6:  # IDYES
        cfg["license_accepted"] = True
        cfg["license_accepted_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        cfg["license_version"] = LICENSE_VERSION
        cfg["license_holder"] = AUTHOR
        save_config(cfg)
        log("用户已同意许可条款 (条款版本 {}，软件 v{}{})".format(
            LICENSE_VERSION, APP_VERSION,
            "，本次为条款更新后重新确认" if is_update else ""))
        return True
    log("用户未同意许可条款，程序退出（条款版本 {}）".format(LICENSE_VERSION))
    return False


def about_text():
    lines = [
        "{}   v{}   ({})".format(APP_TITLE, APP_VERSION, EDITION),
        APP_NAME_EN,
        "",
        COPYRIGHT,
        COPYRIGHT_CN,
        "",
        "作者 / 出品：{}".format(AUTHOR),
    ]
    if HOMEPAGE:
        lines.append("项目主页：{}".format(HOMEPAGE))
    if CONTACT:
        lines.append("联系方式：{}".format(CONTACT))
    lines += [
        "",
        "功能：让 Matrix Lab NOSTATION hub 在开机、登录或重新接入时",
        "      自动把本机时间写入设备（与官网 config.matrix-lab.com",
        "      网页版 Sync 按钮使用相同的 HID 协议）。",
        "",
        "隐私：本软件不联网、不上传任何数据，只与本机 USB HID 设备通信。",
        "      （仅「检查更新」会访问 GitHub 公开接口，可在帮助菜单关闭该行为）",        "授权：{}，以 MIT 许可证【开源】发布——可自由使用、修改、".format(EDITION),
        "      再分发，保留版权声明与署名即可。欢迎在项目主页提",
        "      Issue 或 Pull Request。",
        "",
        "版本历史：见帮助菜单「版本历史」（不在这里堆一长串）。",
        "条款版本：{}".format(LICENSE_VERSION),
        "程序：{}".format(exe_path()),
        "日志：{}".format(log_path()),
    ]
    return "\n".join(lines)


def show_about(parent=None):
    """关于对话框：版权、作者、条款要点。"""
    try:
        from tkinter import messagebox
        title = "关于 {}".format(APP_TITLE)
        body = about_text()
        if parent is not None:
            messagebox.showinfo(title, body, parent=parent)
        else:
            messagebox.showinfo(title, body)
        return True
    except Exception:
        # 没有 Tk 环境时退化成系统对话框
        try:
            ctypes.windll.user32.MessageBoxW(
                0, about_text(), "关于 {} v{}".format(APP_TITLE, APP_VERSION),
                0x40 | 0x40000)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# HID 协议
# ---------------------------------------------------------------------------


_ENUM_CACHE = {"t": 0.0, "devices": []}
ENUM_CACHE_TTL = 2.5        # 秒；hid.enumerate() 单次要 1s+，短时间内复用结果


def enumerate_raw_hid(use_cache=True):
    """枚举 Vial raw HID 接口。

    实测 hid.enumerate() 在本机要 1.1~1.5 秒（系统里有 28 个 HID 设备），
    而界面刷新、状态文字、后台轮询都会调用它，所以加一个短 TTL 缓存。
    后台巡查每 3 秒一轮，仍然会拿到新鲜数据；界面连续刷新则复用同一次枚举。
    """
    now = time.monotonic()
    if use_cache and (now - _ENUM_CACHE["t"]) < ENUM_CACHE_TTL:
        return list(_ENUM_CACHE["devices"])
    import hid
    out = []
    for d in hid.enumerate():
        if d.get("usage_page") == VIA_RAW_USAGE_PAGE and d.get("usage") == VIA_RAW_USAGE:
            out.append(d)
    _ENUM_CACHE["t"] = now
    _ENUM_CACHE["devices"] = out
    return list(out)


def describe(d):
    name = d.get("product_string") or KNOWN_NAMES.get(d["product_id"], "未知设备")
    return "{:04x}:{:04x} {}".format(d["vendor_id"], d["product_id"], name)


def hub_brief():
    """给界面用的一句话设备描述。

    界面上不显示 4d58:5748 这种 ID —— 对普通用户没意义又容易困惑；
    完整 ID 仍会写进日志，需要排查问题时看日志即可。
    """
    try:
        targets = pick_targets()
    except Exception:
        targets = []
    if not targets:
        return ""
    name = targets[0].get("product_string") or "NOSTATION"
    return name.replace("NOSTATION ", "NOSTATION ").strip()


NOSTATION_PRODUCT_TAG = "NOSTATION"
# 准入校验结果缓存：{path: (approved, reason, monotonic_ts)}
_ADMISSION_CACHE = {}
ADMISSION_CACHE_TTL = 60.0     # 已通过的设备在这个时间内不重复做重量级校验
# 已被拒绝并记过日志的设备（跨进程持久化，避免界面/后台两个进程各记一遍）
_REJECT_LOGGED = set()
_REJECT_STATE_NAME = "rejected-devices.json"


def _reject_state_path():
    return os.path.join(data_dir(), _REJECT_STATE_NAME)


def _load_reject_state():
    try:
        with open(_reject_state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            _REJECT_LOGGED.update(str(x) for x in data)
    except Exception:
        pass


def _save_reject_state():
    try:
        with open(_reject_state_path(), "w", encoding="utf-8") as fh:
            json.dump(sorted(_REJECT_LOGGED), fh, ensure_ascii=False, indent=0)
    except Exception:
        pass


def _log_rejection(d, reason):
    """拒绝日志：同一台设备（同一原因）每次运行只写一条，且跨进程只写一条。

    后台进程每 3 秒枚举一次设备，不限流会把日志刷满；界面模式和后台模式
    是两个进程，所以去重集合要落盘共享，否则日志清空后另一个进程会再写一遍。
    """
    key = "{}|{}".format(d.get("path") or describe(d), reason)
    if key in _REJECT_LOGGED:
        return
    _REJECT_LOGGED.add(key)
    _save_reject_state()
    log("拒绝设备 {}：{}".format(describe(d), reason))


def _vial_get_definition(dev, max_bytes=65536):
    """读取设备的 Vial 定义 JSON（键盘名等），用于身份校验。

    这是只读命令（0xFE 0x01/0x02），不会修改设备任何配置。
    """
    import json

    def xfer(payload, timeout_ms=800):
        pkt = bytes(payload) + b"\x00" * (MSG_LEN - len(payload))
        dev.write(b"\x00" + pkt)
        r = dev.read(MSG_LEN, timeout_ms=timeout_ms)
        return bytes(r) if r else None

    while dev.read(MSG_LEN, timeout_ms=40):   # 排空
        pass
    resp = xfer([VIA_PREFIX, VIA_CMD_GET_SIZE])
    if not resp or resp[0] != VIA_PREFIX:
        return None
    size = (resp[1] << 8) | resp[2]
    if not size or size > max_bytes:
        return None
    blob = b""
    while len(blob) < size:
        off = len(blob)
        resp = xfer([VIA_PREFIX, VIA_CMD_GET_DEFINITION, (off >> 8) & 0xFF, off & 0xFF])
        if not resp or resp[0] != VIA_PREFIX:
            return None
        blob += resp[4:]
    try:
        return json.loads(blob[:size].decode("utf-8", "replace"))
    except Exception:
        return None


def _amk_aux_mode(dev, timeout_ms=800):
    """发一条只读 AMK 命令，返回设备应答里的显示模式值；无应答返回 None。

    返回整数即代表"这台设备装的是 Matrix Lab 固件"——普通键盘/官方 Vial
    都不会对这个私有命令作答。
    """
    while dev.read(MSG_LEN, timeout_ms=40):
        pass
    pkt = bytes([AMK_PREFIX, AMK_GET_AUX_MODE]) + b"\x00" * (MSG_LEN - 2)
    dev.write(b"\x00" + pkt)
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        r = dev.read(MSG_LEN, timeout_ms=150)
        if not r:
            continue
        resp = bytes(r)
        if len(resp) > 3 and resp[0] == AMK_PREFIX and resp[1] == AMK_GET_AUX_MODE:
            if resp[2] != AMK_OK:
                return None
            return resp[3]
    return None


def admit_device(d, use_cache=True):
    """准入校验：这台设备是不是"我们要服务的 NOSTATION"。

    三道校验全部通过才准入，任何一道不过都会被拒绝并记入日志：

      1. USB 身份：厂商 ID = Matrix Lab (0x4D58) 且 产品 ID = NOSTATION (0x5748)；
      2. 产品字符串：必须含 "NOSTATION"；
      3. 固件指纹：设备必须对 Matrix Lab 私有的 AMK 只读命令 (FD 3A) 作出应答。
         这一条最关键——AMK 协议是 Matrix Lab 固件特有的（官方 Vial 和普通
         键盘都不会答应答这个命令），而且返回值必须是合法的显示模式。
         所以"拿别的键盘伪装成 NOSTATION"到这一关就会被挡下。

    （曾经还想读设备固件里的 Vial 定义来核对键盘名，但这台 NOSTATION 的固件
      并不支持 0xFE 0x01/0x02 读定义命令，实测返回 08 07 ...，所以改用 AMK 指纹。）

    这个函数是**唯一**的准入判断：pick_targets()/hub_present()/sync_now()/
    后台巡查全部走它，代码里不存在任何"跳过校验"的旁路。
    """
    import hid
    path = d.get("path")
    key = path
    now = time.monotonic()
    if use_cache and key in _ADMISSION_CACHE:
        approved, reason, ts = _ADMISSION_CACHE[key]
        # 通过与否都缓存，60 秒内直接复用判定结果：
        # 通过的要避免重复读指纹，被拒的（如那台 100NG）也要避免每 3 秒
        # 重新走一遍校验逻辑。
        if (now - ts) < ADMISSION_CACHE_TTL:
            return approved, reason

    def reject(reason):
        _ADMISSION_CACHE[key] = (False, reason, now)
        _log_rejection(d, reason)
        return False, reason

    if d.get("vendor_id") != MATRIX_LAB_VID:
        return reject("厂商 ID 不是 Matrix Lab ({:04x})".format(MATRIX_LAB_VID))
    if d.get("product_id") != NOSTATION_PID:
        return reject("产品 ID 不是 NOSTATION ({:04x})".format(NOSTATION_PID))
    product = (d.get("product_string") or "").upper()
    if NOSTATION_PRODUCT_TAG not in product:
        return reject("产品名不含 NOSTATION: {!r}".format(d.get("product_string")))

    dev = hid.device()
    try:
        dev.open_path(path)
    except Exception as exc:
        return reject("无法打开设备: {}".format(exc))
    try:
        try:
            mode = _amk_aux_mode(dev)
        except Exception as exc:
            mode = None
            log("AMK 指纹读取异常: {}".format(exc))
        if mode is None:
            return reject("设备未回应 AMK 指纹命令（不是 Matrix Lab NOSTATION 固件）")
        if not (0 <= mode <= 8):
            return reject("AMK 指纹返回值异常: {}".format(mode))
        _ADMISSION_CACHE[key] = (True, "ok(mode={})".format(mode), now)
        return True, "ok"
    finally:
        try:
            dev.close()
        except Exception:
            pass


def pick_targets():
    """返回**当前已通过准入校验**的 NOSTATION 设备；其它任何设备都不返回。

    这里没有任何"退化/兜底"分支：不匹配就直接没有目标，
    界面会显示"未检测到连接"，后台同步会安静等待。
    """
    approved = []
    for d in enumerate_raw_hid():
        try:
            ok, _reason = admit_device(d)
        except Exception as exc:
            log("准入校验异常 {}: {}".format(describe(d), exc))
            ok = False
        if ok:
            approved.append(d)
    return approved


def hub_present():
    """设备是否已连接（且通过准入校验）。界面与后台同步都以此为准。"""
    try:
        return len(pick_targets()) > 0
    except Exception:
        return False


def set_datetime(dev, when, timeout_ms=2000):
    payload = bytes([
        AMK_PREFIX, AMK_SET_DATETIME,
        (when.year >> 8) & 0xFF, when.year & 0xFF,
        when.month, when.day, when.isoweekday(),
        when.hour, when.minute, when.second,
    ])
    payload += b"\x00" * (MSG_LEN - len(payload))

    while dev.read(MSG_LEN, timeout_ms=40):  # 排空残留
        pass
    dev.write(b"\x00" + payload)

    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        r = dev.read(MSG_LEN, timeout_ms=150)
        if not r:
            continue
        resp = bytes(r)
        if len(resp) > 2 and resp[0] == AMK_PREFIX and resp[1] == AMK_SET_DATETIME:
            if resp[2] == AMK_OK:
                return True, "设备已确认"
            return False, "设备返回 0x{:02x}".format(resp[2])
    return False, "设备无响应"


def sync_now(verbose=False):
    """立刻校时一次。返回 (成功数, 目标数, 说明列表)。

    未检测到设备时不做任何写入动作，只返回结果，界面会据此提示并置灰按钮。
    """
    import hid
    try:
        targets = pick_targets()
    except Exception as exc:
        return 0, 0, ["无法枚举 HID 设备: {}".format(exc)]
    if not targets:
        if verbose:
            _safe_print("未检测到连接：请确认 NOSTATION 已通过 USB 连接到本机")
        return 0, 0, ["未检测到连接（未发现 NOSTATION 设备）"]

    when = datetime.datetime.now()
    details = []
    ok_count = 0
    for d in targets:
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except Exception as exc:
            details.append("{} -> 打开失败: {}".format(describe(d), exc))
            continue
        try:
            ok, detail = set_datetime(dev, when)
        except Exception as exc:
            ok, detail = False, "异常: {}".format(exc)
        finally:
            try:
                dev.close()
            except Exception:
                pass
        details.append("{} -> {}".format(describe(d), "已同步" if ok else "失败: " + detail))
        ok_count += 1 if ok else 0

    stamp = "{:04d}-{:02d}-{:02d} {} {:02d}:{:02d}:{:02d}".format(
        when.year, when.month, when.day, WEEKDAY_NAMES[when.isoweekday()],
        when.hour, when.minute, when.second)
    log("校时 {} -> {}/{} 成功 ({})".format(stamp, ok_count, len(targets), "; ".join(details)))
    if verbose:
        _safe_print("\n".join(details))
    return ok_count, len(targets), details


# ---------------------------------------------------------------------------
# 单实例锁 / 进程
# ---------------------------------------------------------------------------


def acquire_lock(ports):
    """绑本地端口当锁。成功返回 socket，被占用返回 None。"""
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            sock.listen(1)
            return sock
        except OSError:
            try:
                sock.close()
            except Exception:
                pass
    return None


def pid_file():
    return os.path.join(data_dir(), "watcher.pid")


def pid_alive(pid):
    """判断进程是否还活着。

    走系统 API（OpenProcess + GetExitCodeProcess），不启动任何子进程。
    少数进程（受保护/更高权限）拿不到句柄，这时用 tasklist 兜底确认，
    避免把"活着但查不到"误判成"已退出"——那会导致单实例锁失效、
    跑出两个后台同步进程。
    """
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                code = ctypes.c_ulong(0)
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
    except Exception:
        pass
    # 句柄拿不到：用 tasklist 兜底（只按 pid 过滤，不依赖权限）
    try:
        proc = subprocess.run(["tasklist", "/FI", "PID eq {}".format(pid), "/NH", "/FO", "CSV"],
                              capture_output=True, timeout=8, creationflags=CREATE_NO_WINDOW)
        text = proc.stdout.decode("gbk", "replace").lower()
        if str(pid) in text and "no tasks" not in text and "没有运行" not in text:
            return True
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def acquire_watcher_lock():
    """后台进程的独占锁：PID 文件 + 端口双保险。

    返回 (锁对象, 说明)。已被占用时锁对象为 None。
    判定过程写日志，方便排查"跑出两个后台进程"这类问题。
    """
    pf = pid_file()
    try:
        if os.path.exists(pf):
            with open(pf, "r", encoding="utf-8", errors="replace") as fh:
                old = (fh.read() or "").strip()
            if old.isdigit() and int(old) != os.getpid():
                alive = pid_alive(old)
                log("取锁检查：pid 文件记录 {}，该进程{}".format(
                    old, "仍在运行" if alive else "已退出（视为残留锁）"))
                if alive:
                    return None, "已有后台进程运行 (pid {})".format(old)
    except Exception as exc:
        log("取锁检查异常: {}".format(exc))

    lock = acquire_lock(WATCHER_LOCK_PORTS)
    if lock is None:
        return None, "后台进程锁被占用（端口 {} 全部在用）".format(
            "{}-{}".format(WATCHER_LOCK_PORTS[0], WATCHER_LOCK_PORTS[-1]))

    try:
        with open(pf, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except Exception:
        pass
    return lock, "ok"


def release_watcher_lock():
    try:
        pf = pid_file()
        if os.path.exists(pf):
            with open(pf, "r", encoding="utf-8", errors="replace") as fh:
                if (fh.read() or "").strip() == str(os.getpid()):
                    os.remove(pf)
    except Exception:
        pass


def _powershell():
    ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                      "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return ps if os.path.exists(ps) else "powershell.exe"


def _run_ps(command, timeout=60):
    """跑一段 PowerShell（UTF-16LE base64，避免中文/引号转义问题）。"""
    b64 = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", b64],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")
    except Exception as exc:
        return -1, "", str(exc)


def _run_ps_elevated(inner_command, timeout=240):
    """以管理员身份跑一段 PowerShell（按需弹 UAC）。返回 (成功?, 输出)。

    若当前会话不允许提权（Start-Process 直接失败），会很快返回 False，不会卡住。
    """
    b64 = base64.b64encode(inner_command.encode("utf-16-le")).decode("ascii")
    outer = ("$ErrorActionPreference='Stop'; "
             "try {{ $p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -PassThru -Wait "
             "-WindowStyle Hidden -ArgumentList '-NoProfile','-NonInteractive',"
             "'-ExecutionPolicy','Bypass','-EncodedCommand','{b64}'; exit $p.ExitCode }} "
             "catch {{ exit 2 }}").format(b64=b64)
    try:
        proc = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive",
                               "-ExecutionPolicy", "Bypass", "-Command", outer],
                              capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return proc.returncode == 0, (proc.stdout.decode("utf-8", "replace") +
                                      proc.stderr.decode("utf-8", "replace"))
    except subprocess.TimeoutExpired:
        return False, "提权操作超时（UAC 未响应）"
    except Exception as exc:
        return False, str(exc)


def _safe_print(text):
    """控制台可能不是 UTF-8，打印中文失败时不要让程序崩。"""
    try:
        print(text)
    except Exception:
        try:
            print(text.encode("utf-8", "replace").decode("ascii", "replace"))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 位置自愈：exe 被移动/改名后，自动把自启项改到新位置
# ---------------------------------------------------------------------------


def parse_launcher_target(value):
    """从自启项命令行里取出 exe 路径。

    支持带引号 `"C:\\path\\app.exe" --watch` 和不带引号两种写法。
    """
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith('"'):
        end = value.find('"', 1)
        return value[1:end] if end > 0 else ""
    # 不带引号：第一段就是 exe（路径本身不含空格的情况）
    return value.split(" ")[0].strip()


def _same_file(a, b):
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except Exception:
        return False


def autostart_locations():
    """列出所有"记着我们位置"的自启位置及其当前指向。"""
    info = {"registry": "", "shortcut": "", "task": ""}
    try:
        info["registry"] = parse_launcher_target(_reg_read(RUN_VALUE))
    except Exception:
        pass
    try:
        if os.path.exists(startup_shortcut_path()):
            info["shortcut"] = startup_shortcut_target()
    except Exception:
        pass
    try:
        code, out, _ = _run_ps(
            "try {{ (Get-ScheduledTask -TaskName '{t}' -ErrorAction Stop).Actions | "
            "Select-Object -First 1 -ExpandProperty Execute }} catch {{ }}".format(t=TASK_NAME))
        info["task"] = out.strip()
    except Exception:
        pass
    return info


def startup_shortcut_target():
    """读取启动文件夹快捷方式指向的目标。"""
    try:
        import struct  # noqa: F401  (仅为可读性，实际用 PowerShell COM)
    except Exception:
        pass
    lnk = startup_shortcut_path().replace("'", "''")
    code, out, _ = _run_ps(
        "$s = New-Object -ComObject WScript.Shell; "
        "$sc = $s.CreateShortcut('{lnk}'); $sc.TargetPath".format(lnk=lnk))
    return out.strip()


def self_heal_location(repair=True):
    """检查自启项是否指向"当前这个 exe"，不是就改过来。

    这是"随便挪位置也能用"的关键：exe 启动时（不管是双击开界面、还是开机自启
    拉起后台进程）都会跑一遍。只要程序能跑起来一次，记录的位置就会被修正。

    返回 (发现的问题列表, 已修复的操作列表)。
    """
    me = exe_path()
    found, fixed = [], []

    info = autostart_locations()
    old_reg = info["registry"]
    if old_reg and not _same_file(old_reg, me):
        found.append("注册表启动项指向 {}".format(old_reg))
        if repair:
            ok, _msg = enable_registry_autostart()
            if ok:
                fixed.append("注册表启动项 -> {}".format(me))

    if info["shortcut"] and not _same_file(info["shortcut"], me):
        found.append("启动文件夹快捷方式指向 {}".format(info["shortcut"]))
        if repair:
            ok, _msg = create_startup_shortcut()
            if ok:
                fixed.append("启动文件夹快捷方式 -> {}".format(me))

    if info["task"] and not _same_file(info["task"], me):
        # 计划任务的 ACL 属于提权账户，普通权限改不了，只报告不自动改
        found.append("计划任务指向 {}（需要管理员权限才能改，可在界面点一次"
                     "「停止同步」清掉）".format(info["task"]))

    if found:
        for line in found:
            log("位置自愈检查：{}".format(line))
        for line in fixed:
            log("位置自愈已修复：{}".format(line))
    return found, fixed


def _same_exe_process(pid):
    """判断该 pid 是不是"同一个 exe 文件"的进程（用于避免误杀别的程序）。"""
    try:
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                         wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                 int(pid))
        if not h:
            return False
        try:
            buf = ctypes.create_unicode_buffer(4096)
            size = wintypes.DWORD(len(buf))
            kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD)]
            if not kernel32.QueryFullProcessImageNameW(h, 0, buf,
                                                       ctypes.byref(size)):
                return False
            return os.path.normcase(os.path.abspath(buf.value)) == \
                os.path.normcase(os.path.abspath(exe_path()))
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return False


def running_watcher_pids():
    """返回后台监视进程的 pid（本 exe 的 --watch，以及旧版 pythonw 脚本）。

    **为什么不能只靠 CommandLine 匹配**：PyInstaller 单文件模式的子进程
    在 WMI 里 CommandLine 是**空**的。后台进程是用
    `DETACHED_PROCESS | CREATE_NO_WINDOW` 拉起的，实测
    `Get-CimInstance Win32_Process` 查到的 CommandLine 为空字符串，
    于是 `CommandLine -like '*--watch*'` 永远不成立 —— 后果是：
      * 界面把"已设置开机自启"误显示成「未开启」
      * 自动更新重启时杀不掉旧的后台进程，替换失败
    所以主路径改为读 watcher.pid（后台进程启动时本来就会写），
    再用"进程存活 + 确实是同一个 exe"两条校验兜底；
    CommandLine 匹配只作为旧版脚本的兼容分支保留。
    """
    me = os.getpid()
    pids = []

    # --- 主路径：watcher.pid ---
    try:
        pf = pid_file()
        if os.path.exists(pf):
            with open(pf, "r", encoding="utf-8", errors="replace") as fh:
                raw = (fh.read() or "").strip()
            if raw.isdigit():
                pid = int(raw)
                if pid != me and pid_alive(raw) and _same_exe_process(pid):
                    pids.append(pid)
    except Exception:
        pass

    # --- 兼容分支：旧版 pythonw 脚本 / 命令行里带 --watch 的情况 ---
    name = os.path.basename(exe_path()).replace("'", "''")
    code, out, _ = _run_ps(
        "Get-CimInstance Win32_Process | "
        "Where-Object {{ $_.CommandLine -like '*--watch*' -and "
        "($_.Name -eq '{name}' -or $_.Name -eq 'pythonw.exe') }} | "
        "ForEach-Object {{ $_.ProcessId }}".format(name=name))
    code2, out2, _ = _run_ps(
        "Get-CimInstance Win32_Process -Filter \"Name = 'pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -like '*nostation_watcher.py*' } | "
        "ForEach-Object { $_.ProcessId }")
    for token in (out + " " + out2).split():
        try:
            pid = int(token)
        except ValueError:
            continue
        if pid != me and pid not in pids:
            pids.append(pid)
    return pids


def stop_watchers():
    """结束后台监视进程（本 exe 的 --watch，以及旧版 pythonw 脚本）。"""
    stopped = []
    for pid in running_watcher_pids():
        _run_ps("Stop-Process -Id {} -Force -ErrorAction SilentlyContinue".format(pid))
        stopped.append(pid)
    _run_ps("Get-CimInstance Win32_Process -Filter \"Name = 'pythonw.exe'\" | "
            "Where-Object { $_.CommandLine -like '*nostation_watcher.py*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    return stopped


def stop_legacy_watchers():
    """只结束早期"脚本版"安装遗留的 pythonw 监视进程，避免和新版重复。"""
    code, out, _ = _run_ps(
        "Get-CimInstance Win32_Process -Filter \"Name = 'pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -like '*nostation_watcher.py*' } | "
        "ForEach-Object { $_.ProcessId }")
    stopped = []
    for token in out.split():
        try:
            pid = int(token)
        except ValueError:
            continue
        _run_ps("Stop-Process -Id {} -Force -ErrorAction SilentlyContinue".format(pid))
        stopped.append(pid)
    return stopped


# ---------------------------------------------------------------------------
# 自启动：注册表 Run 项（首选，零额外文件）/ 启动文件夹（备用）/ 计划任务（可选）
# ---------------------------------------------------------------------------

RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
RUN_KEY_WIN = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "NostationPal"
# 备份用的旧值名，安装过早期版本时一并清理
RUN_VALUE_LEGACY = "NostationSync"   # 旧版注册表值名，仍需清理


def startup_folder():
    return os.path.join(os.environ.get("APPDATA", ""),
                        "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def startup_shortcut_path():
    return os.path.join(startup_folder(), SHORTCUT_NAME)


def _reg_open(subkey_windows, write=False):
    """打开 HKCU 下的注册表键，返回句柄（失败返回 None）。

    直接用 Windows 注册表 API（advapi32），不经过 PowerShell —— 更快，而且不会
    遇到 PowerShell 5.1 把 "HKCU\\..." 当成相对文件路径解析的坑。
    """
    HKEY_CURRENT_USER = 0x80000001
    KEY_READ = 0x20019
    KEY_SET_VALUE = 0x0002
    access = KEY_READ | (KEY_SET_VALUE if write else 0)
    try:
        advapi = ctypes.windll.advapi32
        handle = ctypes.c_void_p()
        rc = advapi.RegOpenKeyExW(ctypes.c_void_p(HKEY_CURRENT_USER),
                                  ctypes.c_wchar_p(subkey_windows),
                                  0, access, ctypes.byref(handle))
        if rc != 0:
            return None
        return handle
    except Exception:
        return None


def _reg_open_run(write=False):
    return _reg_open(RUN_KEY_WIN, write)


def _reg_close(handle):
    try:
        ctypes.windll.advapi32.RegCloseKey(handle)
    except Exception:
        pass


def _reg_read(name):
    """读取 Run 键下某个值，返回字符串（不存在返回空串）。"""
    handle = _reg_open_run()
    if not handle:
        return ""
    try:
        advapi = ctypes.windll.advapi32
        size = ctypes.c_ulong(0)
        REG_SZ = 1
        rc = advapi.RegQueryValueExW(handle, ctypes.c_wchar_p(name), None, None,
                                     None, ctypes.byref(size))
        if rc != 0 or size.value == 0:
            return ""
        buf = ctypes.create_unicode_buffer(size.value // 2 + 2)
        rc = advapi.RegQueryValueExW(handle, ctypes.c_wchar_p(name), None,
                                     ctypes.byref(ctypes.c_ulong(REG_SZ)),
                                     buf, ctypes.byref(size))
        if rc != 0:
            return ""
        return buf.value
    except Exception:
        return ""
    finally:
        _reg_close(handle)


def _reg_write(name, value):
    handle = _reg_open_run(write=True)
    if not handle:
        return False
    try:
        advapi = ctypes.windll.advapi32
        REG_SZ = 1
        data = ctypes.c_wchar_p(value)
        size = (len(value) + 1) * ctypes.sizeof(ctypes.c_wchar)
        rc = advapi.RegSetValueExW(handle, ctypes.c_wchar_p(name), 0, REG_SZ,
                                   data, size)
        return rc == 0
    except Exception:
        return False
    finally:
        _reg_close(handle)


def _reg_delete(name):
    handle = _reg_open_run(write=True)
    if not handle:
        return False
    try:
        rc = ctypes.windll.advapi32.RegDeleteValueW(handle, ctypes.c_wchar_p(name))
        return rc == 0
    except Exception:
        return False
    finally:
        _reg_close(handle)


def _reg_list_values(subkey_windows):
    """列出某个 HKCU 键下的所有值名（键不存在返回空列表）。"""
    handle = _reg_open(subkey_windows)
    if not handle:
        return []
    advapi = ctypes.windll.advapi32
    names = []
    index = 0
    try:
        while True:
            buf = ctypes.create_unicode_buffer(512)
            size = ctypes.c_ulong(len(buf))
            rc = advapi.RegEnumValueW(handle, index, buf, ctypes.byref(size),
                                      None, None, None, None)
            if rc != 0:
                break
            if buf.value:
                names.append(buf.value)
            index += 1
    except Exception:
        pass
    finally:
        _reg_close(handle)
    return names


def _reg_read_at(subkey_windows, name):
    """读取任意 HKCU 子键下的一个值。"""
    handle = _reg_open(subkey_windows)
    if not handle:
        return ""
    try:
        advapi = ctypes.windll.advapi32
        size = ctypes.c_ulong(0)
        rc = advapi.RegQueryValueExW(handle, ctypes.c_wchar_p(name), None, None,
                                     None, ctypes.byref(size))
        if rc != 0 or size.value == 0:
            return ""
        buf = ctypes.create_unicode_buffer(size.value // 2 + 2)
        rc = advapi.RegQueryValueExW(handle, ctypes.c_wchar_p(name), None, None,
                                     buf, ctypes.byref(size))
        return buf.value if rc == 0 else ""
    except Exception:
        return ""
    finally:
        _reg_close(handle)


def _reg_delete_at(subkey_windows, name):
    handle = _reg_open(subkey_windows, write=True)
    if not handle:
        return False
    try:
        return ctypes.windll.advapi32.RegDeleteValueW(
            handle, ctypes.c_wchar_p(name)) == 0
    except Exception:
        return False
    finally:
        _reg_close(handle)


def _is_ours(value):
    """判断某个自启项的值是不是本程序留下的。"""
    text = (value or "").lower()
    if not text:
        return False
    markers = ["nostation", "nostationautosync", "nostationsync",
               "nostationpal",
               os.path.basename(exe_path()).lower()]
    return any(m in text for m in markers)


def run_value_exists(name=None):
    return bool(_reg_read(name or RUN_VALUE))


def registry_autostart_on():
    """注册表里是否已经有本程序的自启项（含指向旧路径的情况）。"""
    return bool(_reg_read(RUN_VALUE))


def registry_autostart_target():
    """注册表自启项当前指向的命令行（没有则为空串）。"""
    return _reg_read(RUN_VALUE)


def enable_registry_autostart():
    """写 HKCU\\...\\Run：登录即自动运行，且不产生任何额外文件。

    这是免安装程序的标准做法，真正意义上的"就一个 exe"。
    """
    cmd = '"{}" --watch'.format(exe_path())
    if not _reg_write(RUN_VALUE, cmd):
        return False, "写入注册表启动项失败（可能被安全策略禁止）"
    if _reg_read(RUN_VALUE) != cmd:
        return False, "注册表启动项写入后校验不一致"
    return True, "已写入注册表启动项（登录自动运行，不产生任何额外文件）"


def disable_registry_autostart():
    removed = False
    for name in (RUN_VALUE, RUN_VALUE_LEGACY):
        if _reg_read(name):
            _reg_delete(name)
            removed = True
    return removed


def create_startup_shortcut():
    """在「启动」文件夹放一个快捷方式（备用方案，某些策略会禁用 Run 键）。"""
    try:
        os.makedirs(startup_folder(), exist_ok=True)
        lnk = startup_shortcut_path().replace("'", "''")
        target = exe_path().replace("'", "''")
        cwd = os.path.dirname(exe_path()).replace("'", "''")
        cmd = (
            "$s = New-Object -ComObject WScript.Shell; "
            "$sc = $s.CreateShortcut('{lnk}'); "
            "$sc.TargetPath = '{target}'; "
            "$sc.Arguments = '--watch'; "
            "$sc.WorkingDirectory = '{cwd}'; "
            "$sc.WindowStyle = 7; "
            "$sc.Description = 'Nostation 开机自动校时'; "
            "$sc.Save()"
        ).format(lnk=lnk, target=target, cwd=cwd)
        code, _, err = _run_ps(cmd)
        if code == 0 and os.path.exists(startup_shortcut_path()):
            return True, "已加入登录启动项"
        return False, "创建启动项失败: {}".format(err.strip() or code)
    except Exception as exc:
        return False, "创建启动项异常: {}".format(exc)


def remove_startup_shortcut():
    try:
        if os.path.exists(startup_shortcut_path()):
            os.remove(startup_shortcut_path())
            return True
    except Exception:
        pass
    return False


def task_exists():
    code, out, _ = _run_ps("if (Get-ScheduledTask -TaskName '{}' -ErrorAction SilentlyContinue) "
                           "{{ 'yes' }}".format(TASK_NAME))
    return "yes" in out


def register_task_elevated():
    """注册计划任务（会弹一次 UAC）。返回 (成功?, 说明)。"""
    target = exe_path().replace("'", "''")
    cwd = os.path.dirname(exe_path()).replace("'", "''")
    inner = (
        "$ErrorActionPreference='Stop'; "
        "$a = New-ScheduledTaskAction -Execute '{target}' -Argument '--watch' "
        "-WorkingDirectory '{cwd}'; "
        "$t = New-ScheduledTaskTrigger -AtLogOn -User \"$env:USERDOMAIN\\$env:USERNAME\"; "
        "$t.Delay = 'PT10S'; "
        "$b = New-ScheduledTaskTrigger -AtStartup; "
        "$b.Delay = 'PT1M'; "
        "$p = New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\\$env:USERNAME\" "
        "-LogonType Interactive -RunLevel Highest; "
        "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-StartWhenAvailable -MultipleInstances IgnoreNew -Hidden "
        "-ExecutionTimeLimit ([TimeSpan]::Zero); "
        "Register-ScheduledTask -TaskName '{task}' -Action $a -Trigger $t,$b -Principal $p "
        "-Settings $s -Force | Out-Null; 'OK'"
    ).format(target=target, cwd=cwd, task=TASK_NAME)

    ok, out = _run_ps_elevated(inner)
    if task_exists():
        return True, "已注册计划任务（开机/登录自动运行）"
    if not ok:
        return False, "计划任务未注册（UAC 未授权或失败）"
    return False, "计划任务注册失败: {}".format(out.strip()[:120])


def remove_task_elevated():
    inner = ("$ErrorActionPreference='SilentlyContinue'; "
             "Stop-ScheduledTask -TaskName '{task}'; "
             "Unregister-ScheduledTask -TaskName '{task}' -Confirm:$false; 'OK'").format(task=TASK_NAME)
    _run_ps_elevated(inner)
    if task_exists():
        _run_ps("Unregister-ScheduledTask -TaskName '{}' -Confirm:$false "
                "-ErrorAction SilentlyContinue".format(TASK_NAME))
    return not task_exists()


def start_watcher():
    """启动后台监视进程（无窗口）。已经在跑就不重复启动。"""
    try:
        existing = running_watcher_pids()
    except Exception:
        existing = []
    if existing:
        log("后台进程已在运行（{}），不重复启动".format(
            ", ".join(str(p) for p in existing)))
        return True
    if getattr(sys, "frozen", False):
        args = [exe_path(), "--watch"]
        flags = CREATE_NO_WINDOW | DETACHED_PROCESS
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else sys.executable
        args = [exe, exe_path(), "--watch"]
        flags = CREATE_NO_WINDOW
    try:
        subprocess.Popen(args, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except Exception as exc:
        log("启动后台进程失败: {}".format(exc))
        return False


def autostart_active():
    """是否已配置任何一种开机自启。"""
    return (registry_autostart_on() or task_exists()
            or os.path.exists(startup_shortcut_path()))


# ---------------------------------------------------------------------------
# 「停止同步」：全量清理开机自启，并逐项核验不留残留
# ---------------------------------------------------------------------------

# Windows 用来记录"任务管理器里启动项开关状态"的键（Run 值删掉后这里也可能留痕）
STARTUP_APPROVED_KEYS = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run",
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32",
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder",
)
RUN_KEYS = (
    r"Software\Microsoft\Windows\CurrentVersion\Run",
    r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
    r"Software\Microsoft\Windows\CurrentVersion\RunServices",
)


def find_autostart_traces(only_unwanted=False):
    """扫描所有可能藏启动项的位置，返回属于本程序的痕迹清单。

    only_unwanted=True 时只报"不该存在的残留"：
      · 指向别处的旧启动项、重复项
      · 启动文件夹快捷方式、计划任务、仍在跑的进程
    正在使用的那一条注册表自启项（指向当前 exe）不算残留，
    否则每次正常状态都会报一堆"残留"，反而看不清真正的问题。
    """
    traces = []
    me = exe_path()

    for key in RUN_KEYS:
        for name in _reg_list_values(key):
            value = _reg_read_at(key, name)
            if not (_is_ours(value) or name in (RUN_VALUE, RUN_VALUE_LEGACY)):
                continue
            if only_unwanted:
                # 当前命名的那条自启项属于"正在使用"，不算残留
                # （路径不对会被"位置自愈"修正，那是另一回事）
                if key == RUN_KEY_WIN and name == RUN_VALUE:
                    continue
                # 指向的文件已不存在的，才叫残留
                target = parse_launcher_target(value)
                if target and os.path.exists(target):
                    continue
            traces.append("注册表 HKCU\\{} 的值 {!r} = {!r}".format(key, name, value))

    for key in STARTUP_APPROVED_KEYS:
        for name in _reg_list_values(key):
            if name in (RUN_VALUE, RUN_VALUE_LEGACY) or _is_ours(name):
                if only_unwanted:
                    continue    # 只是任务管理器里的开关记录，跟着 Run 项一起清，不算残留
                traces.append("注册表 HKCU\\{} 的值 {!r}".format(key, name))

    try:
        for entry in os.listdir(startup_folder()):
            path = os.path.join(startup_folder(), entry)
            low = entry.lower()
            if "nostation" in low:
                traces.append("启动文件夹: {}".format(path))
            elif low.endswith(".lnk"):
                target = startup_shortcut_target_of(path)
                if _is_ours(target):
                    traces.append("启动文件夹: {} -> {}".format(path, target))
    except Exception:
        pass

    try:
        info = autostart_locations()
        if info.get("task") and not (only_unwanted and _same_file(info["task"], me)):
            traces.append("计划任务 {} -> {}".format(TASK_NAME, info["task"]))
    except Exception:
        pass

    try:
        pids = running_watcher_pids()
        if pids:
            configured = bool(_reg_read(RUN_VALUE)) or os.path.exists(startup_shortcut_path())
            if only_unwanted and configured:
                # 自启项还在、后台进程正常跑着 —— 这是正常工作状态，不是残留
                pass
            else:
                traces.append("还在运行的后台进程: {}".format(
                    ", ".join(str(p) for p in pids)))
    except Exception:
        pass

    return traces


def startup_shortcut_target_of(lnk_path):
    """读取任意一个 .lnk 指向的目标（用于扫描启动文件夹）。"""
    lnk = lnk_path.replace("'", "''")
    code, out, _ = _run_ps(
        "$s = New-Object -ComObject WScript.Shell; "
        "$sc = $s.CreateShortcut('{lnk}'); $sc.TargetPath".format(lnk=lnk))
    return out.strip()


def clean_all_autostart():
    """把本程序留下的所有开机自启痕迹清干净。

    返回 (动作说明列表, 残留清单)。残留清单为空才算"完全清理干净"。
    """
    actions = []

    # 1) 注册表 Run / RunOnce / StartupApproved
    for key in RUN_KEYS:
        removed = []
        for name in _reg_list_values(key):
            value = _reg_read_at(key, name)
            if _is_ours(value) or name in (RUN_VALUE, RUN_VALUE_LEGACY):
                if _reg_delete_at(key, name):
                    removed.append(name)
        if removed:
            actions.append("已删除注册表项 HKCU\\{} 的 {}".format(key, ", ".join(removed)))
    for key in STARTUP_APPROVED_KEYS:
        removed = []
        for name in _reg_list_values(key):
            if name in (RUN_VALUE, RUN_VALUE_LEGACY) or _is_ours(name):
                if _reg_delete_at(key, name):
                    removed.append(name)
        if removed:
            actions.append("已清理启动项管理记录 {}".format(", ".join(removed)))
    if not any("Run" in a for a in actions):
        actions.append("注册表启动项：本来就没有")

    # 2) 启动文件夹快捷方式（含名字被改过的）
    removed_lnk = []
    try:
        for entry in os.listdir(startup_folder()):
            path = os.path.join(startup_folder(), entry)
            low = entry.lower()
            ours = "nostation" in low
            if not ours and low.endswith(".lnk"):
                ours = _is_ours(startup_shortcut_target_of(path))
            if ours:
                try:
                    os.remove(path)
                    removed_lnk.append(entry)
                except Exception:
                    pass
    except Exception:
        pass
    actions.append("已删除启动文件夹快捷方式 {}".format(", ".join(removed_lnk))
                   if removed_lnk else "启动文件夹：本来就没有")

    # 3) 计划任务（ACL 属于提权账户，需要 UAC）
    if task_exists():
        if remove_task_elevated():
            actions.append("已注销计划任务")
        else:
            actions.append("计划任务未能注销（需要管理员权限）")
    else:
        actions.append("计划任务：本来就没有")

    # 4) 后台进程
    stopped = stop_watchers()
    actions.append("已结束后台进程 {}".format(", ".join(str(p) for p in stopped))
                   if stopped else "后台进程：本来就没在运行")

    # 5) 早期脚本版安装的文件残留
    legacy_files = ["start-watcher.vbs", "nostation_watcher.py", "nostation_sync.py",
                    "install.ps1", "uninstall.ps1", "move_to_fixed_dir.ps1",
                    "put_on_desktop.ps1", "make_portable.ps1", "amk_probe.py",
                    "vial_definition.py", "watcher.pid"]
    removed_files = []
    for name in legacy_files:
        path = os.path.join(data_dir(), name)
        if os.path.exists(path):
            try:
                os.remove(path)
                removed_files.append(name)
            except Exception:
                pass
    pycache = os.path.join(data_dir(), "__pycache__")
    if os.path.isdir(pycache):
        import shutil
        try:
            shutil.rmtree(pycache, ignore_errors=True)
            removed_files.append("__pycache__")
        except Exception:
            pass
    if removed_files:
        actions.append("已清理旧版脚本残留: {}".format(", ".join(removed_files)))

    # 6) 核验
    traces = find_autostart_traces(only_unwanted=True)
    return actions, traces


# ---------------------------------------------------------------------------
# 后台监视模式（--watch）
# ---------------------------------------------------------------------------


def watch_loop(interval=3.0, refresh=1800.0):
    lock, why = acquire_watcher_lock()
    if lock is None:
        log("未启动：{}".format(why))
        return 0
    # 本次运行清空日志（拿到锁之后才做，避免第二个实例把日志清掉就跑）
    reset_log("后台同步")
    log("后台模式启动 (pid {}, interval {}, refresh {})".format(os.getpid(), interval, refresh))
    log("后台同步进程启动 (pid {})".format(os.getpid()))

    # 挂上系统关机通知：关机时主动、干净地退出，避免被 Windows 强杀后
    # 临时目录残留、以及 bootloader 弹出
    # 「Failed to remove temporary directory」警告框。
    install_shutdown_handler()

    try:
        known = {d["path"] for d in pick_targets()}
    except Exception as exc:
        log("枚举设备失败: {}".format(exc))
        known = set()

    last_sync = 0.0
    if known:
        log("启动时已检测到 hub，立即校时")
        sync_now()
        last_sync = time.time()

    while not shutdown_requested():
        # 用 Event.wait 代替 time.sleep：关机通知一到就能立刻醒来退出，
        # 不必等满一个轮询间隔（否则仍可能落在关机窗口里被强杀）。
        if _SHUTDOWN_EVENT.wait(interval):
            break
        try:
            now = {d["path"] for d in pick_targets()}
        except Exception as exc:
            log("轮询异常: {}".format(exc))
            continue

        # 未检测到连接：不做任何写入动作，安静等待设备接入
        if not now:
            if known:
                log("未检测到连接（设备已断开），已暂停校时，等待设备接入")
            known = set()
            continue

        if now - known:
            log("检测到设备连接，开始校时")
            for _attempt in range(3):
                ok_count, total, _details = sync_now()
                if total and ok_count == total:
                    break
                time.sleep(1.5)
            last_sync = time.time()
            try:
                now = {d["path"] for d in pick_targets()}
            except Exception:
                pass
        known = now
        if refresh and (time.time() - last_sync) >= refresh:
            log("定时刷新校时")
            sync_now()
            last_sync = time.time()

    log("收到退出请求，后台同步进程结束")
    return 0


# ---------------------------------------------------------------------------
# 系统关机/注销通知：让常驻的后台进程能"优雅退出"
# ---------------------------------------------------------------------------
# 为什么需要这个：
#   后台同步进程（--watch）是开机自启、长期常驻的。关机时 Windows 会强制结束它，
#   它来不及做任何收尾；而 PyInstaller 单文件模式的 bootloader 在退出时还要删除
#   自己的临时解压目录（%TEMP%\_MEIxxxx，约 30 MB）。被强杀时这一步做不了，
#   于是残留一份 30 MB 的副本，而且 bootloader 会在事后弹出
#   「Failed to remove temporary directory」警告框。
#
#   解决办法：给进程挂一个隐藏的 message-only 窗口，接收
#   WM_QUERYENDSESSION / WM_ENDSESSION，在关机流程里主动、干净地退出。
#   这样 bootloader 就有机会把临时目录删掉，既不残留也不弹框。
#
# 实现在独立线程里跑消息循环（主线程在做同步轮询），通过一个 Event 通知主循环。

_SHUTDOWN_EVENT = threading.Event()
_SHUTDOWN_HWND = [None]          # 用 list 存，便于回调里写


def _shutdown_handler_installed():
    return _SHUTDOWN_HWND[0] is not None


def shutdown_requested():
    """后台循环用它判断是否该退出了。"""
    return _SHUTDOWN_EVENT.is_set()


def install_shutdown_handler():
    """挂上关机通知窗口。失败不影响主功能（最坏就是回到被强杀的老样子）。"""
    if _shutdown_handler_installed():
        return True
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # 必须显式声明签名：LPARAM 是有符号指针宽度整数，
        # 不声明的话 ctypes 会把系统消息里的原始指针当 Python int 转换，
        # 抛 OverflowError（"int too long to convert"），窗口过程直接失效。
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_long
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.PostMessageW.argtypes = [
            wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]

        WM_QUERYENDSESSION = 0x0011
        WM_ENDSESSION = 0x0016
        HWND_MESSAGE = wintypes.HWND(-3)

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long, wintypes.HWND, ctypes.c_uint,
            wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [
                ("style", ctypes.c_uint),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        def proc(hwnd, msg, wparam, lparam):
            if msg == WM_QUERYENDSESSION:
                # 允许关机继续；同时记下我们该退出了
                _SHUTDOWN_EVENT.set()
                log("收到系统关机通知，准备优雅退出")
                return 1
            if msg == WM_ENDSESSION:
                _SHUTDOWN_EVENT.set()
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        cls_name = "NostationPalShutdownSink"
        ready = threading.Event()
        result = [False]

        def sink_thread():
            """在**本线程**里建窗并跑消息循环。

            这是必须的：Windows 把窗口消息投递给"创建该窗口的线程"的消息队列，
            所以建窗和 GetMessage 必须在同一个线程里。之前把建窗放在主线程、
            消息循环放在子线程，结果 PostMessage 发过去永远收不到
            （SendMessage 跨线程倒是能跑，但系统关机走的是 PostMessage 路径）。
            """
            try:
                wc = WNDCLASS()
                wc.lpfnWndProc = WNDPROC(proc)   # 引用要保住，函数内局部即可
                wc.lpszClassName = cls_name
                wc.hInstance = kernel32.GetModuleHandleW(None)
                if not user32.RegisterClassW(ctypes.byref(wc)):
                    err = kernel32.GetLastError()
                    if err != 1410:              # 1410 = 类已注册，可继续
                        log("注册关机通知窗口失败（错误 {}）".format(err))
                        ready.set()
                        return
                hwnd = user32.CreateWindowExW(
                    0, cls_name, cls_name, 0, 0, 0, 0, 0, HWND_MESSAGE, None,
                    wc.hInstance, None)
                if not hwnd:
                    log("创建关机通知窗口失败（错误 {}）".format(
                        kernel32.GetLastError()))
                    ready.set()
                    return
                globals()["_SHUTDOWN_WNDPROC_REF"] = wc.lpfnWndProc
                _SHUTDOWN_HWND[0] = hwnd
                result[0] = True
                ready.set()

                msg = wintypes.MSG()
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            except Exception as exc:
                log("关机通知线程异常: {}".format(exc))
                ready.set()

        threading.Thread(target=sink_thread, name="shutdown-sink",
                         daemon=True).start()
        ready.wait(3.0)
        if not result[0]:
            return False
        log("已挂上系统关机通知（关机时可优雅退出，不再残留临时目录）")
        return True
    except Exception as exc:
        log("安装关机通知失败: {}".format(exc))
        return False


# ---------------------------------------------------------------------------
# 界面
# ---------------------------------------------------------------------------


def setup_dpi():
    """让窗口在高分屏上大小合适、文字清晰。

    必须在创建 Tk 窗口之前调用：Windows 默认把进程当 DPI 不感知，
    窗口会被系统拉伸放大导致模糊；而 PyInstaller 打包的 exe 又常常
    自带 DPI 感知清单，Tk 于是按 DPI 缩放，窗口显得很小。这里统一成
    "按显示器感知" 并按实际 DPI 计算窗口像素尺寸。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return "per-monitor"
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return "system"
        except Exception:
            return "none"


def fit_window(root, width=640, height=500):
    """按当前 DPI 计算合适的窗口像素尺寸，并让 Tk 字号随 DPI 缩放。"""
    try:
        dpi = root.winfo_fpixels("1i")
    except Exception:
        dpi = 96.0
    scale = max(1.0, dpi / 96.0)
    try:
        root.tk.call("tk", "scaling", scale * 1.3333)
    except Exception:
        pass
    w = min(int(width * scale), max(480, int(root.winfo_screenwidth() * 0.9)))
    h = min(int(height * scale), max(360, int(root.winfo_screenheight() * 0.9)))
    root.geometry("{}x{}".format(w, h))
    root.minsize(int(560 * scale), int(420 * scale))


class App:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.root = root
        root.title("{}  v{}    ·    By {}  ·    {}".format(
            APP_TITLE, APP_VERSION, AUTHOR, COPYRIGHT_YEAR))
        fit_window(root, 640, 520)
        # 窗口图标由 run_gui() 在建窗后立刻设置（那里设置完才显示窗口，
        # 避免先闪一下默认图标）；这里不再重复设置。
        self.build_menu()
        log("界面启动 v{} (作者 {}, dpi={:.0f}, win={})".format(
            APP_VERSION, AUTHOR, root.winfo_fpixels("1i"), root.winfo_geometry()))

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)

        box = ttk.LabelFrame(outer, text=" 当前状态 ", padding=10)
        box.pack(fill="x")

        self.lamp = tk.Canvas(box, width=16, height=16, highlightthickness=0)
        self.lamp.grid(row=0, column=0, rowspan=2, padx=(2, 8))
        self.lamp_id = self.lamp.create_oval(2, 2, 14, 14, fill="#c0392b", outline="")

        self.hub_label = ttk.Label(box, text="设备：检测中…",
                                   font=("Microsoft YaHei UI", 10, "bold"))
        self.hub_label.grid(row=0, column=1, sticky="w")
        self.auto_label = ttk.Label(box, text="开机自动同步：检测中…",
                                    font=("Microsoft YaHei UI", 10))
        self.auto_label.grid(row=1, column=1, sticky="w", pady=(4, 0))
        box.columnconfigure(1, weight=1)

        # 未检测到连接时给一行醒目提示
        self.warn_label = ttk.Label(box, text="", foreground="#c0392b",
                                    font=("Microsoft YaHei UI", 10, "bold"))
        self.warn_label.grid(row=2, column=1, sticky="w", pady=(6, 0))

        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=(12, 4))
        self.btn_start = ttk.Button(btns, text="开启 Nostation 开机同步",
                                    command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btns, text="停止同步", command=self.on_stop)
        self.btn_stop.pack(side="left", padx=8)
        self.btn_sync = ttk.Button(btns, text="立即同步一次", command=self.on_sync_once)
        self.btn_sync.pack(side="left")
        self.btn_refresh = ttk.Button(btns, text="重新检查", command=self.refresh)
        self.btn_refresh.pack(side="left", padx=8)
        ttk.Button(btns, text="关于", command=self.on_about).pack(side="right")

        # 每个按钮是干什么的 —— 放在按钮正下方，点一下就有说明
        tip = ttk.Frame(outer)
        tip.pack(fill="x", pady=(0, 8))
        # 先放右边的按钮并 pack，再放左边文字，避免文字过长把按钮挤没
        ttk.Button(tip, text="怎么用", command=self.on_help).pack(side="right")
        ttk.Label(
            tip,
            text="第一次用只需点最左边的「开启 Nostation 开机同步」。",
            foreground="#5a6673", font=("Microsoft YaHei UI", 9),
        ).pack(side="left")

        # 需要 hub 在线才能用的按钮（「停止同步」不在此列，断连时也要能停）
        self.hub_buttons = (self.btn_start, self.btn_sync)
        self.hub_online = None      # None=还没检测过
        self.flash_until = 0.0      # 刚断连时让提示闪烁几秒

        logbox = ttk.LabelFrame(outer, text=" 运行记录 ", padding=8)
        logbox.pack(fill="both", expand=True)
        self.text = tk.Text(logbox, height=10, wrap="none", font=("Consolas", 9),
                            background="#fbfbfb", relief="flat")
        scroll = ttk.Scrollbar(logbox, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set, state="disabled")
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.status = ttk.Label(outer, text="正在检查设备与同步状态…", foreground="#555")
        self.status.pack(fill="x", pady=(8, 0))
        ttk.Separator(outer, orient="horizontal").pack(fill="x", pady=(6, 4))
        self.footer = ttk.Label(
            outer,
            text="{}   |   By {}  ·  {}".format(COPYRIGHT_CN, AUTHOR, COPYRIGHT_YEAR),
            foreground="#98a2ad",
            font=("Microsoft YaHei UI", 8))
        self.footer.pack(fill="x", side="bottom")
        self.hint = ttk.Label(
            outer,
            text="关闭窗口不会停止同步；要停止请点「停止同步」。",
            foreground="#98a2ad",
            font=("Microsoft YaHei UI", 8))
        self.hint.pack(fill="x", side="bottom", pady=(0, 2))

        log("界面启动 v{}".format(APP_VERSION))
        # 注意：这里不要调 refresh()/tick()。tick() 里的 check_hub()
        # 会同步做一次设备枚举（本机 1.1~1.5 秒），放在构造函数里就等于
        # 卡在"窗口显示之前"。这两个由 run_gui() 在窗口显示后再启动。

    # ---------------------------------------------------------------- 工具 --
    def build_menu(self):
        """菜单栏：关于 / 许可 / 日志 / 退出（让程序看起来更正式）。"""
        tk = self.tk
        menubar = tk.Menu(self.root)
        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="怎么用（每个按钮是干什么的）", command=self.on_help)
        m_help.add_separator()
        m_help.add_command(label="检查更新", command=self.on_check_update)
        m_help.add_command(label="版本历史", command=self.on_changelog)
        self.var_autocheck = tk.BooleanVar(
            value=load_config().get("auto_check_update", True) is not False)
        m_help.add_checkbutton(label="启动时自动检查更新",
                               variable=self.var_autocheck,
                               command=self.on_toggle_autocheck)
        m_help.add_separator()
        m_help.add_command(label="关于 {}".format(APP_TITLE), command=self.on_about)
        m_help.add_command(label="查看许可条款", command=self.on_license)
        m_help.add_separator()
        m_help.add_command(label="未检测到连接时怎么办", command=self.on_show_disconnected)
        m_help.add_command(label="打开日志文件", command=self.on_open_log)
        m_help.add_command(label="打开程序所在目录", command=self.on_open_dir)
        menubar.add_cascade(label="帮助(H)", menu=m_help)
        try:
            self.root.config(menu=menubar)
        except Exception:
            pass

    def on_show_disconnected(self):
        from tkinter import messagebox
        messagebox.showinfo(
            "未检测到连接",
            "未检测到连接。\n\n"
            "界面与后台同步的工作方式：\n"
            "· 检测不到 NOSTATION 时，「开启同步」与「立即同步一次」按钮置灰不可点；\n"
            "· 后台同步进程会安静等待，不做任何写入动作；\n"
            "· 设备重新插上后 3 秒内自动恢复，无需任何操作；\n"
            "· 「停止同步」按钮始终可用（设备不在手边也能关掉开机自启）。",
            parent=self.root)

    def set_status(self, msg):
        self.status.configure(text=msg)

    def append_log(self, msg):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.text.configure(state="normal")
        self.text.insert("end", "{}  {}\n".format(stamp, msg))
        self.text.see("end")
        self.text.configure(state="disabled")

    def reload_log(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", read_tail(log_path(), 200))
        self.text.see("end")
        self.text.configure(state="disabled")

    def run_async(self, work, done):
        def wrapper():
            try:
                result = work()
            except Exception as exc:
                result = (False, "异常: {}".format(exc))
                log("操作异常: {}".format(traceback.format_exc()))
            try:
                self.root.after(0, lambda: done(result))
            except Exception:
                pass
        threading.Thread(target=wrapper, daemon=True).start()

    def busy(self, busy):
        state = "disabled" if busy else "normal"
        for btn in (self.btn_start, self.btn_stop, self.btn_sync, self.btn_refresh):
            btn.configure(state=state)
        if not busy:
            self._apply_hub_gate()   # 恢复时按 hub 在线情况决定可用性

    def _apply_hub_gate(self):
        """未检测到连接时，与设备相关的按钮置灰不可点。

        「停止同步」刻意不置灰：设备拔了/收起来了，仍然要能关掉开机自启。
        """
        online = bool(self.hub_online)
        for btn in self.hub_buttons:
            btn.configure(state="normal" if online else "disabled")
        if online:
            self.warn_label.configure(text="")
        else:
            self.warn_label.configure(text="未检测到连接")

    # ------------------------------------------------------------ 状态刷新 --
    def refresh(self, manual=True):
        """重新检查全部状态（比自动轮询查得更全）。

        自动轮询（tick，每 3 秒）只看设备插拔和日志；这里还会重新核对
        开机自启、启动文件夹、开机前任务、后台进程。

        manual=True（用户点了「重新检查」）时把结果写进运行记录 ——
        否则用户点完看不到任何反馈，会以为按钮没反应。
        manual=False（程序启动时自动跑一次）不写「手动重新检查」那行，
        避免把自动刷新误标成用户操作（这个 bug 1.7.0 版本出现过）。
        """
        self.set_status("正在重新检查…" if manual else "正在检查设备与同步状态…")
        self.run_async(self._collect_state,
                       self._on_state_refreshed if manual else self._apply_state)

    def _on_state_refreshed(self, state):
        self._apply_state(state)
        if not isinstance(state, dict):
            return
        items = []
        items.append("✓ 设备已连接" if state.get("hub") else "✗ 未检测到设备")
        if state.get("registry"):
            items.append("✓ 开机自启" if state.get("registry_ok")
                         else "✗ 开机自启(路径已失效)")
        else:
            items.append("✗ 开机自启未设置")
        items.append("✓ 启动文件夹项" if state.get("shortcut") else "— 启动文件夹无项")
        items.append("✓ 开机前校时任务" if state.get("task") else "— 无开机前校时任务")
        items.append("✓ 后台同步运行中" if state.get("watcher") else "✗ 后台同步未运行")
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = "手动重新检查（{}）：{}".format(stamp, " · ".join(items))
        self.append_log(line)
        log("界面：" + line)
        detail = state.get("hub_name") or ""
        if detail:
            self.append_log("　设备：{}".format(detail))
        extra = []
        if state.get("task"):
            extra.append("已注册「开机前校时」计划任务")
        if state.get("shortcut"):
            extra.append("启动文件夹里有快捷方式")
        if not state.get("watcher"):
            extra.append("后台同步未运行，点「开启开机同步」可重新拉起")
        if extra:
            self.append_log("　" + "；".join(extra))

    def _collect_state(self):
        targets = []
        try:
            targets = pick_targets()
        except Exception:
            pass
        try:
            reg_target = registry_autostart_target()
        except Exception:
            reg_target = ""
        return {
            "hub": bool(targets),
            "hub_name": describe(targets[0]) if targets else "",
            "hub_brief": hub_brief() if targets else "",
            "registry": bool(reg_target),
            "registry_ok": bool(reg_target) and exe_path().lower() in reg_target.lower(),
            "task": task_exists(),
            "shortcut": os.path.exists(startup_shortcut_path()),
            "watcher": bool(running_watcher_pids()),
        }

    def _apply_state(self, state):
        if not isinstance(state, dict):
            return
        running = state["watcher"]
        self.lamp.itemconfigure(self.lamp_id, fill="#27ae60" if running else "#c0392b")

        if state["hub"]:
            brief = state.get("hub_brief") or "NOSTATION"
            self.hub_label.configure(text="设备：已连接（{}）".format(brief),
                                     foreground="#1e8449")
        else:
            self.hub_label.configure(text="设备：未检测到 NOSTATION（请检查 USB 连接）",
                                     foreground="#c0392b")

        how = []
        if state["registry"]:
            how.append("开机自启" if state["registry_ok"] else "开机自启(路径已失效，运行一次即可自动修正)")
        if state["shortcut"]:
            how.append("启动文件夹")
        if state["task"]:
            how.append("开机前校时任务")
        configured = bool(how)

        if running:
            self.auto_label.configure(
                text="开机自动同步：已开启（{}）· 后台同步中".format(
                    " + ".join(how) or "仅后台进程在运行"),
                foreground="#1e8449")
        elif configured:
            # 已经设好自启、只是此刻后台进程没在跑：不应显示成"未开启"，
            # 否则用户会以为按钮没生效（这正是之前的一个 bug）。
            self.auto_label.configure(
                text="开机自动同步：已开启（{}）· 后台进程未运行（点「重新检查」或重启即可）".format(
                    " + ".join(how)),
                foreground="#b9770e")
        else:
            self.auto_label.configure(
                text="开机自动同步：未开启", foreground="#c0392b")

        # 与设备相关的操作按钮：没连上就置灰
        self.hub_online = state["hub"]
        self._apply_hub_gate()

        if state["hub"]:
            self.set_status("状态正常  ·  免安装单文件，删除程序即可完全卸载")
        else:
            self.set_status("未检测到连接：请确认 NOSTATION 已通过 USB 连接到本机  ·  "
                            "已自动暂停校时")

    def check_hub(self):
        """后台轻量轮询：实时反映设备的插拔。"""
        try:
            online = hub_present()
        except Exception:
            online = False
        if self.hub_online is None:
            self.hub_online = online
            if not online:
                log("界面：未检测到 NOSTATION 连接")
            self._apply_hub_gate()
            return
        if online != self.hub_online:
            self.hub_online = online
            if online:
                self.append_log("检测到 NOSTATION 已连接")
                log("界面：检测到 NOSTATION 已连接")
                self.set_status("检测到设备已连接，自动恢复校时功能")
            else:
                self.append_log("未检测到连接（设备已断开），操作按钮已置灰")
                log("界面：未检测到连接，操作按钮已置灰")
                self.flash_until = time.time() + 12
                self.set_status("未检测到连接：请确认 NOSTATION 已通过 USB 连接到本机")
            self._apply_hub_gate()

    def tick(self):
        try:
            self.reload_log()
        except Exception:
            pass
        try:
            self.check_hub()
        except Exception:
            pass
        # 刚断连时让提示闪一下，避免被忽略
        try:
            if self.flash_until > time.time():
                on = int(time.time() * 2) % 2 == 0
                self.warn_label.configure(foreground="#c0392b" if on else "#e8a3a3")
            else:
                self.warn_label.configure(foreground="#c0392b")
        except Exception:
            pass
        try:
            self.root.after(3000, self.tick)
        except Exception:
            pass

    # -------------------------------------------------------------- 按钮 --
    def _require_hub(self):
        """所有需要设备在线的操作都先过这一关。返回 True 表示可以继续。"""
        if hub_present():
            return True
        from tkinter import messagebox
        self.hub_online = False
        self._apply_hub_gate()
        self.append_log("未检测到连接，操作已取消")
        self.set_status("未检测到连接：请确认 NOSTATION 已通过 USB 连接到本机")
        messagebox.showwarning(
            "未检测到连接",
            "未检测到连接。\n\n"
            "请确认 NOSTATION 已通过 USB 连接到本机，"
            "再接好线后点「刷新状态」或重新操作。",
            parent=self.root)
        return False

    def on_start(self):
        if not self._require_hub():
            return
        self.busy(True)
        self.set_status("正在开启开机同步…")
        self.append_log("开始配置开机同步")

        def work():
            msgs = []
            # 首选：注册表 Run 项。零额外文件，真正的单文件免安装。
            ok_reg, msg_reg = enable_registry_autostart()
            msgs.append(msg_reg)
            if ok_reg:
                # 注册表生效后，清掉早期版本留在启动文件夹里的快捷方式，避免重复
                if remove_startup_shortcut():
                    msgs.append("已移除启动文件夹里的旧快捷方式（改用注册表启动项）")
            else:
                # 备用方案：注册表 Run 键被组策略禁用时，退回启动文件夹快捷方式
                ok_sc, msg_sc = create_startup_shortcut()
                msgs.append(msg_sc)
            legacy = stop_legacy_watchers()
            if legacy:
                msgs.append("已结束旧版脚本同步进程 {}".format(", ".join(str(p) for p in legacy)))
            started = start_watcher()
            msgs.append("后台同步进程已启动" if started else "后台同步进程启动失败")
            time.sleep(1.2)
            ok_sync, total, details = sync_now()
            msgs.extend(details)
            cfg = load_config()
            cfg["autostart"] = True
            cfg["exe"] = exe_path()
            cfg["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
            save_config(cfg)
            return (ok_reg and started), msgs

        def done(result):
            ok, msgs = result
            for part in msgs:
                self.append_log(part)
            self.busy(False)
            self.set_status(("开机同步已开启 · " if ok else "开机同步未完全成功 · ") + msgs[0][:60])
            self.refresh()
            self.offer_boot_task()

        self.run_async(work, done)

    def offer_boot_task(self):
        """询问是否再装一个计划任务，做到"开机还没登录"也校时（需要管理员）。"""
        from tkinter import messagebox
        if task_exists():
            return
        if messagebox.askyesno(
                "可选：更早同步",
                "已开启登录自动同步。\n\n"
                "是否再注册一个计划任务，让它在「开机后、还没登录」的阶段也校时？\n"
                "（需要管理员权限，会弹出一次 UAC 授权窗口）"):
            self.busy(True)
            self.set_status("正在注册计划任务（请在 UAC 窗口点“是”）…")

            def work():
                return register_task_elevated()

            def done(result):
                ok, msg = result
                self.append_log(msg)
                self.busy(False)
                self.set_status(("计划任务已注册 · " if ok else "计划任务未注册 · ") + msg[:60])
                self.refresh()

            self.run_async(work, done)

    def on_stop(self):
        self.busy(True)
        self.set_status("正在停止同步并清理启动项…")
        self.append_log("开始停止同步（全量清理启动项）")

        def work():
            actions, traces = clean_all_autostart()
            cfg = load_config()
            cfg["autostart"] = False
            cfg["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
            save_config(cfg)
            log("停止同步清理完成：{}".format("; ".join(actions)))
            if traces:
                log("停止同步后仍有残留：{}".format("; ".join(traces)))
            else:
                log("停止同步核验：无任何启动项残留")
            return actions, traces

        def done(result):
            actions, traces = result
            for part in actions:
                self.append_log(part)
            if traces:
                self.append_log("⚠ 仍有残留（可能需要在 UAC 窗口授权）: " + "; ".join(traces))
                self.set_status("同步已停止，但仍有 {} 项残留".format(len(traces)))
                from tkinter import messagebox
                messagebox.showwarning(
                    "仍有残留",
                    "已停止同步，但以下启动项残留未能清理：\n\n" + "\n".join(traces) +
                    "\n\n计划任务需要管理员权限：请在弹出的 UAC 窗口点「是」重试，"
                    "或手动在「任务计划程序」里删除该项。",
                    parent=self.root)
            else:
                self.append_log("核验通过：注册表、启动文件夹、计划任务、后台进程均已清理干净")
                self.set_status("同步已停止，启动项已完全清理")
                from tkinter import messagebox
                messagebox.showinfo(
                    "已完全停止",
                    "已完全停止开机同步，并清理干净：\n\n"
                    "· 注册表启动项（含任务管理器的启动项记录）\n"
                    "· 启动文件夹快捷方式\n"
                    "· 计划任务\n"
                    "· 后台同步进程\n"
                    "· 早期版本留下的脚本文件\n\n"
                    "重启后不会再有任何本程序的自启行为。",
                    parent=self.root)
            self.busy(False)
            self.refresh()

        self.run_async(work, done)

    def on_help(self):
        """使用说明：每个按钮与状态的含义。"""
        self.append_log("打开「怎么用」使用说明")
        try:
            show_text_dialog(self.root, "怎么用  ·  {}".format(APP_TITLE), help_text())
        except Exception as exc:
            log("帮助对话框失败: {}".format(exc))
            try:
                ctypes.windll.user32.MessageBoxW(
                    0, help_text(), "怎么用  ·  {}".format(APP_TITLE), 0x40 | 0x40000)
            except Exception:
                pass

    def on_toggle_autocheck(self):
        """开关「启动时自动检查更新」。"""
        on = bool(self.var_autocheck.get())
        cfg = load_config()
        cfg["auto_check_update"] = on
        save_config(cfg)
        msg = "已开启启动时自动检查更新" if on else "已关闭启动时自动检查更新（仍可手动检查）"
        self.append_log(msg)
        log("界面：" + msg)
        self.set_status(msg)

    def on_changelog(self):
        """版本历史单独一个对话框（不再塞进「关于」）。"""
        self.append_log("打开「版本历史」")
        try:
            show_text_dialog(self.root, "版本历史  ·  {}".format(APP_TITLE),
                             changelog_text(), width=76, height=28)
        except Exception as exc:
            log("版本历史对话框失败: {}".format(exc))

    # ------------------------------------------------------------ 检查更新 --
    def on_check_update(self, silent=False):
        """检查更新。

        silent=True 用于启动时自动检查：没有更新（或检查失败）时完全不打扰用户。
        """
        if silent and load_config().get("auto_check_update") is False:
            log("启动检查更新：已被用户关闭（配置 auto_check_update=false）")
            return
        if getattr(self, "_update_busy", False):
            return
        self._update_busy = True
        self._update_silent = silent
        if not silent:
            self.set_status("正在检查更新…")
            self.append_log("手动检查更新…")

        def work():
            return fetch_latest_release()

        def done(rel):
            self._update_busy = False
            if rel is None:
                if not silent:
                    self.set_status("检查更新失败（网络不可用？）")
                    self.append_log("检查更新失败：无法访问 GitHub")
                    from tkinter import messagebox
                    messagebox.showwarning(
                        "检查更新失败",
                        "没能连上 GitHub 获取版本信息。\n\n"
                        "可能原因：网络不可用、被墙、或 GitHub 接口临时异常。\n"
                        "这不影响软件正常使用，稍后再试即可。",
                        parent=self.root)
                return
            if not has_update(rel):
                if not silent:
                    self.set_status("已是最新版本 v{}".format(APP_VERSION))
                    self.append_log("检查更新：已是最新版本（线上 v{}）".format(rel["tag"]))
                    from tkinter import messagebox
                    messagebox.showinfo(
                        "已是最新版本",
                        "当前版本：v{}\n线上最新：{}\n\n已是最新，无需更新。".format(
                            APP_VERSION, rel["tag"]),
                        parent=self.root)
                return
            # 有更新
            self.append_log("检查更新：发现新版本 {}（当前 v{}）".format(
                rel["tag"], APP_VERSION))
            if silent and load_config().get("skip_version") == rel["tag"]:
                log("启动检查更新：{} 已被用户选择跳过".format(rel["tag"]))
                return
            self._prompt_update(rel)

        self.run_async(work, done)

    def _prompt_update(self, rel):
        """有更新时弹对话框，问是否下载并自动替换。"""
        from tkinter import messagebox
        body = rel.get("body") or ""
        brief = body.strip()
        if len(brief) > 1200:
            brief = brief[:1200] + "\n……（完整说明见发布页）"
        try:
            size_mb = rel["asset_size"] / 1024.0 / 1024.0
            size_txt = "{:.2f} MB".format(size_mb)
        except Exception:
            size_txt = "未知大小"
        msg = (
            "发现新版本：{}\n"
            "当前版本：v{}\n"
            "下载大小：{}\n\n"
            "是否现在下载并自动替换旧版本？\n\n"
            "· 下载完成后程序会自动关闭，替换 exe 并重新打开\n"
            "· 你的开机自启设置、日志、配置都不会受影响\n"
            "· 若网络较慢也可以选「否」，去发布页手动下载\n\n"
            "———— 本次更新内容 ————\n{}"
        ).format(rel["tag"], APP_VERSION, size_txt, brief)
        self.set_status("发现新版本 {}".format(rel["tag"]))
        yes = messagebox.askyesno("发现新版本 {}".format(rel["tag"]), msg,
                                  parent=self.root)
        if not yes:
            self.append_log("已跳过本次更新（{}）".format(rel["tag"]))
            cfg = load_config()
            cfg["skip_version"] = rel["tag"]
            save_config(cfg)
            return
        self._start_update_download(rel)

    def _start_update_download(self, rel):
        """下载 → 校验 → 派发自替换脚本 → 退出本进程。"""
        import tempfile
        from tkinter import messagebox
        dest = os.path.join(tempfile.gettempdir(),
                            "Nostation-update-{}.exe".format(rel["tag"]))

        # 进度对话框
        win = None
        bar = None
        label = None
        try:
            import tkinter as tk
            from tkinter import ttk
            win = tk.Toplevel(self.root)
            win.title("正在下载更新 {}".format(rel["tag"]))
            win.transient(self.root)
            win.resizable(False, False)
            win.protocol("WM_DELETE_WINDOW", lambda: None)   # 下载中不允许关闭
            frm = ttk.Frame(win, padding=16)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text="正在下载 {} …".format(rel["tag"])).pack(anchor="w")
            bar = ttk.Progressbar(frm, length=380, mode="determinate", maximum=100)
            bar.pack(pady=10)
            label = ttk.Label(frm, text="0%")
            label.pack(anchor="w")
            ttk.Label(frm, text="下载完成后程序会自动关闭并替换为新版本。",
                      foreground="#5a6673").pack(anchor="w", pady=(8, 0))
            win.update_idletasks()
            w, h = win.winfo_width(), win.winfo_height()
            x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
            win.geometry("+{}+{}".format(max(0, x), max(0, y)))
        except Exception as exc:
            log("进度对话框创建失败: {}".format(exc))
            win = None

        def progress(done, total):
            def ui():
                if bar is None:
                    return
                pct = int(done * 100 / total) if total else 0
                try:
                    bar.configure(value=pct)
                    label.configure(text="{}%  （{:.1f} / {:.1f} MB）".format(
                        pct, done / 1048576.0, (total or 0) / 1048576.0))
                except Exception:
                    pass
            try:
                self.root.after(0, ui)
            except Exception:
                pass

        def work():
            return download_update(rel, dest, progress)

        def done_result(result):
            ok, info = result if isinstance(result, tuple) else (False, "未知错误")
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass
            if not ok:
                self.append_log("更新下载失败：{}".format(info))
                self.set_status("更新下载失败")
                messagebox.showerror("更新失败", "{}\n\n可到发布页手动下载：\n{}".format(
                    info, rel.get("html_url") or UPDATE_PAGE), parent=self.root)
                return
            self.append_log("更新已下载并通过校验：{}".format(dest))
            self.set_status("下载完成，正在替换旧版本…")
            ok_spawn, info_spawn = spawn_self_replace(dest)
            if not ok_spawn:
                self.append_log("派发替换脚本失败：{}".format(info_spawn))
                messagebox.showerror(
                    "自动替换失败",
                    "新版已下载到：\n{}\n\n但自动替换没能启动（{}）。\n"
                    "请手动关闭本程序，把上面这个文件改名为 "
                    "NostationsPal.exe 覆盖旧版。".format(dest, info_spawn),
                    parent=self.root)
                return
            messagebox.showinfo(
                "更新准备就绪",
                "新版已下载并通过 SHA256 校验。\n\n"
                "点「确定」后程序会关闭，自动替换 exe 并重新打开。\n"
                "（大约 3~10 秒，期间请不要手动操作）\n\n"
                "替换过程会记录在：\n{}".format(
                    os.path.join(data_dir(), "self-update.log")),
                parent=self.root)
            log("更新：退出程序以便替换 exe")
            try:
                self.root.destroy()
            except Exception:
                pass

        self.append_log("开始下载 {} …".format(rel["tag"]))
        self.run_async(work, done_result)

    def on_about(self):
        self.append_log("打开「关于」对话框")
        show_about(self.root)

    def on_license(self):
        """查看已同意的许可条款（只读，不写配置）。"""
        try:
            from tkinter import messagebox
            messagebox.showinfo(
                "许可条款  ·  {} v{}（条款版本 {}）".format(
                    APP_TITLE, APP_VERSION, LICENSE_VERSION),
                license_message().replace("是否同意以上条款并继续使用？",
                                          "（你已同意本条款）"),
                parent=self.root)
        except Exception:
            show_license()

    def on_open_log(self):
        try:
            os.startfile(log_path())
        except Exception as exc:
            self.set_status("无法打开日志: {}".format(exc))

    def on_open_dir(self):
        try:
            os.startfile(data_dir())
        except Exception as exc:
            self.set_status("无法打开目录: {}".format(exc))

    def on_sync_once(self):
        if not self._require_hub():
            return
        self.set_status("正在校时…")
        self.append_log("手动校时")

        def work():
            ok, total, details = sync_now()
            return (bool(total) and ok == total), details

        def done(result):
            ok, details = result
            for part in details:
                self.append_log(part)
            self.set_status(("校时成功 · " if ok else "校时未完成 · ") + (details[0] if details else "")[:60])
            self.refresh()

        self.run_async(work, done)


SPLASH_CLOSED = False


def close_splash():
    """关掉 PyInstaller 启动闪屏（非打包环境下是空操作）。"""
    global SPLASH_CLOSED
    try:
        import pyi_splash  # type: ignore  # noqa: F401
        pyi_splash.close()
        SPLASH_CLOSED = True
        return True
    except Exception:
        return False


def _schedule_splash_safety_net(root, timeout_ms=12000):
    """保险：万一闪屏没被正常关掉（异常路径），超时后强制关闭并记录。

    正常流程里 run_gui() 会在窗口显示后立刻 close_splash()，
    那时 SPLASH_CLOSED 已为 True，这里什么也不做——不会误报。
    """
    def check():
        if SPLASH_CLOSED:
            return
        try:
            import pyi_splash  # type: ignore
            pyi_splash.close()
            globals()["SPLASH_CLOSED"] = True
            log("闪屏未被正常关闭，已由保险机制强制关闭（请反馈日志）")
        except Exception:
            pass

    def arm():
        try:
            import pyi_splash  # type: ignore  # noqa: F401
        except Exception:
            return                      # 非打包环境，没有闪屏
        try:
            root.after(timeout_ms, check)
        except Exception:
            pass

    try:
        root.after(0, arm)
    except Exception:
        pass


def run_gui():
    # ---- 启动快路径：只做必要的事，让窗口尽快出现 ----
    # 实测慢的都是外部调用：hid.enumerate 约 1.1s、每个 PowerShell 调用约 0.6s，
    # 全放在窗口显示之前会让"双击到看见界面"变成好几秒（期间只有一个白框）。
    # 所以这里只做：许可检查 → 清日志 → 建窗 → 设图标 → 关闪屏，
    # 其余（位置自愈、后台进程核查、设备状态）都排到窗口显示之后执行。
    if not license_ok():
        close_splash()
        try:
            ctypes.windll.user32.MessageBoxW(0, MSG_LICENSE_DECLINED,
                                             APP_TITLE, 0x30 | 0x40000)
        except Exception:
            pass
        return 0

    # 每次打开程序都清空日志（后台同步进程正在跑也照清）。
    # 另一个进程下次写日志时会通过"会话标记"发现被重置，不会把旧内容写回来。
    reset_log("界面模式")

    gui_lock = acquire_lock(GUI_LOCK_PORTS)
    if gui_lock is None:
        close_splash()
        try:
            ctypes.windll.user32.MessageBoxW(
                0, "{}已经在运行了。\n\n（版权归 {} 所有）".format(APP_TITLE, AUTHOR),
                APP_TITLE, 0x40 | 0x40000)
        except Exception:
            pass
        return 0

    try:
        import tkinter as tk
        root = tk.Tk()
        # 先隐藏，把界面全部布局好、图标设好，最后再显示：
        # 否则会先闪一个默认尺寸的小窗口（Tk 建窗时是 200x200 左右），
        # 然后才跳到真正的尺寸。
        root.withdraw()
        app = App(root)                     # 构建界面（此时窗口不可见）
        apply_window_icon(root)             # 图标在显示之前设好（约 35ms）
        root.update_idletasks()             # 让几何尺寸先生效
        root.deiconify()
        close_splash()                      # 界面已就绪，撤掉启动画面
        _schedule_splash_safety_net(root)   # 异常时兜底关闭
        root.after(30, lambda: _gui_deferred_startup(app))
        # 顺手回收历史遗留的临时目录（被强制结束时 bootloader 来不及删）
        root.after(1200, lambda: _safe_sweep_tempdirs())
        root.mainloop()
        return 0
    except Exception:
        close_splash()
        log("界面启动失败: {}".format(traceback.format_exc()))
        try:
            ctypes.windll.user32.MessageBoxW(
                0, "程序启动失败，请查看日志:\n" + log_path(), APP_TITLE, 0x10)
        except Exception:
            pass
        return 1


def _safe_sweep_tempdirs():
    try:
        sweep_leftover_tempdirs()
    except Exception:
        pass


def _gui_deferred_startup(app):
    """窗口已经显示之后才跑的活（原来阻塞启动的那些）。"""
    # 周期性刷新日志与设备状态（tick 内部会做设备枚举，必须在窗口显示后启动）
    try:
        app.tick()
    except Exception as exc:
        log("启动定时刷新失败: {}".format(exc))
    try:
        found, fixed = self_heal_location(repair=True)
    except Exception as exc:
        found, fixed = [], []
        log("位置自愈检查异常: {}".format(exc))
    if fixed:
        app.append_log("位置自愈：已修正开机自启指向 -> {}".format(exe_path()))
        app.set_status("检测到程序位置变化，已自动修正自启项")
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                "检测到程序位置已变化，已自动修正开机自启：\n\n" + "\n".join(fixed) +
                "\n\n以后换到任何位置都不用重新设置。",
                "{}  ·  位置已自动修正".format(APP_TITLE), 0x40 | 0x40000)
        except Exception:
            pass
    try:
        # manual=False：这是程序启动时的自动检查，不该写成「手动重新检查」
        app.refresh(manual=False)
    except Exception as exc:
        log("启动状态刷新失败: {}".format(exc))

    # 启动时自动检查一次更新（静默：没有更新或网络不通都不打扰用户）。
    # 延迟 3 秒，让窗口先稳定显示、状态先查完。
    try:
        app.root.after(3000, lambda: app.on_check_update(silent=True))
    except Exception as exc:
        log("排入启动检查更新失败: {}".format(exc))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--watch", action="store_true", help="后台监视模式")
    parser.add_argument("--sync-now", action="store_true", help="立刻校时一次")
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--refresh", type=float, default=1800.0)
    parser.add_argument("--status", action="store_true", help="打印状态后退出")
    parser.add_argument("--show-license", action="store_true",
                        help="只看许可条款对话框（排版预览用，不写配置）")
    parser.add_argument("--out", default=None,
                        help="配合 --status：把状态写到文件（exe 无控制台时用于自检）")
    parser.add_argument("--no-heal", action="store_true",
                        help="本次运行不做位置自愈")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    args, _unknown = parser.parse_known_args(argv)

    if args.version:
        _safe_print("{} v{}".format(APP_TITLE, APP_VERSION))
        return 0
    if args.help:
        _safe_print(__doc__)
        return 0

    _load_reject_state()

    # 回收历史遗留的 _MEIxxxx 临时目录（被强制结束时 bootloader 来不及删）。
    # 放在最前面：不依赖界面，命令行模式也会执行。
    # --status 是自检用的只读路径，跳过以免影响它。
    if not args.status:
        try:
            sweep_leftover_tempdirs()
        except Exception:
            pass

    # 重要：PyInstaller 的启动闪屏在这些"无界面"模式里**也会被创建**，
    # 如果不主动关闭，它就会永久挂在桌面上——后台同步进程（--watch）
    # 是开机自启拉起的、一直常驻，所以那个闪屏会一直存在。
    # 所有这些模式都不需要启动画面，进入分支前先关掉。
    if args.watch or args.sync_now or args.status or args.show_license or args.no_heal:
        close_splash()

    # 只要程序能跑起来（双击、开机自启拉起后台、命令行模式），
    # 就检查一次"记录里指着的位置是不是当前这个文件"，不是就改过来。
    # 这是"随便挪到哪个路径都能继续开机自启"的关键。
    healed = []
    if not args.no_heal:
        try:
            _found, healed = self_heal_location(repair=True)
        except Exception as exc:
            log("位置自愈检查异常: {}".format(exc))

    if args.watch:
        return watch_loop(args.interval, args.refresh)
    if args.show_license:
        # 仅用于排版预览：显示对话框但不写入任何配置
        show_license()
        return 0
    if args.sync_now:
        ok, total, details = sync_now(verbose=True)
        return 0 if (total and ok == total) else 1
    if args.status:
        reg = registry_autostart_target()
        locations = autostart_locations()
        mismatched = []
        for label, key in (("registry", "registry"), ("shortcut", "shortcut"), ("task", "task")):
            target = locations.get(key) or ""
            if target and not _same_file(target, exe_path()):
                mismatched.append("{} -> {}".format(label, target))
        lines = [
            "version   : {}".format(APP_VERSION),
            "exe       : {}".format(exe_path()),
            "hub       : " + ("connected" if hub_present() else "not found"),
            "autostart : " + ("on" if autostart_active() else "off"),
            "registry  : " + (reg if reg else "no"),
            "task      : " + ("yes" if task_exists() else "no"),
            "shortcut  : " + ("yes" if os.path.exists(startup_shortcut_path()) else "no"),
            "watcher   : " + ("running" if running_watcher_pids() else "stopped"),
            "healed    : " + ("; ".join(healed) if healed else "no change needed"),
            "stale     : " + ("; ".join(mismatched) if mismatched else "none"),
            "leftovers : " + ("; ".join(find_autostart_traces(only_unwanted=True)) or "none"),
        ]
        text = "\n".join(lines)
        _safe_print(text)
        if args.out:
            try:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
            except Exception:
                return 1
        return 0
    return run_gui()


if __name__ == "__main__":
    try:
        if "--watch" not in sys.argv and "--sync-now" not in sys.argv:
            setup_dpi()  # 必须在任何 tkinter 窗口之前调用
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        log("FATAL: {}".format(traceback.format_exc()))
        sys.exit(2)
