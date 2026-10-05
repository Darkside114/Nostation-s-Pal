"""决定性实验：NOSTATION 到底认不认 AMK 的 SET_DATETIME？

做法：
  1. 用 GET_DATETIME(54) 读一次当前时间
  2. 用 SET_DATETIME(55) 写一个刻意不同的时间（例如 2001-02-03 04:05:06）
  3. 再用 GET_DATETIME(54) 读一次
  4. 如果第 3 步变回真实时间或读到的内容变了 → 说明真的在读写
     如果三次都一样（全零/不变）→ 说明这套命令根本没生效

只使用官方的读命令 + 一个写命令；写的是时间，程序本来就在做这件事，安全。
最后**一定**把真实时间写回去，不留副作用。
"""
import datetime
import struct
import sys
import time

import hid

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
GET_DATETIME = 54
SET_DATETIME = 55


def open_dev():
    for d in hid.enumerate(0x4D58, 0x5748):
        if d.get("usage_page") == 0xFF60 and d.get("usage") == 0x61:
            dev = hid.device()
            dev.open_path(d["path"])
            return dev
    return None


def xfer(dev, msg, timeout_ms=900):
    msg = bytes(msg) + b"\x00" * (MSG_LEN - len(msg))
    dev.write(b"\x00" + msg)
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        r = dev.read(MSG_LEN, timeout_ms=int(timeout_ms))
        if r:
            return bytes(r)
    return b""


def show(label, r):
    if not r:
        print("  %-24s (超时无应答)" % label)
        return
    print("  %-24s %s" % (label, r.hex(" ")))
    if len(r) >= 3:
        if r[0] == PREFIX and r[1] in (GET_DATETIME, SET_DATETIME):
            print("       -> 前缀/命令正确; 状态字节 = 0x%02X (%s)" % (
                r[2], "ACK" if r[2] == OK else "非 ACK"))
        else:
            print("       -> 不是 AMK 应答格式 (r[0]=0x%02X r[1]=0x%02X)" % (r[0], r[1]))


def main():
    dev = open_dev()
    if not dev:
        print("设备未找到")
        return 2
    print("已连接 NOSTATION\n")

    print("=== 第 1 步：读时间 GET_DATETIME(54) ===")
    r1 = xfer(dev, [PREFIX, GET_DATETIME])
    show("读时间 #1", r1)

    print("\n=== 第 2 步：写一个刻意不同的时间 SET_DATETIME(55) = 2001-02-03 04:05:06 ===")
    payload = struct.pack(">BBHBBBBBB", PREFIX, SET_DATETIME,
                          2001, 2, 3, 6, 4, 5, 6)
    r2 = xfer(dev, payload)
    show("写时间", r2)

    print("\n=== 第 3 步：再读一次 GET_DATETIME(54) ===")
    r3 = xfer(dev, [PREFIX, GET_DATETIME])
    show("读时间 #2", r3)

    print("\n=== 结论 ===")
    if r1 and r3 and r1 == r3:
        print("  两次读取完全相同 -> 设备没有真正返回时间数据")
    elif r1 and r3:
        print("  两次读取不同 -> 设备确实在读写时间！")
    if r3 and len(r3) > 3:
        y = (r3[2] << 8) | r3[3]
        print("  按 AMK 格式解析读到的年份: %d" % y)

    print("\n=== 收尾：把真实时间写回去 ===")
    now = datetime.datetime.now()
    payload = struct.pack(">BBHBBBBBB", PREFIX, SET_DATETIME,
                          now.year, now.month, now.day, now.isoweekday(),
                          now.hour, now.minute, now.second)
    r4 = xfer(dev, payload)
    show("恢复真实时间", r4)
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
