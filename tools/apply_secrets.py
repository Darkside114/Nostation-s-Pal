"""把源码里的默认凭据改成"从本地私有文件读取"，凭据本身永不入库。

== 背景 ==
  早期为了方便把和风的 API Key / 专属 Host 直接写成了代码里的默认值，
  结果这些**绑定账号、有调用配额**的凭据进了 Git 历史 ——
  任何 clone 的人翻历史就能拿到，还会消耗账号持有人的额度。
  （该历史已被重写清除，见仓库说明。）

  现在改成：
      app_secrets.py（**已被 .gitignore 忽略，永不入库**）
          QWEATHER_KEY  = "..."
          QWEATHER_HOST = "..."

      weather.py / companion_app.py 运行时尝试导入它；
      导入不到就用空字符串 —— 此时和风不可用，免费数据源照常工作。

  这样公开代码永远不含凭据；本机放一个 app_secrets.py 就能开箱即用。

== 本脚本刻意不含任何凭据 ==
  需要写入凭据时用命令行传入：

      python tools/apply_secrets.py --write \
          --key <你的KEY> --host <你的专属HOST>

  或者写一个 --from-file 指向已存在的 app_secrets.py。
  仓库里不留凭据，连这个工具也不例外。

用法：
    python tools/apply_secrets.py --check
    python tools/apply_secrets.py --sanitize
    python tools/apply_secrets.py --write --key XXX --host YYY
"""
import argparse
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = os.path.join(REPO, "app_secrets.py")
GITIGNORE = os.path.join(REPO, ".gitignore")
COMPANION = os.path.join(REPO, "companion_app.py")
WEATHER = os.path.join(REPO, "weather.py")

COMPANION_OLD = re.compile(
    r'# (?:内置的和风天气凭据|和风天气凭据)[^\n]*\n(?:[^\n]*\n)*?'
    r'(?:try:\n(?:[^\n]*\n)*?except Exception:\n'
    r'(?:[^\n]*\n)*?|DEFAULT_QWEATHER_HOST = "[^"]*"\n'
    r'DEFAULT_QWEATHER_KEY = "[^"]*"\n)', re.S)

COMPANION_NEW = '''# 和风天气凭据**不写进代码**。
# 和风免费订阅的 Host 是每个账号专属的，且 Key 绑定账号、有调用配额；
# 把凭据硬编码进来会消耗他人的额度。所以这里一律为空，由用户自己在
# 界面上填；本机开发者可以放一个 app_secrets.py（已被 .gitignore 忽略）
# 来预填，见 tools/apply_secrets.py。
try:
    from app_secrets import QWEATHER_HOST as DEFAULT_QWEATHER_HOST
    from app_secrets import QWEATHER_KEY as DEFAULT_QWEATHER_KEY
except Exception:
    DEFAULT_QWEATHER_HOST = ""
    DEFAULT_QWEATHER_KEY = ""
'''

WEATHER_OLD = re.compile(
    r'# 和风(?: 2024 起|凭据)[^\n]*\n(?:[^\n]*\n)*?'
    r'(?:try:\n(?:[^\n]*\n)*?except Exception:\n'
    r'(?:[^\n]*\n)*?|DEFAULT_QW_HOST = "[^"]*"\n'
    r'(?:\s*\n)*# 内置的和风凭据[^\n]*\n(?:[^\n]*\n)*?'
    r'DEFAULT_QW_KEY = "[^"]*"\n)', re.S)

WEATHER_NEW = '''# 和风 2024 起，免费订阅**不能**用 devapi.qweather.com（会返回
# 403 Invalid Host），必须用控制台里给每个账号分配的专属 Host。
#
# 凭据**不写进代码**：和风 Key 绑定账号、有调用配额，硬编码会消耗
# 他人额度。这里从本地私有文件 app_secrets.py（已被 .gitignore 忽略）
# 读取；读不到就为空 —— 和风不可用，但免费数据源照常工作。
try:
    from app_secrets import QWEATHER_KEY as DEFAULT_QW_KEY
    from app_secrets import QWEATHER_HOST as DEFAULT_QW_HOST
except Exception:
    DEFAULT_QW_KEY = ""
    DEFAULT_QW_HOST = ""
'''


def sanitize():
    changed = []
    for path, pat, new in ((COMPANION, COMPANION_OLD, COMPANION_NEW),
                           (WEATHER, WEATHER_OLD, WEATHER_NEW)):
        s = io.open(path, encoding="utf-8").read()
        if 'from app_secrets import' in s:
            continue                      # 已经改过了
        if pat.search(s):
            s = pat.sub(new, s, count=1)
            io.open(path, "w", encoding="utf-8", newline="").write(s)
            changed.append(os.path.basename(path))
    return changed


