"""穷举像素编码：用「实心矩形」图案，把位序/像素位数一次排除掉。

== 为什么要用实心矩形 ==
  之前用线条/文字判断，结果受位序影响很大，很难区分"尺寸错"和"位序错"。
  实心矩形（左上角一块 + 右下角一块）对位序**不敏感**：
  就算每个字节内部顺序反了，实心块看起来仍然是实心块，只是位置可能镜像。
  所以它能干净地区分：
      * 看到两块实心方块 -> 尺寸和"像素位数"都对，剩下的只是位序/镜像问题
      * 看到点阵/虚线    -> 像素位数不对（1bpp vs 2bpp）
      * 看到方块变形      -> 宽度或高度不对

== 本轮要排除的变量 ==
  1. 1bpp（每字节 8 像素，低位在前 / 高位在前）
  2. 2bpp（每字节 4 像素，低位在前 / 高位在前），亮度 11 或 01

用法：
    python test_solid.py            # 依次试全部组合
    python test_solid.py --restore
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
MAGIC = "ABIT"
CHUNK = 24


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


def blocks(w, h):
    """左上 1/3 与右下 1/3 两块实心矩形，中间留空。"""
    px = [[0] * w for _ in range(h)]
    bw, bh = max(1, w // 3), max(1, h // 3)
    for y in range(bh):
        for x in range(bw):
            px[y][x] = 1
    for y in range(h - bh, h):
        for x in range(w - bw, w):
            px[y][x] = 1
    return px


def pack_1bpp(px, w, h, msb=False):
    flat = [px[y][x] for y in range(h) for x in range(w)]
    out = bytearray()
    for i in range(len(flat) // 8):
        v = 0
        for bit in range(8):
            if flat[i * 8 + bit]:
                v |= (1 << (7 - bit)) if msb else (1 << bit)
        out.append(v)
    return bytes(out)


def pack_2bpp(px, w, h, bright=0b11, msb=False):
    flat = [px[y][x] for y in range(h) for x in range(w)]
    while len(flat) % 4:
        flat.append(0)
    out = bytearray()
    for i in range(len(flat) // 4):
        v = 0
        for k in range(4):
            val = bright if flat[i * 4 + k] else 0
            shift = (3 - k) * 2 if msb else k * 2
            v |= (val & 0b11) << shift
        out.append(v)
    return bytes(out)


def upload(dev, w, h, data, tag):
    HDR = "<4s2HI4H"
    hs = struct.calcsize(HDR)
    off = hs + 2
    payload = (struct.pack(HDR, MAGIC.encode(), hs, off, off + len(data),
                           w, h, 2, 1) + struct.pack("<H", 0) + data)
    r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0)
             + AUX_FILE.encode())
    if not ack(r, OPEN_FILE):
        return False, "打开失败", len(payload)
    idx = r[3]
    cur = 0
    while cur < len(payload):
        ch = payload[cur:cur + CHUNK]
        r = xfer(dev, struct.pack("<BBBBI", PREFIX, WRITE_FILE, idx,
                                  len(ch), cur) + ch)
        if not ack(r, WRITE_FILE):
            xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, idx), 300)
            return False, "写入失败@%d" % cur, len(payload)
        cur += len(ch)
    xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, idx))
    r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 0))
    return ack(r, SET_AUX_MODE), "已显示", len(payload)


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

    w, h = 160, 27
    plans = [
        ("1bpp LSB", lambda px: pack_1bpp(px, w, h, False)),
        ("1bpp MSB", lambda px: pack_1bpp(px, w, h, True)),
        ("2bpp 11 LSB", lambda px: pack_2bpp(px, w, h, 0b11, False)),
        ("2bpp 11 MSB", lambda px: pack_2bpp(px, w, h, 0b11, True)),
        ("2bpp 01 LSB", lambda px: pack_2bpp(px, w, h, 0b01, False)),
    ]

    secs = 6
    print("画布 %dx%d，图案 = 左上块 + 右下块（实心矩形）" % (w, h))
    print("每种编码显示 %d 秒。\n" % secs)
    px = blocks(w, h)
    n = 0
    for tag, fn in plans:
        data = fn(px)
        ok, why, total = upload(dev, w, h, data, tag)
        print("  %-13s 数据=%4d 载荷=%d -> %s %s"
              % (tag, len(data), total, "OK" if ok else "失败", "" if ok else why))
        if not ok:
            print("     !! 设备锁死，停止")
            break
        n += 1
        for k in range(secs, 0, -1):
            print("     停留 %2d 秒..." % k, end="\r")
            time.sleep(1)
        print()
    print()
    print("=== 结束（成功 %d 个）===" % n)
    print("请告诉我：哪一版看到了【两块实心方块】（左上 + 右下）？")
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
