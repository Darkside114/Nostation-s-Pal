"""设备健康检查（只读，不破坏任何数据）。

为什么单独做这个：之前用"写入 8 字节看能不能成功"来判断设备是否卡死，
但那个写法**会把屏幕文件覆盖成 8 字节**，等于每次体检都破坏一次现场。
现在改成纯只读判断：

  设备的文件句柄是**每次 OPEN 递增分配**的（实测 0,1,2,3...）。
  正常工作时返回的是小数值；一旦文件系统卡死，会一直返回
  一个固定的大值（实测 48），并且所有 WRITE 都返回 0x55。

所以只要看 OPEN 返回的句柄是否"像正常递增的小值"即可，
无需实际写入。

用法：
    python device_health.py
"""
import struct
import sys
import time

import hid

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
UNSUPPORTED = 0x55
GET_FS_INFO, GET_FILE_INFO = 0x23, 0x24
GET_AUX_MODE, SET_AUX_MODE = 0x3A, 0x3B
OPEN_FILE, CLOSE_FILE = 0x25, 0x28
AUX_FILE = "BW_TEXT.ABW"

# 卡死状态下 OPEN 会一直返回这个值（实测）
SUSPECT_STUCK_HANDLE = 48


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

    print("=== 文件系统 ===")
    r = xfer(dev, [PREFIX, GET_FS_INFO])
    print("  FS info:", r.hex(" ") if r else "(超时)")
    count = r[3] if len(r) > 3 else None

    print("=== 文件列表 ===")
    for i in range(16):
        r = xfer(dev, [PREFIX, GET_FILE_INFO, i])
        if not (len(r) > 20 and r[2] == OK):
            break
        name = r[3:16].split(b"\x00")[0].decode("utf-8", "replace")
        size = struct.unpack("<I", r[16:20])[0]
        print("  [%2d] %-20s %6d 字节" % (i, name, size))

    print("=== aux 模式 ===")
    r = xfer(dev, [PREFIX, GET_AUX_MODE])
    print("  模式 =", r[3] if len(r) > 3 else "?")

    print("=== 句柄分配探测（只 OPEN 后立刻 CLOSE，不写入）===")
    handles = []
    for k in range(3):
        r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0)
                 + AUX_FILE.encode())
        if len(r) > 2 and r[2] == OK:
            idx = r[3]
            handles.append(idx)
            xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, idx), 400)
        else:
            print("  OPEN 失败:", r.hex(" ") if r else "(超时)")
            break
        time.sleep(0.2)
    print("  返回句柄:", handles)

    dev.close()

    print()
    stuck = bool(handles) and all(h == SUSPECT_STUCK_HANDLE for h in handles)
    if stuck:
        print("结论: 文件系统疑似卡死（句柄恒为 %d）。需要给设备断电重插。"
              % SUSPECT_STUCK_HANDLE)
        return 1
    if handles:
        print("结论: 正常（句柄为递增的小值）")
        return 0
    print("结论: 无法判断")
    return 2


if __name__ == "__main__":
    sys.exit(main())
