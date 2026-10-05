"""宽度标尺：画等间距竖线，用屏幕上数到的线数直接算出真实宽度。

原理：
  我在 w 宽的画布上每 16 像素画一条竖线，共 w/16 条。
  若设备按 w' 读：

    * 只有 w == w' 时，每行的字节数一致，所有竖线都落在同一列 -> 看到 w/16 条平行竖线
    * w != w' 时，每行错位 (w - w') 像素，竖线会逐个横向漂移 -> 看起来是斜的

  所以：
    * 数到的线数 == w/16 且都竖直    -> 宽度就是 w
    * 线是斜的                        -> 记录斜的走向，可以算出 w'
    * 线数变多（比如翻倍）            -> 说明每像素位数不对（2 位/像素）

  为了让"线数"好数，竖线做成 2 像素宽、贯穿全高，并且第一条从 x=0 开始。

用法：
    python width_ruler.py 160x27     # 只测一个
    python width_ruler.py --restore
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
STEP = 16               # 竖线间距


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


def ruler(w, h):
    """每 STEP 像素一条 2 像素宽的竖线，贯穿全高；顶部一条横线做参照。"""
    px = [[0] * w for _ in range(h)]
    x = 0
    while x < w:
        for y in range(h):
            px[y][x] = 1
            if x + 1 < w:
                px[y][x + 1] = 1
        x += STEP
    # 顶部横线：如果宽度正确，它应该是连续直的一条
    for xx in range(w):
        px[0][xx] = 1
    return px


def pack(px, w, h):
    flat = [px[y][x] for y in range(h) for x in range(w)]
    out = bytearray()
    for i in range(len(flat) // 8):
        v = 0
        for bit in range(8):
            if flat[i * 8 + bit]:
                v |= (1 << bit)
        out.append(v)
    return bytes(out)


def upload(dev, w, h):
    data = pack(ruler(w, h), w, h)
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

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    size = args[0] if args else "160x27"
    w, h = (int(v) for v in size.split("x"))
    lines = len(range(0, w, STEP))
    print("画布 %dx%d，每 %d 像素一条竖线 -> 应有 %d 条" % (w, h, STEP, lines))
    ok, why, total = upload(dev, w, h)
    print("上传: %s (%s)  载荷 %d 字节" % ("成功" if ok else "失败", why, total))
    dev.close()
    print()
    print(">>> 请看屏幕并数一下：")
    print(">>>   1) 有几条竖线？")
    print(">>>   2) 它们是竖直的，还是斜的？")
    print(">>>   3) 顶部那条横线是连续的一条，还是断成虚线的？")
    return 0


if __name__ == "__main__":
    sys.exit(main())
