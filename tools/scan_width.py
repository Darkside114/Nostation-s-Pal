"""宽度扫描：用「斜条纹」反推屏幕真实宽度。

原理：我按宽度 W 把像素排成行。若设备实际按 W' 读，
每一行就相对上一行错位 (W - W') 个像素，累积起来就是斜条纹。
所以**宽度不对时一定是斜的，宽度对了才会闭合成直边图形**。

本脚本用同一个"矩形边框"图案，依次尝试一组宽度，
你只需看哪一次边框是**正的**（不斜）。

用法：
    python scan_width.py --seq          # 依次试下面这组尺寸，每个 7 秒
    python scan_width.py 160x80         # 只试一个
    python scan_width.py --restore      # 恢复设备自带画面
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

FONT = {
    "0": ["01110","10001","10011","10101","11001","10001","01110"],
    "1": ["00100","01100","00100","00100","00100","00100","01110"],
    "2": ["01110","10001","00001","00010","00100","01000","11111"],
    "3": ["11111","00010","00100","00010","00001","10001","01110"],
    "4": ["00010","00110","01010","10010","11111","00010","00010"],
    "5": ["11111","10000","11110","00001","00001","10001","01110"],
    "6": ["00110","01000","10000","11110","10001","10001","01110"],
    "7": ["11111","00001","00010","00100","01000","01000","01000"],
    "8": ["01110","10001","10001","01110","10001","10001","01110"],
    "9": ["01110","10001","10001","01111","00001","00010","01100"],
    "X": ["10001","10001","01010","00100","01010","10001","10001"],
    " ": ["00000"]*7,
}


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


def make(w, h, label):
    px = [[0]*w for _ in range(h)]
    t = 2 if min(w, h) > 34 else 1
    for y in range(h):
        for x in range(w):
            if x < t or x >= w-t or y < t or y >= h-t:
                px[y][x] = 1
    gap = max(4, w//6)
    for y in range(h-t, h):
        for x in range((w-gap)//2, (w+gap)//2):
            px[y][x] = 0
    text = label.upper()
    scale = 1
    for s in (2, 1):
        if sum(6*s for _ in text)-s <= w-2*t and 7*s <= h-2*t:
            scale = s; break
    tw = sum(6*scale for _ in text) - scale
    ox = max(t, (w-tw)//2); oy = max(t, (h-7*scale)//2)
    x = ox
    for ch in text:
        gl = FONT.get(ch, FONT[" "])
        for gy in range(7):
            for gx in range(5):
                if gl[gy][gx] != "1":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        X, Y = x+gx*scale+sx, oy+gy*scale+sy
                        if 0 <= X < w and 0 <= Y < h and px[Y][X] == 0:
                            px[Y][X] = 1
        x += 6*scale
    return px


def upload(dev, w, h, label, real_size=True, msb=False):
    px = make(w, h, label)
    flat = [px[y][x] for y in range(h) for x in range(w)]
    data = bytearray()
    for i in range(len(flat)//8):
        b = 0
        for bit in range(8):
            if flat[i*8+bit]:
                b |= (1 << (7-bit)) if msb else (1 << bit)
        data.append(b)
    HDR = "<4s2HI4H"; hs = struct.calcsize(HDR); off = hs + 2
    size = off + (len(data) if real_size else w*h*2)
    hdr = struct.pack(HDR, b"AUXI", hs, off, size, w, h, 2, 1)
    payload = hdr + struct.pack("<H", 0) + bytes(data)

    r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0) + AUX_FILE.encode())
    if not ack(r, OPEN_FILE):
        return False, "打开失败"
    idx = r[3]; cur = 0
    while cur < len(payload):
        ch = payload[cur:cur+24]
        r = xfer(dev, struct.pack("<BBBBI", PREFIX, WRITE_FILE, idx, len(ch), cur) + ch)
        if not ack(r, WRITE_FILE):
            return False, "写入失败@%d" % cur
        cur += len(ch)
    xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, idx))
    r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 0))
    return ack(r, SET_AUX_MODE), "%dx%d 数据 %d 字节" % (w, h, len(data))


def main():
    dev = open_dev()
    if not dev:
        print("设备未找到"); return 2

    if "--restore" in sys.argv:
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 5))
        print("已恢复设备自带画面: %s" % ("OK" if ack(r, SET_AUX_MODE) else "失败"))
        dev.close(); return 0

    if "--seq" in sys.argv:
        # 宽度从小到大扫；高度按同比例给几个可能值
        cands = [(160, 64), (160, 80), (128, 48), (128, 64), (96, 32),
                 (100, 40), (120, 48), (80, 30)]
        secs = 7
        print("宽度扫描，每个显示 %d 秒。请记住哪一次边框是【正的】：" % secs)
        for w, h in cands:
            ok, info = upload(dev, w, h, "%dX%d" % (w, h))
            print("  -> %-12s 上传=%s" % (info, "OK" if ok else "失败"))
            for k in range(secs, 0, -1):
                print("     停留 %2d 秒..." % k, end="\r")
                time.sleep(1)
            print()
        print()
        print("=== 扫描结束 ===")
        print("请告诉我：哪一次的**边框是正的**（不是斜的）？")
        print("如果全都是斜的，请告诉我斜的方向（左上->右下 还是 左下->右上）。")
        dev.close(); return 0

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    size = args[0] if args else "160x64"
    w, h = (int(x) for x in size.split("x"))
    ok, info = upload(dev, w, h, "%dX%d" % (w, h),
                      msb=("--msb" in sys.argv))
    print("上传 %s -> %s" % (info, "OK" if ok else "失败"))
    dev.close(); return 0


if __name__ == "__main__":
    sys.exit(main())
