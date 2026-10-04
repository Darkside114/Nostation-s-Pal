"""列 NOSTATION 内置文件系统里的所有文件，找温湿度/屏幕相关的资源文件。

GET_FILE_SYSTEM_INFO(0x23) 返回：
    fd 23 aa 01 00 00 ff 00 00 80 ff 00 00 ...
              ^^          ^^^^^
              可能是文件数量   可能是容量（0x00ff8000 = 16744448）
GET_FILE_INFO(0x24) 逐条取文件信息（名字 + 大小）。

只读操作，不写任何东西。
"""
import struct
import sys
import time

import hid

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
UNSUPPORTED = 0x55
GET_FS_INFO = 0x23
GET_FILE_INFO = 0x24


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


def main():
    dev = open_dev()
    if not dev:
        print("设备未找到")
        return 2
    print("已连接 NOSTATION\n")

    print("=== GET_FILE_SYSTEM_INFO (0x23) ===")
    r = xfer(dev, [PREFIX, GET_FS_INFO])
    print("  %s" % (r.hex(" ") if r else "(超时)"))
    if r and len(r) >= 12 and r[2] == OK:
        # 猜测字段含义，把几种可能的解释都打出来
        print("  原始字段（按 AMK 常见布局解析）:")
        print("    r[3]      = %d" % r[3])
        print("    r[4:8]    = %s  (小端 uint32 = %d)"
              % (r[4:8].hex(" "), struct.unpack("<I", r[4:8])[0]))
        print("    r[8:12]   = %s  (小端 uint32 = %d)"
              % (r[8:12].hex(" "), struct.unpack("<I", r[8:12])[0]))
        count_guess = r[3] or struct.unpack("<I", r[4:8])[0]
        print("  用 r[3]=%d 当文件数尝试列举" % count_guess)

    print()
    print("=== GET_FILE_INFO (0x24) 逐条列举 ===")
    print("  试 index 0..24：")
    found = []
    for idx in range(25):
        r = xfer(dev, [PREFIX, GET_FILE_INFO, idx])
        if not r:
            print("    [%2d] (超时)" % idx)
            continue
        if len(r) >= 3 and r[0] == PREFIX and r[1] == GET_FILE_INFO:
            if r[2] == UNSUPPORTED:
                print("    [%2d] 0x55 不支持" % idx)
                continue
            if r[2] != OK:
                print("    [%2d] 状态 0x%02X" % (idx, r[2]))
                continue
            # 解析：通常 r[3]=名字长度 或 r[3:..]=名字
            raw = r[3:]
            name_len = raw[0]
            name = ""
            if 0 < name_len <= 20:
                try:
                    name = raw[1:1 + name_len].decode("utf-8", "replace")
                except Exception:
                    name = raw[1:1 + name_len].hex(" ")
            if not name:
                # 退而求其次：从 r[3] 开始找可打印串
                m = raw.split(b"\x00")[0]
                try:
                    name = m.decode("ascii", "replace")
                except Exception:
                    name = ""
            size = struct.unpack("<I", r[16:20])[0] if len(r) >= 20 else 0
            found.append((idx, name, size))
            print("    [%2d] name=%-24r size=%d   原文=%s"
                  % (idx, name, size, r.hex(" ")))
        else:
            print("    [%2d] 异常应答 %s" % (idx, r.hex(" ")))

    print()
    print("=== 汇总 ===")
    if found:
        for idx, name, size in found:
            print("  %-24s %8d 字节" % (name, size))
    else:
        print("  没取到文件列表")

    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
