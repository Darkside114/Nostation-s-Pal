"""读 NOSTATION 的传感器/显示相关寄存器，找"温湿度数值"从哪来。

只发 GET 类命令（0x11..0x1A 的 Get 侧、0x2A），不做任何写入。
"""
import sys
import time

import hid

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA

READS = [
    (0x00, "GET_VERSION"),
    (0x03, "GET_RT"),
    (0x11, "GET_RT_SENS"),
    (0x13, "GET_TOP_SENS"),
    (0x15, "GET_BTM_SENS"),
    (0x17, "GET_APC_SENS"),
    (0x19, "GET_NOISE_SENS"),
    (0x23, "GET_FILE_SYSTEM_INFO"),
    (0x2A, "DISPLAY_CONTROL"),
    (0x3A, "GET_AUX_MODE"),
    (0x3C, "GET_RGB_DATA"),
    (0x3D, "GET_RGB_PARAM"),
    (0x3F, "GET_SWITCH_STATE"),
    (0x49, "ESP32_COMMAND"),
]


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
    print("%-6s %-22s %s" % ("命令", "名称", "应答"))
    print("-" * 92)
    for cmd, name in READS:
        r = xfer(dev, [PREFIX, cmd])
        if not r:
            print("0x%02X   %-22s (超时)" % (cmd, name))
            continue
        tag = ""
        if len(r) >= 3 and r[0] == PREFIX and r[1] == cmd:
            tag = "ACK" if r[2] == OK else "状态0x%02X" % r[2]
        print("0x%02X   %-22s %s" % (cmd, name, r.hex(" ")))
        if tag:
            print("       %-22s ^ %s" % ("", tag))
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
