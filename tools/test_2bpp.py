"""验证「像素是 2 位而不是 1 位」这个假设。

== 推理依据 ==
官方文件 562 字节 = 20(头部) + 2(帧时长) + 540 字节像素数据 = 4320 位。

  * 若 1 位/像素 -> 4320 像素。我按这个假设试了十几个尺寸，
    最佳结果是「竖线直、横线断/锯齿」，始终不能完整闭合。
  * 若 **2 位/像素** -> 只有 2160 像素。这正是"竖线直但横线断"的典型症状：
    宽度声明正确（列对齐），但每像素多占 1 位，导致行内数据周期性错位，
    横线跨越时断开，而竖线只依赖列偏移所以看起来仍然是直的。

2160 的因数组合（宽为 4 的倍数，因为 2 位/像素时 4 像素 = 1 字节）：
    72x30 / 80x27 / 90x24 / 108x20 / 120x18 / 135x16 / 144x15 ...
  其中 72x30 与 80x27 比例最接近照片量出的屏幕比例。

== 2 位/像素的编码约定 ==
  一个字节 4 个像素，从低位到高位。亮度值 00=黑，11=白（也试 01/10）。

用法：
    python test_2bpp.py            # 依次试多组 (尺寸, 亮度, 布局)
    python test_2bpp.py --restore
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

# 2160 像素（2 位/像素）的候选尺寸
CAND_2BPP = [(72, 30), (80, 27), (90, 24), (108, 20), (120, 18)]
# 1 位/像素的对照
CAND_1BPP = [(160, 27)]


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


def make_shape(w, h):
    """一个完整的外框 + 中间一条竖线。用来判断是否完整对齐。"""
    px = [[0] * w for _ in range(h)]
    for x in range(w):
        px[0][x] = 1
        px[h - 1][x] = 1
    for y in range(h):
        px[y][0] = 1
        px[y][w - 1] = 1
    # 正中竖线
    for y in range(h):
        px[y][w // 2] = 1
    return px


def pack_1bpp(px, w, h):
    flat = [px[y][x] for y in range(h) for x in range(w)]
    out = bytearray()
    for i in range(len(flat) // 8):
        v = 0
        for bit in range(8):
            if flat[i * 8 + bit]:
                v |= (1 << bit)
        out.append(v)
    return bytes(out)


def pack_2bpp(px, w, h, bright=0b11, hi_first=False):
    """每字节 4 像素，每像素 2 位。bright 是"亮"的取值。"""
    flat = [px[y][x] for y in range(h) for x in range(w)]
    # 需要 4 的倍数
    while len(flat) % 4:
        flat.append(0)
    out = bytearray()
    for i in range(len(flat) // 4):
        b = 0
        for k in range(4):
            v = bright if flat[i * 4 + k] else 0
            shift = (3 - k) * 2 if hi_first else k * 2
            b |= (v & 0b11) << shift
        out.append(b)
    return bytes(out)


def upload(dev, w, h, data):
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

    # 图案：完整外框 + 中间竖线。尺寸对且编码对 -> 规整的"日"字形
    plan = []
    for w, h in CAND_2BPP[:3]:
        plan.append((w, h, "2bpp-11", lambda px, w=w, h=h: pack_2bpp(px, w, h, 0b11)))
        plan.append((w, h, "2bpp-01", lambda px, w=w, h=h: pack_2bpp(px, w, h, 0b01)))
    plan.append((CAND_1BPP[0][0], CAND_1BPP[0][1], "1bpp",
                 lambda px, w=CAND_1BPP[0][0], h=CAND_1BPP[0][1]: pack_1bpp(px, w, h)))

    secs = 7
    print("图案：完整外框 + 中间一条竖线（尺寸/编码都对 -> 规整的『日』字形）")
    print("每个显示 %d 秒。\n" % secs)
    n = 0
    for w, h, tag, fn in plan:
        px = make_shape(w, h)
        data = fn(px)
        ok, why, total = upload(dev, w, h, data)
        print("  %-9s %3dx%-3d 像素数据=%4d 载荷=%d -> %s %s"
              % (tag, w, h, len(data), total, "OK" if ok else "失败", "" if ok else why))
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
    print("请告诉我：哪一版出现了【规整的外框 + 中间竖线】？")
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
