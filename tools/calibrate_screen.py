"""屏幕定标工具：用一次上传同时确定「屏幕尺寸」和「像素位序」。

原理：
  * 画一个**粗边框**。如果宽度猜对了，边框会闭合成一个规整矩形；
    如果宽度猜错了，每行的字节错位，边框会断成锯齿/斜线，一眼就能看出来。
  * 边框**底边中间留一个缺口**，用于判断上下和左右方向（否则矩形旋转/镜像看不出来）。
  * 边框内部写大字 "A"/"B"/"C" 标注当前假设的尺寸，方便你直接报出哪次是对的。

用法：
    python calibrate_screen.py 70x40        # 上传 70x40 的定标图案
    python calibrate_screen.py 80x30
    python calibrate_screen.py 70x40 --msb  # 试另一种位序
    python calibrate_screen.py --restore    # 恢复时间显示
"""
import struct
import sys
import time

import hid

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
GET_AUX_MODE, SET_AUX_MODE = 58, 59
OPEN_FILE, WRITE_FILE, CLOSE_FILE = 37, 38, 40
AUX_FILE = "BW_TEXT.ABW"

FONT = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    " ": ["00000"] * 7,
}


def open_dev():
    for d in hid.enumerate(0x4D58, 0x5748):
        if d.get("usage_page") == 0xFF60 and d.get("usage") == 0x61:
            dev = hid.device()
            dev.open_path(d["path"])
            return dev
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


def pack_header(width, height, magic, total):
    ANIM_HDR = "<4s2HI4H"
    hdr = struct.calcsize(ANIM_HDR)
    offset = hdr + 2 * total
    size = offset + total * width * height * 2
    return struct.pack(ANIM_HDR, magic.encode(), hdr, offset, size,
                       width, height, 2, total)


def make_calibration(w, h, label, msb=False):
    """粗边框 + 底边中间缺口 + 内部大号尺寸标记。"""
    px = [[0] * w for _ in range(h)]
    t = 2 if min(w, h) > 34 else 1          # 边框粗细，小屏用 1 以免吃掉全部空间

    for y in range(h):
        for x in range(w):
            if x < t or x >= w - t or y < t or y >= h - t:
                px[y][x] = 1
    # 底边中间开一个缺口，用来判断哪边是"下"
    gap = max(4, w // 8)
    for y in range(h - t, h):
        for x in range((w - gap) // 2, (w + gap) // 2):
            px[y][x] = 0

    # 内部写尺寸标记，尽量塞满剩余空间
    text = label
    for scale in (2, 1):
        glyphs = [FONT.get(c, FONT[" "]) for c in text.upper()]
        text_w = sum(6 * scale for _ in glyphs) - scale
        text_h = 7 * scale
        if text_w <= w - 2 * t - 2 and text_h <= h - 2 * t - 2:
            break
    ox = max(t, (w - text_w) // 2)
    oy = max(t, (h - text_h) // 2)
    for gi, g in enumerate(glyphs):
        for gy in range(7):
            for gx in range(5):
                if g[gy][gx] != "1":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        x = ox + gi * 6 * scale + gx * scale + sx
                        y = oy + gy * scale + sy
                        if 0 <= x < w and 0 <= y < h and px[y][x] == 0:
                            px[y][x] = 1

    flat = [px[y][x] for y in range(h) for x in range(w)]
    out = bytearray()
    for i in range(len(flat) // 8):
        b = 0
        for bit in range(8):
            if flat[i * 8 + bit]:
                b |= (1 << (7 - bit)) if msb else (1 << bit)
        out.append(b)
    return bytes(out)


def upload(dev, width, height, msb):
    label = "%dx%d" % (width, height)
    pix = make_calibration(width, height, label, msb)
    payload = pack_header(width, height, "ABIT", 1) + struct.pack("<H", 0) + pix
    print("  图案 %s  位序=%s  载荷 %d 字节" % (label, "MSB" if msb else "LSB", len(payload)))

    r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0) + AUX_FILE.encode())
    if not ack(r, OPEN_FILE):
        print("  !! 打开文件失败: %s" % (r.hex(" ") if r else "(超时)"))
        return False
    index = r[3]
    cur = 0
    while cur < len(payload):
        chunk = payload[cur:cur + 24]
        r = xfer(dev, struct.pack("<BBBBI", PREFIX, WRITE_FILE, index, len(chunk), cur) + chunk)
        if not ack(r, WRITE_FILE):
            print("  !! 写入失败于偏移 %d" % cur)
            return False
        cur += len(chunk)
    xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, index))
    r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 0))
    print("  上传完成，已切到自定义显示 (ACK=%s)" % ack(r, SET_AUX_MODE))
    return True


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    msb = "--msb" in sys.argv

    dev = open_dev()
    if not dev:
        print("设备未找到")
        return 2

    if "--restore" in sys.argv:
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 1))
        print("已切回时间显示: %s" % ("OK" if ack(r, SET_AUX_MODE) else r.hex(" ")))
        dev.close()
        return 0

    # ---- 轮换模式：自动依次上传多个候选尺寸，盯着屏幕看哪个是规整矩形 ----
    if "--seq" in sys.argv:
        cands = [(80, 30), (70, 40), (80, 80), (60, 60)]
        secs = 9
        print("轮换测试，每个尺寸停留 %d 秒，共 %d 个候选：" % (secs, len(cands)))
        for w, h in cands:
            print("    %dx%d" % (w, h))
        print()
        for w, h in cands:
            print(">>> 现在上传 %dx%d ..." % (w, h))
            upload(dev, w, h, msb)
            for k in range(secs, 0, -1):
                print("    停留中 %2d 秒..." % k, end="\r")
                time.sleep(1)
            print()
        print()
        print("=== 轮换结束 ===")
        print("请告诉我：哪一个尺寸显示成了**闭合的规整矩形**？")
        print("（屏幕里是一个尺寸标记，例如 80X30 或 70X40）")
        dev.close()
        return 0

    size = args[0] if args else "70x40"
    w, h = (int(x) for x in size.split("x"))
    print("上传定标图案 %dx%d ..." % (w, h))
    upload(dev, w, h, msb)
    dev.close()
    print()
    print(">>> 请看屏幕：应该是一个**闭合的粗边框矩形**，里面写着 %s，" % size)
    print(">>> 底边中间有一个缺口。")
    print(">>> 如果边框是断的/斜的/呈锯齿状，说明尺寸不对，请告诉我它的形状。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
