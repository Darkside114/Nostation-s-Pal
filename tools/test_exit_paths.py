"""对照实验：不同退出路径下，_MEI 临时目录能不能被 bootloader 删掉。

删不掉 = bootloader 会弹「Failed to remove temporary directory」。

覆盖的退出路径：
  A  GUI 正常关闭（点 X）
  B  --watch 由 bootloader 直接拉起，然后强杀（模拟关机）
  C  --watch 由 bootloader 直接拉起，发 WM_CLOSE / 正常结束
  D  --status（短命进程，自己结束）

判据：进程完全退出后，本次新产生的 _MEI 目录是否还留在 %TEMP%。
"""
import ctypes
import glob
import os
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
TMP = os.environ["TEMP"]


def mei_set():
    return set(glob.glob(os.path.join(TMP, "_MEI*")))


def snap(exe, args, wait_s, kill=False, mode="gui"):
    """跑一轮，返回 (是否残留, 残留目录数)。"""
    before = mei_set()
    p = subprocess.Popen([exe] + args)
    time.sleep(wait_s)
    if kill:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       capture_output=True)
    else:
        # 尝试优雅关闭所有属于这个进程树的顶层窗口
        killed = False
        for _ in range(30):
            time.sleep(0.5)
            if p.poll() is not None:
                break
        else:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True)
            killed = True
    try:
        p.wait(timeout=20)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       capture_output=True)
        time.sleep(2)
    time.sleep(2)
    left = mei_set() - before
    return left, killed


def main():
    exe = sys.argv[1]
    print("exe: %s" % os.path.basename(exe))
    print()

    # A：GUI 正常关闭 —— 用 WM_CLOSE 手动关
    print("=== A  GUI 正常关闭（点 X）===")
    for i in range(3):
        before = mei_set()
        p = subprocess.Popen([exe])
        hwnd = None
        for _ in range(50):
            time.sleep(0.4)
            res = [None]

            def cb(h, l):
                if not user32.IsWindowVisible(h):
                    return True
                c = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(h, c, 64)
                if not c.value.startswith("Tk"):
                    return True
                n = user32.GetWindowTextLengthW(h)
                b = ctypes.create_unicode_buffer(n + 2)
                user32.GetWindowTextW(h, b, n + 2)
                if "Nostation" in b.value or "Pal" in b.value:
                    res[0] = h
                    return False
                return True

            user32.EnumWindows(
                ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                   wintypes.LPARAM)(cb), 0)
            hwnd = res[0]
            if hwnd:
                break
        if not hwnd:
            print("  第 %d 轮: 窗口没出现" % (i + 1))
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True)
            continue
        time.sleep(1.5)
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        try:
            p.wait(timeout=25)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True)
            time.sleep(2)
        time.sleep(2)
        left = mei_set() - before
        print("  第 %d 轮: %s" % (i + 1, "[FAIL] 残留 %d" % len(left) if left
                                  else "[ OK ] 清理干净"))
        for d in left:
            subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", d],
                           capture_output=True)

    # B：--watch 被强杀（模拟关机）
    print()
    print("=== B  --watch 由 exe 自己拉起后强杀（模拟关机）===")
    for i in range(3):
        before = mei_set()
        p = subprocess.Popen([exe, "--watch"])
        time.sleep(8)
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       capture_output=True)
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
        left = mei_set() - before
        print("  第 %d 轮: %s" % (i + 1, "[FAIL] 残留 %d" % len(left) if left
                                  else "[ OK ] 清理干净"))
        for d in left:
            subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", d],
                           capture_output=True)

    # C：--status 短命进程
    print()
    print("=== C  --status（短命进程，自然结束）===")
    for i in range(3):
        before = mei_set()
        out = os.path.join(os.environ["TEMP"], "st_probe.txt")
        subprocess.run([exe, "--status", "--out", out], capture_output=True,
                       timeout=60)
        time.sleep(1.5)
        left = mei_set() - before
        print("  第 %d 轮: %s" % (i + 1, "[FAIL] 残留 %d" % len(left) if left
                                  else "[ OK ] 清理干净"))
        for d in left:
            subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", d],
                           capture_output=True)

    print()
    print("说明：✗ 的那条路径就是会弹框的路径。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
