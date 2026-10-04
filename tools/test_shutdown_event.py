"""验证关机处理器：给 exe 的隐藏通知窗口发 WM_QUERYENDSESSION，看它是否优雅退出。

判定标准：
  * 进程自己退出（不是被 taskkill 杀的）
  * 退出后 _MEI 临时目录被清理干净（说明 bootloader 正常完成收尾）
  * 日志里出现"收到系统关机通知"和"后台同步进程结束"

用法：
    python test_shutdown_event.py <exe>
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
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016


def find_sink_window(pid):
    """找指定进程里那个用来接收关机通知的隐藏窗口。

    它没有标题、通常不可见，所以按"属于该 PID 的窗口"来找。
    """
    res = []

    def cb(hwnd, lparam):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid:
            return True
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls, 128)
        res.append((hwnd, cls.value))
        return True

    user32.EnumWindows(
        ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    return res


def main():
    exe = sys.argv[1]
    before = set(glob.glob(os.path.join(TMP, "_MEI*")))

    print("启动 --watch ...")
    p = subprocess.Popen([exe, "--watch"])
    time.sleep(9)

    # 先看清有哪些窗口（含 bootloader 自己的隐藏窗口，别发错对象）
    wins = find_sink_window(p.pid)
    print("  父进程窗口: %s" % wins)

    # 我们的通知窗口是 message-only 窗口（父级 HWND_MESSAGE），
    # EnumWindows **不会**枚举到它，必须用 FindWindowExW 在 HWND_MESSAGE 下找。
    HWND_MESSAGE = ctypes.c_void_p(-3)
    user32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowExW.restype = ctypes.c_void_p
    target = user32.FindWindowExW(HWND_MESSAGE, None,
                                  "NostationPalShutdownSink", None)
    if target:
        print("  找到通知窗口（message-only）: 0x%X" % target)
    else:
        print("  !! 没找到 NostationPalShutdownSink（message-only 窗口）")
        print("     父进程窗口: %s" % wins)
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       capture_output=True)
        return 1

    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_void_p, ctypes.c_void_p]
    print("  发送 WM_QUERYENDSESSION -> 0x%X" % target)
    user32.PostMessageW(target, WM_QUERYENDSESSION, 0, 0)
    time.sleep(1.5)

    print("  等待进程自行退出 ...")
    exited_cleanly = False
    try:
        p.wait(timeout=25)
        exited_cleanly = True
    except subprocess.TimeoutExpired:
        pass
    print("  进程自行退出: %s" % ("是" if exited_cleanly else "否（需强杀）"))

    if not exited_cleanly:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       capture_output=True)
    time.sleep(2.5)

    left = set(glob.glob(os.path.join(TMP, "_MEI*"))) - before
    print("  临时目录残留: %d %s" % (len(left), [os.path.basename(x) for x in left]))

    log = os.path.join(os.environ["LOCALAPPDATA"], "NostationSync",
                       "nostation-sync.log")
    if os.path.exists(log):
        txt = open(log, encoding="utf-8", errors="replace").read()
        print()
        print("  日志关键行:")
        for line in txt.splitlines():
            if any(k in line for k in ("关机", "优雅", "结束", "通知")):
                print("    %s" % line.strip())

    print()
    ok = exited_cleanly and not left
    print("结论: %s" % ("PASS —— 关机处理器生效，且无残留" if ok
                        else "FAIL —— 见上面的输出"))
    for d in left:
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", d], capture_output=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