def ensure_gitignore():
    s = io.open(GITIGNORE, encoding="utf-8").read() if os.path.exists(GITIGNORE) else ""
    lines = [l.strip() for l in s.splitlines()]
    added = []
    for pat in ("app_secrets.py", "*.sharebak", "app_secrets.py.hidden*"):
        if pat not in lines:
            s = s.rstrip("\n") + "\n" + pat + "\n"
            added.append(pat)
    if added:
        io.open(GITIGNORE, "w", encoding="utf-8", newline="").write(s)
    return added


def write_secrets(key, host):
    body = ('"""本地私有凭据 —— 该文件被 .gitignore 忽略，永远不会进仓库。\n\n'
            '由 tools/apply_secrets.py --write 生成，也可以自己填。\n'
            '删掉它程序照样能用，只是和风天气需要手动填 Key。\n"""\n'
            'QWEATHER_KEY = "%s"\n'
            'QWEATHER_HOST = "%s"\n' % (key, host))
    io.open(SECRETS, "w", encoding="utf-8", newline="").write(body)
    return SECRETS


# 已被 .gitignore 忽略、允许在本机持有凭据的文件（不算泄漏）
ALLOWED_LOCAL = {"app_secrets.py"}


def scan_leaks():
    """扫描仓库里是否还有硬编码凭据。

    只认"看起来像真的凭据"的值：长度足够，且不含正则元字符或
    格式化占位符。早期版本用 ([^"]+) 抓任意非空串，结果把本文件里的
    正则片段（如 [^"\n]*）和占位符（%s）都当成了凭据，一直误报。
    """
    leaks = []
    pat = re.compile(
        r'(?:QWEATHER|DEFAULT_QW\w*)_(?:KEY|HOST)\s*=\s*"([^"]+)"')
    bad_chars = set('[]()*+?|\\^$%{}')
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "dist", "build", "__pycache__")]
        for f in files:
            if f in ALLOWED_LOCAL:
                continue
            if not f.endswith((".py", ".ps1", ".md", ".json", ".txt")):
                continue
            path = os.path.join(root, f)
            try:
                body = io.open(path, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for i, line in enumerate(body.splitlines(), 1):
                m = pat.search(line)
                if not m:
                    continue
                v = m.group(1)
                if len(v) < 12:                     # 太短，不是凭据
                    continue
                if any(ch in bad_chars for ch in v):
                    continue                        # 正则片段/占位符
                if not re.fullmatch(r"[A-Za-z0-9._\-]+", v):
                    continue                        # 含异常字符
                leaks.append((os.path.relpath(path, REPO), i, v[:8] + "..."))
    return leaks


def check():
    print("=== 当前状态 ===")
    print("  app_secrets.py 存在:", os.path.exists(SECRETS))
    for path in (COMPANION, WEATHER):
        s = io.open(path, encoding="utf-8").read()
        ok = 'from app_secrets import' in s
        print("  %-18s %s" % (os.path.basename(path),
                              "从私有文件读 ✓" if ok else "★仍是硬编码"))
    gi = io.open(GITIGNORE, encoding="utf-8").read() if os.path.exists(GITIGNORE) else ""
    print("  app_secrets.py 已忽略:", "app_secrets.py" in gi)
    print()
    leaks = scan_leaks()
    if leaks:
        print("  ★ 发现硬编码凭据：")
        for f, i, v in leaks:
            print("     %s:%d  %s" % (f, i, v))
    else:
        print("  未发现硬编码凭据 ✓")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--sanitize", action="store_true", help="只清理源码")
    ap.add_argument("--write", action="store_true", help="写入 app_secrets.py")
    ap.add_argument("--key", default="")
    ap.add_argument("--host", default="")
    ap.add_argument("--from-file", default="")
    args = ap.parse_args()

    if args.check:
        return check()

    if args.write:
        key, host = args.key, args.host
        if args.from_file and os.path.exists(args.from_file):
            s = io.open(args.from_file, encoding="utf-8").read()
            mk = re.search(r'QWEATHER_KEY\s*=\s*"([^"]*)"', s)
            mh = re.search(r'QWEATHER_HOST\s*=\s*"([^"]*)"', s)
            key = key or (mk.group(1) if mk else "")
            host = host or (mh.group(1) if mh else "")
        if not key:
            print("  需要 --key（或用 --from-file）")
            return 2
        print("  已写入:", write_secrets(key, host))
        ensure_gitignore()
        return check()

    print("=== 1) 清理源码里的硬编码凭据 ===")
    changed = sanitize()
    print("  已改写:", ", ".join(changed) if changed else "(无需改动)")
    print()
    print("=== 2) 确保 .gitignore 忽略私有文件 ===")
    added = ensure_gitignore()
    print("  新增忽略规则:", ", ".join(added) if added else "(已存在)")
    print()
    return check()


if __name__ == "__main__":
    sys.exit(main())
