"""构建「分享版」exe：不含任何和风凭据。

== 为什么需要分享版 ==
  本机开发时会把和风 Key / 专属 Host 放在 app_secrets.py
  （已被 .gitignore 忽略，不入库）。如果直接拿这份代码打包，
  凭据会被 PyInstaller 收进 exe —— 分发出去就等于把
  **绑定账号、有调用配额**的 Key 送给所有人。

== 做法 ==
  1. 构建前把 app_secrets.py 临时改名，这样 PyInstaller 导入不到它，
     weather.py / companion_app.py 的 except 分支生效，凭据为空。
  2. 构建。
  3. **复查产物**：把 app_secrets.py 里的值读出来在二进制里搜一遍，
     搜到就判定产物不合格并删掉。
  4. finally 里把 app_secrets.py 改回来。

  本脚本自身不含任何凭据 —— 复查用的值是从 app_secrets.py 现读的。

用法：
    python tools/build_share_exe.py
产物：
    dist/NostationsPal-share.exe
"""
import io
import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = os.path.join(REPO, "app_secrets.py")
HIDDEN = os.path.join(REPO, "app_secrets.py.hidden_for_share_build")
DIST = os.path.join(REPO, "dist", "NostationsPal.exe")
SHARE = os.path.join(REPO, "dist", "NostationsPal-share.exe")


def secret_values():
    """从 app_secrets.py 现读出凭据值（用于构建后复查产物）。"""
    if not os.path.exists(SECRETS):
        return []
    s = io.open(SECRETS, encoding="utf-8").read()
    return [v.encode() for v in re.findall(r'=\s*"([^"]+)"', s) if v]


def main():
    secrets = secret_values()
    print("=== 0) 待复查的本地凭据 ===")
    print("  数量:", len(secrets))
    if not secrets:
        print("  提示：没有 app_secrets.py，产物本来就不会含凭据")

    moved = False
    try:
        if os.path.exists(SECRETS):
            shutil.move(SECRETS, HIDDEN)
            moved = True
            print()
            print("=== 1) 已临时移走 app_secrets.py ===")

        print()
        print("=== 2) 构建 ===")
        ps1 = os.path.join(REPO, "build_exe.ps1")
        p = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", ps1],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8",
            errors="replace")
        for l in (p.stdout or "").splitlines():
            if any(k in l for k in ("Build OK", "Size    :", "error")):
                print("  " + l.strip())
        if not os.path.exists(DIST):
            print("  构建失败")
            print((p.stderr or "")[-800:])
            return 1

        print()
        print("=== 3) 复查产物：凭据必须不存在 ===")
        raw = open(DIST, "rb").read()
        print("  产物大小: %.2f MB" % (len(raw) / 1024 / 1024))
        bad = [v for v in secrets if v in raw]
        if bad:
            print("  ★ 产物里发现 %d 处凭据，产物已删除" % len(bad))
            os.remove(DIST)
            return 1
        print("  凭据检查通过 ✓")

        print()
        print("=== 4) 另存为分享版 ===")
        shutil.copy2(DIST, SHARE)
        print("  %s  (%.2f MB)" % (SHARE, os.path.getsize(SHARE) / 1024 / 1024))
        return 0
    finally:
        if moved:
            shutil.move(HIDDEN, SECRETS)
            print()
            print("=== 5) 已恢复 app_secrets.py ===")


if __name__ == "__main__":
    sys.exit(main())
