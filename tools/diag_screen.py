"""屏幕诊断：用「纯色 / 单线」图案判定极性、位序、宽度。

为什么用这些图案：
  * 整屏点亮 / 整屏熄灭 —— 与宽度、位序都无关，只看固件的极性约定。
      全亮 -> 极性正常；全黑回退 -> 极性相反（需要取反）。
  * 只在第 0 行画满、其余全黑 —— 亮的那条带有多宽，就能反推真实宽度
      （我按 W 排的行，若设备按 W' 读，这条带会在屏幕上斜着走）。
  * 只在第 0 列画满 —— 竖线若变成斜线，斜的斜率直接对应 W/W' 的比值。

用法：
    python diag_screen.py --seq      # 依次显示下列图案，每个 7 秒
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


def header(w, h, magic, total, data_len=None):
    HDR = "<4s2HI4H"
    hs = struct.calcsize(HDR)
    off = hs + 2 * total
    size = (off + data_len) if data_len is not None else (off + total * w * h * 2)
    return struct.pack(HDR, magic.encode(), hs, off, size, w, h, 2, total)


def pack_bits(bits, msb=False):
    out = bytearray()
    for i in range(len(bits) // 8):
        b = 0
        for bit in range(8):
            if bits[i * 8 + bit]:
                b |= (1 << (7 - bit)) if msb else (1 << bit)
        out.append(b)
    return bytes(out)


def pattern(w, h, kind):
    """返回 w*h 的 0/1 位序列。"""
    bits = [0] * (w * h)
    if kind == "all_on":
        bits = [1] * (w * h)
    elif kind == "all_off":
        bits = [0] * (w * h)
    elif kind == "row0":
        for x in range(w):
            bits[x] = 1
    elif kind == "col0":
        for y in range(h):
            bits[y * w] = 1
    elif kind == "top_half":
        for y in range(h // 2):
            for x in range(w):
                bits[y * w + x] = 1
    elif kind == "left_half":
        for y in range(h):
            for x in range(w // 2):
                bits[y * w + x] = 1
    return bits


def upload(dev, w, h, kind, msb=False, real_size=True, magic="AUXI"):
    bits = pattern(w, h, kind)
    data = pack_bits(bits, msb)
    payload = (header(w, h, magic, 1, data_len=(len(data) if real_size else None))
               + struct.pack("<H", 0) + data)
    r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0) + AUX_FILE.encode())
    if not ack(r, OPEN_FILE):
        print("      打开失败")
        return False
    index = r[3]
    cur = 0
    while cur < len(payload):
        chunk = payload[cur:cur + 24]
        r = xfer(dev, struct.pack("<BBBBI", PREFIX, WRITE_FILE, index,
                                  len(chunk), cur) + chunk)
        if not ack(r, WRITE_FILE):
            print("      写入失败 @%d" % cur)
            return False
        cur += len(chunk)
    xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, index))
    r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 0))
    return ack(r, SET_AUX_MODE)


def main():
    dev = open_dev()
    if not dev:
        print("设备未找到")
        return 2

    if "--restore" in sys.argv:
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 5))
        print("已恢复设备自带画面: %s" % ("OK" if ack(r, SET_AUX_MODE) else "失败"))
        dev.close()
        return 0

    w, h = 80, 30
    seq = [
        ("all_on",   False, "整屏点亮（判极性：应全白）"),
        ("all_off",  False, "整屏熄灭（应全黑）"),
        ("row0",     False, "只有第 0 行亮（看这条带多宽/是否斜）"),
        ("col0",     False, "只有第 0 列亮（竖线若斜，斜率反映宽度不匹配）"),
        ("top_half", False, "上半屏亮（应看到上白下黑的分界）"),
        ("col0",     True,  "第 0 列亮 + MSB 位序"),
    ]
    secs = 7
    print("诊断图案序列（%dx%d），每个显示 %d 秒：" % (w, h, secs))
    for kind, msb, desc in seq:
        tag = "%s%s" % (kind, "+MSB" if msb else "")
        print("  -> %-10s %s" % (tag, desc))
        ok = upload(dev, w, h, kind, msb=msb)
        print("      上传=%s" % ("OK" if ok else "失败"))
        for k in range(secs, 0, -1):
            print("      停留 %2d 秒..." % k, end="\r")
            time.sleep(1)
        print()
    print()
    print("=== 序列结束 ===")
    print("请告诉我这几点，我就能算出准确的参数：")
    print("  1) 「整屏点亮」那一步，屏幕是变全白还是几乎没变？")
    print("  2) 「第 0 行亮」那一步，看到的是横带、斜带、还是几个亮点？")
    print("  3) 「第 0 列亮」那一步，竖线是直的还是斜的？斜的话大概多少度？")
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
