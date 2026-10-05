"""一次性扫完所有满足 562 字节的候选尺寸（最保守的写入流程）。

== 结论汇总（重要）==
  官方 amk/aux_display.py 的上传流程：
      packed = pack_anim_header(w, h, "ABIT", 1) + struct.pack("<H", 0) + frames
      len(frames) = w*h/8                  # 每字节 8 像素，低位在前
      open_anim_file("BW_TEXT.ABW") -> 每片 24 字节 -> close -> apply_aux_mode(0)

  * magic = "ABIT"（官方源码确认；"AUXI" 是另一个模式用的）
  * 官方源码里 aux 尺寸写死 70x40，但官方工具实际写出 562 字节，
    而 70x40 只有 372 字节 —— 说明官网用设备上报的尺寸覆盖了默认值。
    562 - 20(头部) - 2(帧时长) = 540 字节 = 4320 位 = w*h
  * 4320 的因数组合里（宽为 8 的倍数）只有下面这些，全部生成 562 字节。

== 为什么这个脚本特别保守 ==
  实测设备在**写入失败后会锁死文件系统**（句柄恒返回 48，所有 WRITE 返回
  0x55，且软件无法恢复，必须断电重插）。而多次实测发现：
  **在写入之间插入额外的探测命令（如反复 OPEN/CLOSE 做健康检查）
  更容易触发锁死。** 所以这里：
      * 写入之间不做任何额外命令
      * 每个候选独立 OPEN -> 连续 WRITE -> CLOSE -> 切模式
      * 一旦某个候选写入失败，立即停止（避免继续破坏）

用法：
    python sweep_final.py            # 依次上传全部候选
    python sweep_final.py --restore  # 恢复设备自带画面（模式 5）
"""
import os
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
MAGIC = "ABIT"                  # 官方 magic
CHUNK = 24                      # 官方分片大小：24 字节

# 4320 位 = 540 字节的全部合理因数组合（宽为 8 的倍数）
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
    """四角实心方块（判断缩放/裁切）+ 旁写尺寸标签（判断是否我的数据）。"""
    px = [[0] * w for _ in range(h)]
    b = max(2, min(w, h) // 8)
    for y in range(min(b, h)):
        for x in range(min(b, w)):
            px[y][x] = 1
            px[y][w - 1 - x] = 1
            px[h - 1 - y][x] = 1
            px[h - 1 - y][w - 1 - x] = 1
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


def upload(dev, w, h):
    label = "%dX%d" % (w, h)
    data = pack(frame(w, h, label), w, h)
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
            # 尽力关一次，但不再做多余动作
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

    secs = 10
    print("候选（全部 = 540 字节数据 + ABIT magic，总 562 字节）：")
    for w, h in CANDIDATES:
        print("   %3dx%-4d 比例 %.2f:1" % (w, h, w / h))
    print()
    print("每个显示 %d 秒。请记住哪一版出现了【四角实心方块】。" % secs)
    print()

    ok_count = 0
    for i, (w, h) in enumerate(CANDIDATES):
        ok, why, total = upload(dev, w, h)
        print("  [%d] %3dx%-4d 载荷=%d -> %s %s"
              % (i, w, h, total, "OK" if ok else "失败", "" if ok else why))
        if not ok:
            print("      !! 写入失败，设备可能已锁死，停止后续候选")
            break
        ok_count += 1
        for k in range(secs, 0, -1):
            print("      停留 %2d 秒..." % k, end="\r")
            time.sleep(1)
        print()

    print()
    print("=== 结束（成功上传 %d 个）===" % ok_count)
    print("请告诉我哪一版显示了【四角实心方块 + 尺寸文字】。")
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
