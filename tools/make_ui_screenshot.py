"""抓取软件界面的真实截图（不依赖真实桌面，渲染到内存）。

这个会话里屏幕捕获是黑的，所以走 PrintWindow(PW_RENDERFULLCONTENT)：
先真的把界面建起来、填充真实内容并完成布局，再把窗口位图抓下来。
截出来的就是用户双击 exe 后看到的那一屏。
"""
import ctypes
import os
import sys
import time
import tkinter as tk
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                 # 仓库根目录（本脚本在 tools/ 下）
sys.path.insert(0, ROOT)
import companion_app as ca

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def get_hwnd(root):
    root.update_idletasks()
    h = user32.GetParent(root.winfo_id())
    return h if h else root.winfo_id()


def grab(hwnd):
    """PrintWindow 抓整个窗口（含标题栏/菜单）为 PIL 图。"""
    from PIL import Image
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top

    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, bmp)

    ok = user32.PrintWindow(hwnd, memdc, PW_RENDERFULLCONTENT)
    if not ok:
        gdi32.BitBlt(memdc, 0, 0, w, h, hdc, 0, 0, SRCCOPY)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # 负数 = 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)

    # 在客户区的 DC 上做一次真实抓取，顺便验证
    print("  PrintWindow 返回:", ok, " 尺寸: %dx%d" % (w, h))
    # BGRA -> RGBA
    import numpy as np
    arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))
    rgba = arr[:, :, [2, 1, 0, 3]].copy()
    rgba[:, :, 3] = 255
    img = Image.fromarray(rgba, "RGBA").convert("RGB")

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, hdc)
    return img


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ca.script_dir(), "assets", "ui-preview.png")

    root = tk.Tk()
    root.withdraw()
    app = ca.App(root)
    ca.apply_window_icon(root)
    root.update_idletasks()
    root.deiconify()
    root.update()

    # 填入真实感内容（用真实逻辑取状态，失败就退回演示值）
    try:
        state = app._collect_state()
    except Exception as exc:
        print("  取状态失败:", exc)
        state = {}
    # 演示用：让界面呈现"已连接 + 已开启同步"
    state.update({
        "hub": True,
        "hub_name": "4d58:5748 NOSTATION",
        "registry": True,
        "registry_ok": True,
        "watcher": True,
        "task": False,
        "shortcut": False,
    })

    try:
        app._apply_state(state)
    except Exception as exc:
        print("  _apply_state 失败:", exc)

    demo_log = [
        "===== {0}  启动于 2026-10-05 09:44:01   v{1}   界面模式 =====".format(
            ca.APP_TITLE, ca.APP_VERSION),
        "校时 2026-10-05 周一 09:14:03 -> 1/1 成功 (4d58:5748 NOSTATION -> 已同步)",
        "拒绝设备 4d58:0103 100NG Edition：产品 ID 不是 NOSTATION (5748)",
        "校时 2026-10-05 周一 09:44:04 -> 1/1 成功 (4d58:5748 NOSTATION -> 已同步)",
        "已开启开机同步；后台进程运行中 (pid 10792)",
    ]
    try:
        app.text.configure(state="normal")
        app.text.delete("1.0", "end")
        for line in demo_log:
            app.text.insert("end", line + "\n")
        app.text.configure(state="disabled")
        app.text.see("end")
    except Exception as exc:
        print("  写日志区失败:", exc)

    try:
        app.set_status("已同步 · 最近校时 09:44:04 · 后台进程运行中（关闭窗口不影响同步）")
    except Exception:
        pass

    root.update_idletasks()
    root.update()
    time.sleep(1.2)
    root.update()

    hwnd = get_hwnd(root)
    img = grab(hwnd)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out)
    print("  已保存:", out, img.size, os.path.getsize(out), "bytes")
    root.destroy()


if __name__ == "__main__":
    main()
