"""用官方设备定义里的权威尺寸（108x40）测试辅助屏。

== 权威参数的来源 ==
  用 Vial 协议从设备读出的设备定义（lzma 压缩 JSON）里写着：
      "amkFeature": [
          "datetime",
          "aux_display",
          {"aux_display_params": {"width": 108, "height": 40, "mode": 0}}
      ]
  见 tools/read_vial_definition.py（它能把这台设备的定义完整读出来）。

== 之前的错误 ==
  我一直假设"每行必须是字节对齐、宽度必须是 8 的倍数"，
  于是把所有宽度非 8 倍数的候选（含正确的 108）都筛掉了，
  转而试 144x30 / 120x36 / 96x45 / 80x54 / 160x27 …全都不对。

  108x40 = 4320 像素，1 位/像素 -> 540 字节，正好等于观测到的官方数据量。
  但 108 不是 8 的倍数，所以有两种可能的排布：
      A) 每行补齐到字节边界：每行 14 字节（112 位，含 4 位填充）-> 560 字节
      B) 连续位流，行与行之间跨界                                      -> 540 字节
  观测到的官方数据量是 540 字节，指向 B；但这里两种都试，用显示结果定论。

用法：
    python test_108x40.py            # 依次试 B / A 两种排布
    python test_108x40.py --restore
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

W, H = 108, 40                 # 官方定义里的权威尺寸


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


def frame(w, h):
    """外框 + 四角加粗 + 中间十字。便于一眼判断是否完整对齐。"""
    px = [[0] * w for _ in range(h)]
    for x in range(w):
        px[0][x] = 1
        px[h - 1][x] = 1
    for y in range(h):
        px[y][0] = 1
        px[y][w - 1] = 1
    # 四角 5x5 实心块
    for y in range(5):
        for x in range(5):
            px[y][x] = 1
            px[y][w - 1 - x] = 1
            px[h - 1 - y][x] = 1
            px[h - 1 - y][w - 1 - x] = 1
    # 中间十字
    cy, cx = h // 2, w // 2
    for x in range(cx - 8, cx + 9):
        if 0 <= x < w:
            px[cy][x] = 1
    for y in range(cy - 6, cy + 7):
        if 0 <= y < h:
            px[y][cx] = 1
    return px


def pack_stream(px, w, h, msb=False):
    """连续位流：像素按行优先顺序排，行与行之间不补齐（总 w*h/8 字节）。"""
    flat = [px[y][x] for y in range(h) for x in range(w)]
    out = bytearray()
    for i in range(len(flat) // 8):
        v = 0
        for bit in range(8):
            if flat[i * 8 + bit]:
                v |= (1 << (7 - bit)) if msb else (1 << bit)
        out.append(v)
    return bytes(out)


def pack_padded(px, w, h, msb=False):
    """每行补齐到字节边界（每行 ceil(w/8) 字节）。"""
    out = bytearray()
    for y in range(h):
        row = [px[y][x] for x in range(w)]
        while len(row) % 8:
            row.append(0)
        for i in range(len(row) // 8):
            v = 0
            for bit in range(8):
                if row[i * 8 + bit]:
                    v |= (1 << (7 - bit)) if msb else (1 << bit)
            out.append(v)
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

    px = frame(W, H)
    plans = [
        ("连续位流 LSB", pack_stream(px, W, H, False)),
        ("连续位流 MSB", pack_stream(px, W, H, True)),
        ("行对齐   LSB", pack_padded(px, W, H, False)),
        ("行对齐   MSB", pack_padded(px, W, H, True)),
    ]
    print("权威尺寸 %dx%d（来自设备定义 aux_display_params）" % (W, H))
    print("图案：外框 + 四角实心块 + 中间十字\n")
    secs = 8
    for tag, data in plans:
        ok, why, total = upload(dev, W, H, data)
        print("  %-12s 数据=%4d 载荷=%4d -> %s %s"
              % (tag, len(data), total, "OK" if ok else "失败", "" if ok else why))
        if not ok:
            print("     !! 设备锁死，停止")
            break
        for k in range(secs, 0, -1):
            print("     停留 %2d 秒..." % k, end="\r")
            time.sleep(1)
        print()
    print()
    print("=== 结束 ===")
    print("请告诉我：哪一版出现了【完整外框 + 四角方块 + 中间十字】？")
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
