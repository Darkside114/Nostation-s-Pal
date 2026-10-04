"""试写「传感器/显示」类的 SET 命令，看设备接不接受（找温湿度的写入通道）。

背景：用户说 NOSTATION 屏幕自带温湿度显示，只需替换数字。
但 0x11/0x13/0x15/0x17/0x19 这些 GET 全部返回 0x55（不支持）。
所以试探它们的 SET 侧（0x12/0x14/0x16/0x18/0x1A）以及 DISPLAY_CONTROL(0x2A)。

判据：0xAA = 接受了这条命令；0x55 = 不支持。
安全性：只在"不支持"时有副作用（等于没做）。为避免真的乱改设备，
本脚本默认只做**试写探测**，并在最后把 AUX 模式设回时间模式(1)。
"""
import struct
import sys
import time

import hid

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
UNSUPPORTED = 0x55
SET_AUX_MODE = 0x3B


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


def send(dev, cmd, payload=b""):
    r = xfer(dev, bytes([PREFIX, cmd]) + payload)
    if not r:
        return None, "(超时)"
    if len(r) >= 3 and r[0] == PREFIX and r[1] == cmd:
        if r[2] == OK:
            return r, "ACK 接受"
        if r[2] == UNSUPPORTED:
            return r, "0x55 不支持"
        return r, "状态 0x%02X" % r[2]
    return r, "应答格式异常"


def main():
    dev = open_dev()
    if not dev:
        print("设备未找到")
        return 2
    print("已连接 NOSTATION\n")

    # 先确认基线：这些 GET 都不支持
    print("=== 基线（GET 侧）===")
    for cmd, name in ((0x11, "GET_RT_SENS"), (0x13, "GET_TOP_SENS"),
                      (0x15, "GET_BTM_SENS"), (0x17, "GET_APC_SENS"),
                      (0x19, "GET_NOISE_SENS"), (0x2A, "DISPLAY_CONTROL")):
        r, why = send(dev, cmd)
        print("  0x%02X %-18s %s" % (cmd, name, why))

    print()
    print("=== 试探 SET 侧 ===")
    # 每对 (命令, 名称, 负载) —— 用一个普通数值，温度 25 / 湿度 60
    trials = [
        (0x12, "SET_RT_SENS",    bytes([25])),
        (0x14, "SET_TOP_SENS",   bytes([25])),
        (0x16, "SET_BTM_SENS",   bytes([25])),
        (0x18, "SET_APC_SENS",   bytes([25])),
        (0x1A, "SET_NOISE_SENS", bytes([60])),
        # DISPLAY_CONTROL：试着带一个子命令 0
        (0x2A, "DISPLAY_CONTROL+0", bytes([0])),
        (0x2A, "DISPLAY_CONTROL+1", bytes([1])),
        (0x2A, "DISPLAY_CONTROL+2", bytes([2])),
        (0x2A, "DISPLAY_CONTROL+3", bytes([3])),
    ]
    accepted = []
    for cmd, name, payload in trials:
        r, why = send(dev, cmd, payload)
        print("  0x%02X %-20s %s   %s" % (
            cmd, name, why, r.hex(" ") if r else ""))
        if why.startswith("ACK"):
            accepted.append((name, cmd, payload))

    print()
    print("=== 结果 ===")
    if accepted:
        print("  以下命令被设备**接受**，值得进一步研究：")
        for name, cmd, payload in accepted:
            print("    %-20s (0x%02X) 负载=%s" % (name, cmd, payload.hex(" ")))
    else:
        print("  没有任何 '设置传感器数值' 的命令被接受。")
        print("  => 结论：NOSTATION 固件没有提供'由软件设置温湿度数值'的通道。")

    print()
    print("=== 收尾：把显示模式设回时间模式(1) ===")
    r, why = send(dev, SET_AUX_MODE, bytes([1]))
    print("  SET_AUX_MODE(1): %s" % why)

    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
