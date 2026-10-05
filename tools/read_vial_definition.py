"""尝试用 Vial 协议从设备读取设备定义（里面含 amkFeature 与辅助屏尺寸）。

== 为什么这可能是突破口 ==
  官方 config.matrix-lab.com 是这样拿到设备定义的（protocol/keyboard_comm.py）：

      CMD_VIA_VIAL_PREFIX = 0xFE
      data = usb_send(dev, [0xFE, CMD_VIAL_GET_KEYBOARD_ID])   # -> <IQ: 协议版本, keyboard_id
      data = usb_send(dev, [0xFE, CMD_VIAL_GET_SIZE])          # -> <I: 压缩后长度
      while sz > 0:
          data = usb_send(dev, [0xFE, CMD_VIAL_GET_DEFINITION, block])   # 分块
      definition = json.loads(lzma.decompress(payload))

  这个 definition 里就含 "amkFeature" 列表，而 amkFeature 里的 aux_display
  条目决定了辅助屏的**确切宽高** —— 正是我一直在猜的那个值。

  早期笔记里写过"本固件不支持读定义（返回 08 07 …）"，但那是很早的探测结论，
  当时可能没带对 Vial 的前缀或参数。这里按官方实现精确复现一遍。

== 注意 ==
  1. 官方 usb_send 发的是 **不带 report id** 的短包（数据自身不超过 32 字节），
     由 hid_send 负责补零并前置 0x00。这里照做。
  2. 这是**只读**探测，不会改动设备任何状态。
  3. 若设备确实不支持，各命令会返回 0x55（不支持）或异常包，脚本会如实打印。

用法：
    python read_vial_definition.py
"""
import lzma
import json
import struct
import sys
import time

import hid

MSG_LEN = 32
VIA_VIAL_PREFIX = 0xFE
CMD_VIAL_GET_KEYBOARD_ID = 0x00
CMD_VIAL_GET_SIZE = 0x01
CMD_VIAL_GET_DEFINITION = 0x02
CMD_VIA_GET_PROTOCOL_VERSION = 0x01


def open_dev():
    for d in hid.enumerate(0x4D58, 0x5748):
        if d.get("usage_page") == 0xFF60 and d.get("usage") == 0x61:
            dev = hid.device()
            dev.open_path(d["path"])
            return dev
    return None


def usb_send(dev, msg, timeout_ms=1200):
    """复现官方 util.hid_send：不额外加前缀，只补零到 32 字节并前置 0x00。"""
    if len(msg) > MSG_LEN:
        raise ValueError("msg too long")
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

    print("=== 1) VIA 协议版本 (CMD 0x01) ===")
    r = usb_send(dev, [CMD_VIA_GET_PROTOCOL_VERSION])
    print("  返回:", r.hex(" ") if r else "(超时)")

    print()
    print("=== 2) Vial keyboard id (0xFE 0x00) ===")
    r = usb_send(dev, [VIA_VIAL_PREFIX, CMD_VIAL_GET_KEYBOARD_ID])
    print("  返回:", r.hex(" ") if r else "(超时)")
    if len(r) >= 12:
        try:
            proto, kb_id = struct.unpack("<IQ", r[0:12])
            print("  协议版本=%d  keyboard_id=%d (0x%X)" % (proto, kb_id, kb_id))
        except Exception as exc:
            print("  解析失败:", exc)

    print()
    print("=== 3) Vial 定义长度 (0xFE 0x01) ===")
    r = usb_send(dev, [VIA_VIAL_PREFIX, CMD_VIAL_GET_SIZE])
    print("  返回:", r.hex(" ") if r else "(超时)")
    size = None
    if len(r) >= 4:
        size = struct.unpack("<I", r[0:4])[0]
        print("  压缩后长度 = %d 字节" % size)
        if size == 0 or size > 1_000_000:
            print("  长度不合理，判定为不支持")
            size = None

    if size:
        print()
        print("=== 4) 分块取定义 (0xFE 0x02) ===")
        payload = b""
        block = 0
        remain = size
        while remain > 0:
            r = usb_send(dev, struct.pack("<BBI", VIA_VIAL_PREFIX,
                                          CMD_VIAL_GET_DEFINITION, block))
            if not r:
                print("  第 %d 块超时，中止" % block)
                break
            chunk = r[:MSG_LEN] if remain >= MSG_LEN else r[:remain]
            payload += chunk
            print("  块 %2d: %s" % (block, chunk.hex(" ")[:64]))
            block += 1
            remain -= MSG_LEN
            if block > 300:
                print("  块数过多，中止")
                break
        print("  共收 %d 字节（期望 %d）" % (len(payload), size))
        try:
            raw = lzma.decompress(payload)
            print("  lzma 解压成功: %d 字节" % len(raw))
            definition = json.loads(raw.decode("utf-8"))
            print("  JSON 解析成功，顶层键:", sorted(definition.keys())[:20])
            feats = definition.get("amkFeature")
            if feats:
                print()
                print("  *** amkFeature 内容（辅助屏权威参数）***")
                print("  " + json.dumps(feats, ensure_ascii=False, indent=2)[:1500])
            else:
                print("  没有 amkFeature 字段")
            # 顺便存下来
            import os
            p = os.path.join(os.environ.get("TEMP", "."), "nostation_definition.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(definition, fh, ensure_ascii=False, indent=1)
            print("  已保存到:", p)
        except Exception as exc:
            print("  解压/解析失败:", type(exc).__name__, exc)
    else:
        print()
        print("=== 结论 ===")
        print("  设备没有返回有效的 Vial 定义长度，说明本固件不支持读定义。")
        print("  （官网配置站应当是靠其它途径拿到 amkFeature 的，或者它也用默认值）")

    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
