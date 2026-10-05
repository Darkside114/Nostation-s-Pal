"""读回设备上的屏幕文件，核对写入内容与头部参数。

用途：屏幕显示不对时，先确认"设备里到底存了什么"，避免盲目猜格式。
只做只读操作。
"""
import struct
import sys
import time

import hid

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
OPEN_FILE, READ_FILE, CLOSE_FILE = 0x25, 0x27, 0x28
GET_FILE_INFO = 0x24
FILES = ["BW_TEXT.ABW"]


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


def main():
    dev = open_dev()
    if not dev:
        print("设备未找到")
        return 2

    print("=== 文件列表 ===")
    for idx in range(0, 6):
        r = xfer(dev, [PREFIX, GET_FILE_INFO, idx])
        if not ack(r, GET_FILE_INFO):
            break
        name = r[3:16].split(b"\x00")[0].decode("utf-8", "replace")
        size = struct.unpack("<I", r[16:20])[0]
        print("  [%d] %-20s %d 字节" % (idx, name, size))

    for fname in FILES:
        print()
        print("=== 读 %s ===" % fname)
        r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 1)
                 + fname.encode())
        if not ack(r, OPEN_FILE):
            print("  打开失败:", r.hex(" ") if r else "(超时)")
            continue
        index = r[3]
        print("  句柄 index =", index)

        blob = bytearray()
        offset = 0
        while offset < 4096:
            r = xfer(dev, struct.pack("<BBBBI", PREFIX, READ_FILE, index,
                                      24, offset))
            if not ack(r, READ_FILE):
                print("  读到偏移 %d 时停止 (%s)" % (
                    offset, r.hex(" ") if r else "超时"))
                break
            size = r[3]
            if size == 0:
                break
            chunk = r[8:8 + size]
            blob += chunk
            offset += size
            if size < 24:
                break
        xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, index))

        print("  实际读到 %d 字节" % len(blob))
        if len(blob) < 20:
            print("  内容太短，无法解析头部")
            dev.close()
            return 0
        hdr = struct.unpack("<4s2HI4H", blob[:20])
        print("  --- 头部（pack_anim_header 布局）---")
        print("    magic      = %r" % hdr[0])
        print("    hdr_size   = %d" % hdr[1])
        print("    offset     = %d" % hdr[2])
        print("    file_size  = %d" % hdr[3])
        print("    width      = %d" % hdr[4])
        print("    height     = %d" % hdr[5])
        print("    fmt        = %d" % hdr[6])
        print("    total      = %d" % hdr[7])
        w, h = hdr[4], hdr[5]
        need = 20 + 2 + (w * h // 8)
        print("    按此尺寸应有 %d 字节，实际 %d 字节  %s"
              % (need, len(blob), "一致" if need == len(blob) else "不一致!"))
        if len(blob) > 22:
            print("  --- 像素数据前 24 字节 ---")
            print("    " + blob[22:46].hex(" "))
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
