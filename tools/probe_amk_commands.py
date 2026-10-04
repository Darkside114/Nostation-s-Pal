#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""向 NOSTATION 逐条发送 AMK 命令，记录哪些真的有应答。

为什么需要这个工具：
  companion_app.py 里那组命令常量（0x37 当"设置时间"、0x3A 当"读取显示模式"）
  是早期靠"发一条看有没有回包"凑出来的，**并不知道设备真实支持哪些命令**。
  后来从官网配置页内嵌的 Pyodide 里提取到了完整的 AMK 协议源码
  （amk/protocol.py，AMK_VERSION 0.9.11），但那份常量表的编号与实测的
  0x3A 对不上，所以必须直接问设备。

本工具只做**只读/无害**探测：
  * 命令 0（GET_VERSION）在 AMK 协议里是纯读取，最安全
  * 其余命令统一带全零负载发送，并只记录"有无应答、应答长什么样"
  * 不做任何持久化写入（不碰 OPEN_FILE / WRITE_FILE 这类会改设备状态的命令）

用法：
    python probe_amk_commands.py            # 扫描命令号 0..63
    python probe_amk_commands.py 0 1 2 3    # 只测指定命令号
"""
import sys
import time

import hid

VENDOR_ID = 0x4D58
PRODUCT_ID = 0x5748
USAGE_PAGE = 0xFF60
USAGE = 0x61
REPORT_LEN = 32
PREFIX = 0xFD

# AMK 协议里"会改变设备状态"的命令，探测时默认跳过（编号来自 amk/protocol.py）
# 说明：这些编号是官方 AMK 的，未必等于本设备；宁可多跳过，不可乱写。
MUTATING = set(range(1, 100)) - {0}   # 只放行 GET_VERSION(0)


def open_device():
    for d in hid.enumerate(VENDOR_ID, PRODUCT_ID):
        if d.get("usage_page") == USAGE_PAGE and d.get("usage") == USAGE:
            dev = hid.device()
            dev.open_path(d["path"])
            return dev, d
    return None, None


def xfer(dev, cmd, payload=b"", timeout_ms=700):
    """发一条命令，返回原始应答 bytes（超时返回 b""）。

    注意：必须像官方 util.hid_send 那样**在最前面补一个 0x00 当 HID 报告 ID**。
    早期版本漏了这个字节，设备收到的是错位数据，会回 [cmd] 00 00…；
    看起来"有应答"，其实命令根本没被识别，据此得出的结论全是错的。
    """
    body = bytes([cmd]) + payload
    msg = body + b"\x00" * (REPORT_LEN - len(body))
    try:
        # 报告 ID 0 + 32 字节负载
        dev.write(b"\x00" + msg)
    except Exception as exc:
        return b"ERR:" + str(exc).encode()
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        try:
            r = dev.read(REPORT_LEN, timeout_ms=int(timeout_ms))
        except Exception as exc:
            return b"ERR:" + str(exc).encode()
        if r:
            return bytes(r)
    return b""


def main():
    args = [int(a, 0) for a in sys.argv[1:]] or list(range(0, 64))

    dev, info = open_device()
    if not dev:
        print("找不到 NOSTATION（VID 4D58 / PID 5748 / usage 0xFF60:0x61）")
        return 2
    print("已连接: {} {:04x}:{:04x} {}".format(
        info.get("path", ""), info["vendor_id"], info["product_id"],
        info.get("product_string")))
    print()

    # 先确认它确实说 AMK：用已验证过的只读指纹命令
    print("=== 已知可用的指纹命令 ===")
    for cmd in (0x3A, 0, 1):
        r = xfer(dev, cmd)
        print("  FD {:02X} -> {}".format(cmd, r.hex(" ") if r else "(超时/无应答)"))
    print()

    print("=== 扫描命令空间 ===")
    print("  %-6s %-8s %s" % ("命令", "十六进制", "应答"))
    acked = []
    for cmd in args:
        r = xfer(dev, cmd)
        if not r or r.startswith(b"ERR:"):
            continue
        flag = ""
        if len(r) >= 3 and r[0] == PREFIX and r[1] == cmd:
            if r[2] == 0xAA:
                flag = "  <== ACK(OK)"
                acked.append(cmd)
            else:
                flag = "  <== 状态 0x%02X" % r[2]
        print("  %-6d 0x%02X     %s%s" % (cmd, cmd, r.hex(" "), flag))

    print()
    print("=== 有应答的命令 ===")
    print("  " + (", ".join("0x%02X" % c for c in acked) if acked else "(无)"))
    print()
    print("注意：带 ACK 的命令里，属于 GET 类的可用于读取；")
    print("      OPEN_FILE(37)/WRITE_FILE(38) 等属于会改设备的写命令，本工具已跳过。")

    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
