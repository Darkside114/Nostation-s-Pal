"""对照实验：正常关闭时 _MEI 临时目录能否被删掉（删不掉 = 会弹警告框）。

PyInstaller 单文件程序的 bootloader 在退出时会删除自己的 _MEIxxxx 解压目录。
删不掉时会弹「Failed to remove temporary directory」—— 我无法可靠地"看见"弹窗，
但**目录是否被删除**是完全可测的等价判据。

用法：
    python test_mei_cleanup.py <exe> [次数]
"""
import ctypes
import glob
import os
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
P = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_main_window():
    """找 NOSTATION 的 Tk 主窗口，用来发 WM_CLOSE（等同一用户点 X）。"""
    res = [None]

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if not cls.value.startswith("Tk"):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        user32.GetWindowTextW(hwnd, buf, n + 2)
        if "Nostation" in buf.value or "Pal" in buf.value:
            res[0] = hwnd
            return False
        return True

    user32.EnumWindows(P(cb), 0)
    return res[0]


def count_dialogs():
    """统计当前可见的 #32770 对话框（弹窗就是这一类）。"""
    n = [0]

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "#32770":
            n[0] += 1
        return True

    user32.EnumWindows(P(cb), 0)
    return n[0]


def main():
    exe = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    tmp = os.environ["TEMP"]
    fails = 0

    print("测试: %s" % os.path.basename(exe))
    print("轮数: %d" % rounds)
    print()
    for i in range(1, rounds + 1):
        before = set(glob.glob(os.path.join(tmp, "_MEI*")))
        p = subprocess.Popen([exe])
        # 等窗口出现
        hwnd = None
        for _ in range(40):
            time.sleep(0.5)
            hwnd = find_main_window()
            if hwnd:
                break
        if not hwnd:
            print("  第 %d 轮: 主窗口没出现，跳过" % i)
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True)
            time.sleep(1)
            continue
        time.sleep(2)
        # 优雅关闭 = 点 X
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        try:
            p.wait(timeout=25)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True)
            time.sleep(2)
        time.sleep(1.5)
        # 关键判据：本次新产生的目录还在不在
        after = set(glob.glob(os.path.join(tmp, "_MEI*")))
        left = after - before
        dlg = count_dialogs()
        if left:
            fails += 1
            print("  第 %d 轮: ✗ 残留 %d 个 %s   (可见对话框 %d)"
                  % (i, len(left), [os.path.basename(x) for x in left], dlg))
        else:
            print("  第 %d 轮: ✓ 已清理干净   (可见对话框 %d)" % (i, dlg))
        # 清掉残留，保证下一轮判据干净
        for d in left:
            subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", d],
                           capture_output=True)

    print()
    print("结果: %d/%d 轮清理失败" % (fails, rounds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
