"""一次性测完所有候选：正确 magic(ABIT) × 全部可能的点阵尺寸。

背景（本文件是最新结论的汇总，务必先读）：

  官方 amk/aux_display.py 的上传流程：
      packed = pack_anim_header(w, h, "ABIT", 1) + struct.pack("<H", 0) + frames
      len(frames) = w*h/8          # 每字节 8 像素，低位在前
      open_anim_file("BW_TEXT.ABW") -> 每片 24 字节写入 -> close -> apply_aux_mode(0)

  * magic 是 **"ABIT"**（不是 "AUXI"；AUXI 是另一个动画模式 auxi_80_30 用的）
  * 官方源码里 aux 尺寸写死 70x40，但那样只有 372 字节，
    而官方工具实际写出 562 字节 —— 说明官网用**设备上报的尺寸**覆盖了默认值。
    562 - 20(头部) - 2(帧时长) = 540 字节 = 4320 位 = w*h
  * 4320 的因数组合里，宽必须是 8 的倍数，可能的只有下面这几个。

用法：
    python sweep_all.py            # 依次上传下面全部候选，每个 6 秒
    python sweep_all.py --restore  # 恢复设备自带画面（模式 5）
"""
import os
import struct
import sys
import time

import hid

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
GET_AUX_MODE, SET_AUX_MODE = 0x3A, 0x3B
OPEN_FILE, WRITE_FILE, CLOSE_FILE = 0x25, 0x26, 0x28
GET_FILE_INFO = 0x24
AUX_FILE = "BW_TEXT.ABW"
MAGIC = "ABIT"                  # 官方用的 magic
CHUNK = 24

# 4320 位的全部合理因数组合（宽为 8 的倍数，高在 8..200 之间）
CANDIDATES = [
    (144, 30), (120, 36), (96, 45), (80, 54),
    (72, 60), (48, 90), (40, 108), (32, 135), (24, 180),
]

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
    "A": ["01110","10001","10001","11111","10001","10001","10001"],
    "B": ["11110","10001","10001","11110","10001","10001","11110"],
    "C": ["01110","10001","10000","10000","10000","10001","01110"],
    "D": ["11100","10010","10001","10001","10001","10010","11100"],
    "E": ["11111","10000","10000","11110","10000","10000","11111"],
    "H": ["10001","10001","10001","11111","10001","10001","10001"],
    "L": ["10000","10000","10000","10000","10000","10000","11111"],
    "O": ["01110","10001","10001","10001","10001","10001","01110"],
    "X": ["10001","10001","01010","00100","01010","10001","10001"],
    " ": ["00000"]*7,
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


def frame(w, h, label):
    """内容：四角实心方块（判断缩放/裁切）+ 左上一个字母（判断是否我的数据）。"""
    px = [[0] * w for _ in range(h)]
    b = max(2, min(w, h) // 8)
    for y in range(min(b, h)):
        for x in range(min(b, w)):
            px[y][x] = 1                      # 左上
            px[y][w - 1 - x] = 1              # 右上
            px[h - 1 - y][x] = 1              # 左下
            px[h - 1 - y][w - 1 - x] = 1      # 右下
    # 左上角四个方块之后写标签
    x0 = b + 2
    for ch in label:
        g = FONT.get(ch, FONT[" "])
        for gy in range(7):
            for gx in range(5):
                if g[gy][gx] != "1":
                    continue
                for sy in range(2):
                    for sx in range(2):
                        X, Y = x0 + gx * 2 + sx, b + 2 + gy * 2 + sy
                        if 0 <= X < w and 0 <= Y < h:
                            px[Y][X] = 1
        x0 += 12
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


def upload(dev, w, h, label):
    px = frame(w, h, label)
    data = pack(px, w, h)
    HDR = "<4s2HI4H"
    hs = struct.calcsize(HDR)
    off = hs + 2
    payload = (struct.pack(HDR, MAGIC.encode(), hs, off, off + len(data),
                           w, h, 2, 1) + struct.pack("<H", 0) + data)

    r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0)
             + AUX_FILE.encode())
    if not ack(r, OPEN_FILE):
        return False, "打开失败", len(payload), None
    idx = r[3]
    try:
        cur = 0
        while cur < len(payload):
            ch = payload[cur:cur + CHUNK]
            r = xfer(dev, struct.pack("<BBBBI", PREFIX, WRITE_FILE, idx,
                                      len(ch), cur) + ch)
            if not ack(r, WRITE_FILE):
                return False, "写入失败@%d" % cur, len(payload), idx
            cur += len(ch)
        xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, idx))
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 0))
        return ack(r, SET_AUX_MODE), "已显示", len(payload), idx
    finally:
        xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, idx), timeout_ms=300)


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

    # 先看看当前状态
    r = xfer(dev, [PREFIX, GET_FILE_INFO, 0])
    if len(r) > 20 and r[2] == OK:
        print("当前 文件[0] %s %d 字节"
              % (r[3:16].split(b"\x00")[0].decode("utf-8", "replace"),
                 struct.unpack("<I", r[16:20])[0]))
    r = xfer(dev, [PREFIX, GET_AUX_MODE])
    print("当前 aux 模式:", r[3] if len(r) > 3 else "?")
    print()
    print("候选（全部满足 540 字节数据 + ABIT magic）：")
    for w, h in CANDIDATES:
        print("   %3dx%-4d 比例 %.2f:1" % (w, h, w / h))
    print()

    secs = 6
    for i, (w, h) in enumerate(CANDIDATES):
        label = "%d%s" % (w, "ABCDEFGH"[i] if i < 8 else "")
        tag = "%dx%d" % (w, h)
        ok, why, total, idx = upload(dev, w, h, tag.replace("x", "X"))
        print("  [%d] %-9s 载荷=%d 句柄=%s -> %s %s"
              % (i, tag, total, idx, "OK" if ok else "失败", "" if ok else why))
        if not ok:
            print("      !! 写入失败，设备可能已锁死，停止后续测试")
            break
        for k in range(secs, 0, -1):
            print("      停留 %2d 秒..." % k, end="\r")
            time.sleep(1)
        print()
        # 每次之后检查句柄是否变成可疑值（卡死征兆）
        rr = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0)
                  + AUX_FILE.encode())
        if len(rr) > 2 and rr[2] == OK:
            h2 = rr[3]
            xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, h2), 300)
            if h2 >= 48:
                print("      !! 句柄=%d（卡死征兆），停止" % h2)
                break

    print()
    print("=== 结束 ===")
    print("请告诉我哪一版显示了【四角实心方块 + 中间的文字标签】。")
    print("（方块在四角 = 尺寸对；只有部分角 = 高度或宽度仍不对）")
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
