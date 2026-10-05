"""用「⌐ 形」图案量出屏幕真实宽度。

图案：第 0 行整行点亮 + 第 0 列整列点亮。
  * 宽度猜对 -> 屏幕上是一个正的「⌐」，顶边横平、左边竖直
  * 宽度猜错 -> 左边那条竖线会变成斜线

斜线的斜率能直接算出真实宽度：
  我按宽度 W 排行，每行有 ceil(W/8) 字节。若设备按宽度 W' 读，
  则我的第 k 行在设备上会从像素 k*(W-W') 处开始，即每行横移 (W-W')。
所以「竖线在最后一行横移了多少像素」= (W-W') * 行数。
把这个横移量告诉我（或告诉我竖线斜到大约第几列），就能解出 W'。

用法：
    python lshape_width.py --seq      # 依次试一组宽度，每个 7 秒
    python lshape_width.py --restore
"""
import struct
import sys
import time

import hid

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
SET_AUX_MODE = 0x3B
OPEN_FILE, WRITE_FILE, CLOSE_FILE = 0x25, 0x26, 0x28
AUX_FILE = "BW_TEXT.ABW"


def open_dev():
    for d in hid.enumerate(0x4D58, 0x5748):
        if d.get("usage_page") == 0xFF60 and d.get("usage") == 0x61:
            dev = hid.device(); dev.open_path(d["path"]); return dev
    return None


def xfer(dev, msg, timeout_ms=900):
    body = bytes(msg) + b"\x00" * (MSG_LEN - len(msg))
    dev.write(b"\x00" + body)
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        r = dev.read(MSG_LEN, timeout_ms=int(timeout_ms))
        if r:
            return bytes(r)
    return b""


def ack(r, cmd):
    return len(r) >= 3 and r[0] == PREFIX and r[1] == cmd and r[2] == OK


def lshape(w, h, extra=0):
    """第 0 行 + 第 0 列点亮；extra>0 时在最右侧再画一条短竖线做标尺。"""
    bits = [0] * (w * h)
    for x in range(w):
        bits[x] = 1                     # 顶边
    for y in range(h):
        bits[y * w] = 1                 # 左边
    for y in range(h):                  # 右侧标尺（靠近右边）
        xx = w - 2
        if 0 <= xx < w:
            bits[y * w + xx] = 1
    return bits


def upload(dev, w, h, msb=False, real_size=True):
    bits = lshape(w, h)
    data = bytearray()
    for i in range(len(bits) // 8):
        b = 0
        for bit in range(8):
            if bits[i * 8 + bit]:
                b |= (1 << (7 - bit)) if msb else (1 << bit)
        data.append(b)
    HDR = "<4s2HI4H"; hs = struct.calcsize(HDR); off = hs + 2
    size = off + (len(data) if real_size else w * h * 2)
    payload = (struct.pack(HDR, b"AUXI", hs, off, size, w, h, 2, 1)
               + struct.pack("<H", 0) + bytes(data))

    r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0) + AUX_FILE.encode())
    if not ack(r, OPEN_FILE):
        return False, "打开失败(%s)" % (r.hex(" ")[:20] if r else "超时")
    idx = r[3]; cur = 0
    while cur < len(payload):
        ch = payload[cur:cur + 24]
        r = xfer(dev, struct.pack("<BBBBI", PREFIX, WRITE_FILE, idx, len(ch), cur) + ch)
        if not ack(r, WRITE_FILE):
            return False, "写入失败@%d/%d (%s)" % (cur, len(payload),
                                                  r.hex(" ")[:20] if r else "超时")
        cur += len(ch)
    r = xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, idx))
    if not ack(r, CLOSE_FILE):
        return False, "关闭失败"
    r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 0))
    if not ack(r, SET_AUX_MODE):
        return False, "切模式失败"
    return True, "%dx%d 数据=%d 总=%d" % (w, h, len(data), len(payload))


def main():
    dev = open_dev()
    if not dev:
        print("设备未找到"); return 2
    if "--restore" in sys.argv:
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 5))
        print("已恢复设备自带画面: %s" % ("OK" if ack(r, SET_AUX_MODE) else "失败"))
        dev.close(); return 0

    if "--seq" in sys.argv:
        cands = [(160, 64), (160, 80), (128, 64), (128, 48), (120, 48),
                 (100, 40), (96, 32), (80, 30), (240, 120), (256, 64)]
        secs = 6
        print("⌐ 形图案扫描，每个 %d 秒。" % secs)
        print("请重点看【左边那条竖线】：是竖直的，还是斜的？")
        print()
        for w, h in cands:
            ok, info = upload(dev, w, h)
            print("  -> %-28s %s" % (info, "OK" if ok else "失败"))
            for k in range(secs, 0, -1):
                print("     停留 %2d 秒..." % k, end="\r")
                time.sleep(1)
            print()
        print()
        print("=== 结束 ===")
        print("请告诉我：")
        print("  1) 哪一次的竖线是**竖直的**？（那就是正确宽度）")
        print("  2) 若全斜，斜的那个大约从左上角走到右边的什么位置？")
        print("     （例如：从左上角一直斜到右下角）")
        dev.close(); return 0

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    size = args[0] if args else "160x64"
    w, h = (int(x) for x in size.split("x"))
    ok, info = upload(dev, w, h, msb=("--msb" in sys.argv))
    print("%s -> %s" % (info, "OK" if ok else "失败"))
    dev.close(); return 0


if __name__ == "__main__":
    sys.exit(main())
